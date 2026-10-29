import logging

import click
import rucio.client  # type: ignore
import rucio.common.exception  # type: ignore
from sqlalchemy import select
from sqlalchemy.orm import Session

from cmsopendata.models import DONE, Dataset, create_engine

log = logging.getLogger("cmsopendata")


@click.command(name="release")
@click.option(
    "-d",
    "--dataset-pattern",
    required=False,
    type=str,
    default="",
    help="Glob pattern to select datasets by name.",
)
@click.argument("dataset_ids", nargs=-1, type=int)
@click.option(
    "--delete-rule",
    is_flag=True,
    help="Also delete the Rucio replication rule for the released datasets.",
)
@click.pass_context
def command(
    ctx: click.Context,
    dataset_pattern: str,
    dataset_ids: tuple[int, ...],
    delete_rule: bool,
) -> None:
    """Mark DONE datasets as RELEASED.

    Use this once an external actor has copied a verified dataset elsewhere
    and removed it from the RSE, freeing its size quota for `subscribe`.
    """

    database_path = ctx.obj["database_path"]
    engine = create_engine(database_path)
    with Session(engine) as session:
        stmt = select(Dataset).where(Dataset.status == DONE)
        if dataset_ids:
            stmt = stmt.where(Dataset.id.in_(dataset_ids))
        if dataset_pattern:
            stmt = stmt.where(Dataset.name.op("GLOB")(dataset_pattern))

        datasets = list(session.scalars(stmt))
        if not datasets:
            log.info("No DONE datasets found to release.")
            return

        client = rucio.client.Client() if delete_rule else None
        released = 0
        for dataset in datasets:
            if delete_rule and dataset.subscription:
                try:
                    client.delete_replication_rule(dataset.subscription)  # type: ignore[union-attr]
                    log.info(
                        "Deleted replication rule %s for %s",
                        dataset.subscription,
                        dataset.name,
                    )
                except rucio.common.exception.RucioException:
                    log.exception(
                        "Failed to delete replication rule %s for %s — skipping release",
                        dataset.subscription,
                        dataset.name,
                    )
                    continue
            dataset.set_released()  # type: ignore
            log.info("Released %s", dataset.name)
            released += 1
            session.commit()

        log.info("Released %d/%d dataset(s).", released, len(datasets))
