import logging

import click
import rucio  # type: ignore
import rucio.client  # type: ignore
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cmsopendata.models import SUBSCRIBED, Dataset, File, create_engine

log = logging.getLogger("cmsopendata")


def human_readable_size(num_bytes: int) -> str:
    """Convert a number of bytes to a human readable string.

    Uses 1024-based units and returns values like '512 B', '1.2 KB', '3.4 MB'.
    """
    n = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024.0 or unit == "PB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}"
        n /= 1024.0
    # Fallback (shouldn't be reached because of the loop above)
    return f"{n:.1f} PB"


@click.command(name="status")
@click.option("--update", is_flag=True, help="Update the status of the transfers.")
@click.pass_context
def command(ctx: click.Context, update: bool) -> None:
    """Display the status of the transfers."""

    #   client = rucio.client.Client()

    database_path = ctx.obj["database_path"]
    engine = create_engine(database_path)
    with Session(engine) as session:
        if update:
            client = rucio.client.Client()
            log.info("Updating transfer statuses...")
            stmt = select(Dataset).where(Dataset.status == SUBSCRIBED)
            for dataset in session.scalars(stmt):
                log.info("Updating dataset: %s", dataset.id)
                response = client.get_replication_rule(dataset.subscription)
                if response["state"] == "OK":
                    dataset.set_transferred()  # type: ignore
                dataset.ok_cnt = response.get("locks_ok_cnt", 0)
                dataset.replicating_cnt = response.get("locks_replicating_cnt", 0)
                dataset.stuck_cnt = response.get("locks_stuck_cnt", 0)
                session.commit()
                # Here you would add the logic to update the dataset status
        # count datasets using Core (no ORM object loading)
        total_datasets_count = session.scalar(
            select(func.count()).select_from(Dataset.__table__)
        )
        log.info("Total datasets (SQL count): %d", total_datasets_count)
        # Get the set of used states from the Dataset table
        used_states = session.scalars(
            select(Dataset.__table__.c.status).distinct()
        ).all()
        if not used_states:
            log.info("No dataset statuses found")
        else:
            for state in used_states:
                count = session.scalar(
                    select(func.count())
                    .select_from(Dataset.__table__)
                    .where(Dataset.__table__.c.status == state)
                )
                state_label = state if state is not None else "NULL"
                log.info("Datasets with status '%s': %d", state_label, int(count or 0))
        # count datasets using Core (no ORM object loading)
        total_files_count = session.scalar(
            select(func.count()).select_from(File.__table__)
        )
        log.info("Total files (SQL count): %d", total_files_count)
        # Sum the ok/replicating/stuck counters across all datasets (gives number of files)
        total_ok_files = session.scalar(
            select(func.coalesce(func.sum(Dataset.__table__.c.ok_cnt), 0))
        )
        total_replicating_files = session.scalar(
            select(func.coalesce(func.sum(Dataset.__table__.c.replicating_cnt), 0))
        )
        total_stuck_files = session.scalar(
            select(func.coalesce(func.sum(Dataset.__table__.c.stuck_cnt), 0))
        )

        log.info(
            "Total files with status OK (sum of ok_cnt): %d", int(total_ok_files or 0)
        )
        log.info(
            "Total files replicating (sum of replicating_cnt): %d",
            int(total_replicating_files or 0),
        )
        log.info(
            "Total stuck files (sum of stuck_cnt): %d", int(total_stuck_files or 0)
        )
        # count datasets with non-zero ok/replicating/stuck counters (no ORM loading)
        ok_datasets_count = session.scalar(
            select(func.count())
            .select_from(Dataset.__table__)
            .where(Dataset.__table__.c.ok_cnt > 0)
        )
        replicating_datasets_count = session.scalar(
            select(func.count())
            .select_from(Dataset.__table__)
            .where(Dataset.__table__.c.replicating_cnt > 0)
        )
        stuck_datasets_count = session.scalar(
            select(func.count())
            .select_from(Dataset.__table__)
            .where(Dataset.__table__.c.stuck_cnt > 0)
        )

        log.info("Datasets with ok_cnt > 0: %d", int(ok_datasets_count or 0))
        log.info(
            "Datasets with replicating_cnt > 0: %d",
            int(replicating_datasets_count or 0),
        )
        log.info("Datasets with stuck_cnt > 0: %d", int(stuck_datasets_count or 0))

        # Compute the sum entirely in SQL using the table column to avoid
        # loading File ORM objects into memory.
        total_size = session.scalar(
            select(func.coalesce(func.sum(File.__table__.c.size), 0))
        )
    # total_size will be an int (0 when there are no files)
    total_bytes = int(total_size or 0)
    readable = human_readable_size(total_bytes)
    log.info("Total size (bytes): %d (%s)", total_bytes, readable)
