import logging
import time
from datetime import datetime, timezone

import click
import rucio  # type: ignore
import rucio.client  # type: ignore
import rucio.common.exception  # type: ignore
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cmsopendata.models import (
    FAILED,
    INITIALIZED,
    OCCUPYING_STATUSES,
    SUBSCRIBED,
    Dataset,
    File,
    create_engine,
)

log = logging.getLogger("cmsopendata")

DEFAULT_RSE = "T3_CH_CERN_OpenData_Stage"
DEFAULT_MAX_SIZE = "200TB"

_SIZE_UNITS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "PB": 1000**5,
}


def parse_size(value: str) -> int:
    """Parse a human readable size string (e.g. '200TB', '512GB') into bytes."""
    text = value.strip().upper()
    for unit in sorted(_SIZE_UNITS, key=len, reverse=True):
        if text.endswith(unit):
            number = text[: -len(unit)].strip()
            try:
                return int(float(number) * _SIZE_UNITS[unit])
            except ValueError:
                break
    try:
        return int(text)
    except ValueError as exc:
        raise click.BadParameter(
            f"Invalid size {value!r}. Use e.g. '200TB', '512GB' or a plain byte count."
        ) from exc


def human_readable_size(num_bytes: int) -> str:
    """Convert a number of bytes to a human readable string."""
    n = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1000.0 or unit == "PB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}"
        n /= 1000.0
    return f"{n:.1f} PB"


def occupied_size(session: Session) -> int:
    """Total size of datasets that still occupy space on the target RSE."""
    dataset_size = (
        select(File.dataset_id.label("dataset_id"), func.sum(File.size).label("size"))
        .group_by(File.dataset_id)
        .subquery()
    )
    total = session.scalar(
        select(func.coalesce(func.sum(dataset_size.c.size), 0))
        .select_from(Dataset)
        .join(dataset_size, dataset_size.c.dataset_id == Dataset.id)
        .where(Dataset.status.in_(OCCUPYING_STATUSES))
    )
    return int(total or 0)


def candidates_with_size(
    session: Session, dataset_pattern: str, force: bool
) -> list[tuple[Dataset, int]]:
    """Datasets eligible for subscription, together with their total size in bytes."""
    dataset_size = (
        select(File.dataset_id.label("dataset_id"), func.sum(File.size).label("size"))
        .group_by(File.dataset_id)
        .subquery()
    )
    filters = []
    if dataset_pattern:
        filters.append(Dataset.name.op("GLOB")(dataset_pattern))
    if force:
        filters.append(
            (Dataset.status == INITIALIZED)
            | (Dataset.status == FAILED)
            | (Dataset.status == SUBSCRIBED)
        )
    else:
        filters.append(Dataset.status == INITIALIZED)

    stmt = (
        select(Dataset, func.coalesce(dataset_size.c.size, 0))
        .join(dataset_size, dataset_size.c.dataset_id == Dataset.id, isouter=True)
        .filter(*filters)
        .order_by(Dataset.id)
    )
    return [(dataset, int(size or 0)) for dataset, size in session.execute(stmt).all()]


def refresh_subscribed_statuses(session: Session, client: rucio.client.Client) -> None:
    """Poll Rucio for datasets still SUBSCRIBED and advance their status."""
    stmt = select(Dataset).where(Dataset.status == SUBSCRIBED)
    for dataset in session.scalars(stmt):
        try:
            response = client.get_replication_rule(dataset.subscription)
        except rucio.common.exception.RucioException:
            log.exception("Failed to query rule for %s", dataset.name)
            continue
        state = response.get("state")
        if state == "OK":
            dataset.set_transferred()  # type: ignore
        elif state == "STUCK":
            dataset.set_stuck()  # type: ignore
        dataset.ok_cnt = response.get("locks_ok_cnt", 0)
        dataset.replicating_cnt = response.get("locks_replicating_cnt", 0)
        dataset.stuck_cnt = response.get("locks_stuck_cnt", 0)
        session.commit()


def find_oversized_datasets(
    session: Session, max_size_bytes: int
) -> list[tuple[Dataset, int]]:
    """Datasets whose size alone exceeds the budget.

    Such datasets could never be subscribed within --max-size, meaning
    splitting at dataset granularity would not be sufficient for them.
    """
    dataset_size = (
        select(File.dataset_id.label("dataset_id"), func.sum(File.size).label("size"))
        .group_by(File.dataset_id)
        .subquery()
    )
    stmt = (
        select(Dataset, dataset_size.c.size)
        .join(dataset_size, dataset_size.c.dataset_id == Dataset.id)
        .where(dataset_size.c.size > max_size_bytes)
        .order_by(dataset_size.c.size.desc())
    )
    return [(dataset, int(size)) for dataset, size in session.execute(stmt).all()]


