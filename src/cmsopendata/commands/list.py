import logging
from typing import Any, cast

import click
from rucio.client import Client  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session

from cmsopendata.models import Dataset, create_engine

log = logging.getLogger("cmsopendata")


def human_readable_size(num_bytes: int) -> str:
    """Convert a number of bytes to a decimal human readable string.

    Returns values like '512 B', '1.2 KB', '3.4 MB'.
    """
    n = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1000.0 or unit == "PB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}"
        n /= 1000.0
    return f"{n:.1f} PB"


def check_rucio_subscription(subscription_id: str) -> dict[str, Any] | None:
    """Query Rucio for details about a subscription by its ID."""
    try:
        client = Client()
        for item in client.list_subscriptions():  # type: ignore[union-attr]
            sub = cast(dict[str, Any], item)
            if str(sub.get("id")) == str(subscription_id):
                return sub
        log.warning("No Rucio subscription found for id: %s", subscription_id)
        return None
    except Exception as e:
        log.error("Failed to query Rucio for subscription %s: %s", subscription_id, e)
        return None


@click.command(name="list")
@click.option("--limit", default=10, help="Limit the number of datasets displayed.")
@click.option(
    "--status",
    default=None,
    type=click.Choice(Dataset.state_config.states),
    help=f"Filter datasets by status. Valid values: {', '.join(Dataset.state_config.states)}.",
)
@click.option(
    "--check-rucio",
    is_flag=True,
    default=False,
    help="Query Rucio for subscription details for each listed dataset.",
)
@click.pass_context
def command(
    ctx: click.Context, limit: int, status: str | None, check_rucio: bool
) -> None:
    """Display the status of the transfers."""

    database_path = ctx.obj["database_path"]
    engine = create_engine(database_path)
    with Session(engine) as session:
        stmt = select(Dataset)
        if status is not None:
            stmt = stmt.where(Dataset.status == status)
        stmt = stmt.limit(limit)
        datasets = session.scalars(stmt).all()
        for dataset in datasets:
            log.info(
                "%d - %s - %s, [%s/%s/%s]",
                dataset.id,
                dataset.name,
                dataset.status,
                dataset.ok_cnt,
                dataset.replicating_cnt,
                dataset.stuck_cnt,
            )
            if check_rucio:
                subscription_id = getattr(dataset, "subscription_id", None)
                if subscription_id:
                    sub: dict[str, Any] | None = check_rucio_subscription(
                        subscription_id
                    )
                    if sub:
                        log.info(
                            "  Rucio subscription %s: state=%s, lifetime=%s, rules=%s",
                            subscription_id,
                            str(sub.get("state")),
                            str(sub.get("lifetime")),
                            str(sub.get("rules")),
                        )
                else:
                    log.info("  No Rucio subscription ID for dataset %d", dataset.id)
