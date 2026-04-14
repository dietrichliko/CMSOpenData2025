import logging

import click
import rucio.client
import rucio.common.exception
from sqlalchemy import select
from sqlalchemy.orm import Session

from cmsopendata.models import Dataset, create_engine

log = logging.getLogger("cmsopendata")


@click.command(name="inspect")
@click.option("--limit", default=10, help="Limit the number of datasets inspected.")
@click.argument("dataset_ids", nargs=-1, type=int)
@click.pass_context
def command(ctx: click.Context, limit: int, dataset_ids: tuple[int, ...]) -> None:
    """Inspect stuck datasets and their file locks via Rucio."""

    database_path = ctx.obj["database_path"]
    engine = create_engine(database_path)
    client = rucio.client.Client()

    with Session(engine) as session:
        stmt = select(Dataset).where(Dataset.stuck_cnt > 0)
        if dataset_ids:
            stmt = stmt.where(Dataset.id.in_(dataset_ids))
        stmt = stmt.limit(limit)

        datasets = session.scalars(stmt).all()
        if not datasets:
            log.info("No stuck datasets found.")
            return

        for dataset in datasets:
            log.info(
                "Dataset %d - %s [ok=%s, replicating=%s, stuck=%s]",
                dataset.id,
                dataset.name,
                dataset.ok_cnt,
                dataset.replicating_cnt,
                dataset.stuck_cnt,
            )

            if not dataset.subscription:
                log.warning("  No subscription (rule ID) recorded for this dataset.")
                continue

            try:
                rule = client.get_replication_rule(dataset.subscription)
                log.info("  Rule state : %s", rule["state"])
                log.info("  Rule error : %s", rule.get("error") or "none")
                log.info("  Rule RSE   : %s", rule["rse_expression"])
                log.info("  Expires    : %s", rule.get("expires_at") or "never")
            except rucio.common.exception.RucioException:
                log.exception("  Failed to get rule %s", dataset.subscription)
                continue

            log.info("  Stuck file locks:")
            try:
                stuck_count = 0
                for lock in client.list_replica_locks(dataset.subscription):
                    if lock["state"] == "S":  # S = Stuck
                        log.info(
                            "    [STUCK] %s  error: %s",
                            lock["name"],
                            lock.get("error") or "unknown",
                        )
                        stuck_count += 1
                if stuck_count == 0:
                    log.info("    No stuck locks found (may have recovered).")
            except rucio.common.exception.RucioException:
                log.exception(
                    "  Failed to list locks for rule %s", dataset.subscription
                )