@click.command(name="subscribe")
@click.option(
    "-d",
    "--dataset-pattern",
    required=False,
    type=str,
    help="Dataset pattern to subscribe to",
    default="",
)
@click.option(
    "--rse",
    required=True,
    type=str,
    help="Site to subscribe the dataset to",
    show_default=True,
    default=DEFAULT_RSE,
)
@click.option(
    "-f", "--force", is_flag=True, help="Force subscription also for failed datasets"
)
@click.option(
    "--max-size",
    type=str,
    default=DEFAULT_MAX_SIZE,
    show_default=True,
    help="Maximum total size of datasets occupying space on the RSE "
    "(subscribed, transferred, stuck or done but not yet released), e.g. '200TB'.",
)
@click.option(
    "--watch",
    is_flag=True,
    help="Keep polling Rucio and subscribe more datasets as budget frees up "
    "(e.g. via `cmstool release`), until all matching datasets are subscribed.",
)
@click.option(
    "--poll-interval",
    type=int,
    default=300,
    show_default=True,
    help="Seconds to wait between polls when --watch is set.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show which datasets would be subscribed without contacting Rucio or changing the database.",
)
@click.pass_context
def command(
    ctx: click.Context,
    dataset_pattern: str,
    rse: str,
    force: bool,
    max_size: str,
    watch: bool,
    poll_interval: int,
    dry_run: bool,
) -> None:
    """Subscribe datasets to a site, staying within a maximum size budget."""

    max_size_bytes = parse_size(max_size)
    if dry_run and watch:
        raise click.BadParameter("--watch cannot be combined with --dry-run")
    database_path = ctx.obj["database_path"]
    engine = create_engine(database_path)
    client = None
    if not dry_run:
        client = rucio.client.Client()
        response = client.whoami()
        log.debug("Rucio account: %s", response["account"])

    with Session(engine) as session:
        oversized = find_oversized_datasets(session, max_size_bytes)
        if oversized:
            log.warning(
                "%d dataset(s) exceed the max-size budget of %s on their own; "
                "splitting at dataset granularity is not enough for them "
                "(they can only be subscribed once --max-size is increased):",
                len(oversized),
                human_readable_size(max_size_bytes),
            )
            for dataset, size in oversized:
                log.warning("  %s: %s", dataset.name, human_readable_size(size))

        log.info(
            "%sSubscribing datasets to RSE '%s' (max size budget: %s)...",
            "[dry-run] " if dry_run else "",
            rse,
            human_readable_size(max_size_bytes),
        )
        while True:
            occupied = occupied_size(session)
            budget = max_size_bytes - occupied
            candidates = candidates_with_size(session, dataset_pattern, force)
            if not candidates:
                log.info("No more datasets to subscribe.")
                break

            log.info(
                "Occupied size: %s, remaining budget: %s, candidates: %d",
                human_readable_size(occupied),
                human_readable_size(max(budget, 0)),
                len(candidates),
            )

            skipped = 0
            for dataset, size in candidates:
                if size > budget:
                    log.debug(
                        "Skipping %s (%s) — exceeds remaining budget",
                        dataset.name,
                        human_readable_size(size),
                    )
                    skipped += 1
                    continue
                if dry_run:
                    log.info(
                        "[dry-run] Would subscribe %s (%s) with status %s",
                        dataset.name,
                        human_readable_size(size),
                        dataset.status,
                    )
                else:
                    _subscribe_dataset(session, client, dataset, rse)
                budget -= size

            if dry_run or not watch:
                break
            if skipped == 0:
                # everything that matched fit within budget this round; nothing
                # left to wait for (re-querying could re-match already
                # subscribed datasets when --force is set).
                break

            log.info(
                "%d dataset(s) waiting for budget; polling Rucio for transfer completion...",
                skipped,
            )
            refresh_subscribed_statuses(session, client)
            time.sleep(poll_interval)


