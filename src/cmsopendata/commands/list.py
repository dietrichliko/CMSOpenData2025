import logging

import click
from sqlalchemy import select
from sqlalchemy.orm import Session

from cmsopendata.models import Dataset, create_engine

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


@click.command(name="list")
@click.option("--limit", default=10, help="Limit the number of datasets displayed.")
@click.option(
    "--status",
    default=None,
    type=click.Choice(Dataset.state_config.states),
    help=f"Filter datasets by status. Valid values: {', '.join(Dataset.state_config.states)}.",
)
@click.pass_context
def command(ctx: click.Context, limit: int, status: str | None) -> None:
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