def _subscribe_dataset(
    session: Session, client: rucio.client.Client, dataset: Dataset, rse: str
) -> None:
    log.info("Processing dataset %s with status %s", dataset.name, dataset.status)
    dids = [{"scope": "cms", "name": dataset.name}]
    log.debug("DIDs to subscribe: %s", dids)
    if (dataset.status == FAILED) | (dataset.status == SUBSCRIBED):
        if dataset.status == FAILED:
            log.info("Clearing failed status for %s", dataset.name)
            dataset.clear()  # type: ignore
        elif dataset.status == SUBSCRIBED:
            log.info("Resetting status for %s", dataset.name)
            dataset.reset()  # type: ignore
        if dataset.subscription:
            # Delete existing rule before creating a new one
            try:
                client.delete_replication_rule(dataset.subscription)
                log.info(
                    "Deleted existing replication rule %s for %s",
                    dataset.subscription,
                    dataset.name,
                )
            except rucio.common.exception.AccessDenied:
                log.warning(
                    "Cannot delete rule %s for %s — not owned by this account, skipping delete",
                    dataset.subscription,
                    dataset.name,
                )
            except rucio.common.exception.RucioException:
                log.exception(
                    "Error deleting existing replication rule %s for %s",
                    dataset.subscription,
                    dataset.name,
                )
    try:
        log.debug("Calling add_replication_rule for %s to RSE %s", dataset.name, rse)
        response = client.add_replication_rule(
            dids=dids,
            copies=1,
            rse_expression=rse,
            lifetime=None,  # no expiry
        )
        log.debug("add_replication_rule response: %s", response)
        log.info("Subscribed %s with rule ID %s", dataset.name, response[0])
        dataset.subscription = response[0]
        dataset.set_subscribed()  # type: ignore
    except rucio.common.exception.DuplicateRule:
        log.debug("DuplicateRule for %s, querying existing rules", dataset.name)
        rules = list(
            client.list_replication_rules(
                {
                    "scope": "cms",
                    "name": dataset.name,
                    "rse_expression": rse,
                }
            )
        )
        log.debug("Existing rules for %s: %s", dataset.name, rules)
        if rules:
            rule = rules[0]
            rule_account = rule.get("account")
            my_account = client.whoami()["account"]
            if rule_account != my_account:
                log.warning(
                    "Rule %s for %s is owned by account '%s', not '%s' — cannot delete",
                    rule["id"],
                    dataset.name,
                    rule_account,
                    my_account,
                )

            expires_at = rule.get("expires_at")
            if expires_at is not None:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                now = datetime.now(tz=timezone.utc)
                if expires_at < now:
                    log.warning(
                        "Rule %s for %s has EXPIRED at %s (owned by '%s') — %s",
                        rule["id"],
                        dataset.name,
                        expires_at,
                        rule_account,
                        "cannot delete, owned by different account"
                        if rule_account != my_account
                        else "deleting and recreating",
                    )
                    if rule_account == my_account:
                        try:
                            client.delete_replication_rule(rule["id"])
                            log.info("Deleted expired rule %s", rule["id"])
                            response = client.add_replication_rule(
                                dids=dids,
                                copies=1,
                                rse_expression=rse,
                                lifetime=None,
                            )
                            log.debug("add_replication_rule response: %s", response)
                            log.info(
                                "Recreated rule for %s with new rule ID %s",
                                dataset.name,
                                response[0],
                            )
                            dataset.subscription = response[0]
                            dataset.set_subscribed()  # type: ignore
                        except rucio.common.exception.RucioException:
                            log.exception(
                                "Failed to recreate rule for %s", dataset.name
                            )
                            dataset.set_failed()  # type: ignore
                    else:
                        log.error(
                            "Expired rule %s is owned by '%s' — manual intervention required",
                            rule["id"],
                            rule_account,
                        )
                        dataset.set_failed()  # type: ignore
                    session.commit()
                    return
            log.info("Already subscribed %s", dataset.name)
            if dataset.status == "failed":
                log.info("Clearing failed status for %s", dataset.name)
                dataset.clear_failed()  # type: ignore
            dataset.subscription = rule["id"]
            dataset.ok_cnt = rule["locks_ok_cnt"]
            dataset.replicating_cnt = rule["locks_replicating_cnt"]
            dataset.stuck_cnt = rule["locks_stuck_cnt"]
            dataset.set_subscribed()  # type: ignore
        else:
            log.error(
                "DuplicateRule exception raised but no existing rule found for %s",
                dataset.name,
            )
            dataset.set_failed()  # type: ignore

    except rucio.common.exception.RucioException:
        log.exception("Error creating replication rule for %s", dataset.name)
        dataset.set_failed()  # type: ignore

    session.commit()
