import asyncio
import json
import logging
import pathlib
from itertools import islice

import click
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from cmsopendata.models import Base, Dataset, File, create_engine

log = logging.getLogger("cmsopendata")


@click.command(name="create")
@click.option(
    "-d",
    "--datasets",
    required=True,
    type=click.Path(dir_okay=False, exists=True, path_type=pathlib.Path),
    help="Contains a list of datasets to load",
)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output")
@click.option("-l", "--limit", type=int, default=0, help="Limit the number of dataset")
@click.option("-j", "--jobs", type=int, default=10, help="Number of parallel jobs")
@click.option("-f", "--force", is_flag=True, help="Force re-initialization")
@click.pass_context
def command(
    ctx: click.Context,
    datasets: pathlib.Path,
    verbose: bool,
    limit: int,
    jobs: int,
    force: bool,
) -> None:
    """Initialize the CMS Open Data environment."""

    database_path = ctx.obj["database_path"]
    asyncio.run(init_command(datasets, verbose, limit, jobs, database_path, force))


async def init_command(
    datasets: pathlib.Path,
    verbose: bool,
    limit: int,
    jobs: int,
    database_path: pathlib.Path,
    force: bool,
) -> None:
    log.info("Initializing CMS Open Data environment...")

    if database_path.exists():
        if force:
            log.info(
                "Removing existing database at %s due to --force flag.", database_path
            )
            database_path.unlink()
        else:
            log.fatal("Database already exists at %s.", database_path)
            return

    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.fatal("Failed to create directory for database: %s", e)
        return

    engine = create_engine(database_path)

    Base.metadata.create_all(engine)

    sem = asyncio.Semaphore(jobs)

    with open(datasets, "r", encoding="utf-8") as f:
        lines = filter(None, (line.strip() for line in f))
        datasets_list = list(islice(lines, limit)) if limit > 0 else list(lines)

        async with asyncio.TaskGroup() as tg:
            for dataset in datasets_list:
                if dataset:
                    tg.create_task(load_dataset(dataset, verbose, sem, engine))

    log.info("Initialization complete.")


async def load_dataset(
    dataset: str,
    verbose: bool,
    sem: asyncio.Semaphore,
    engine: Engine,
) -> None:
    if verbose:
        log.info(f"Loading dataset: {dataset}")

    async with sem:
        proc = await asyncio.create_subprocess_exec(
            "dasgoclient",
            "-json",
            "-query",
            f"file dataset={dataset}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            log.warning(
                "dasgoclient failed for %s: %s",
                dataset,
                (stderr.decode().strip() or "no stderr"),
            )
            return

    try:
        info = json.loads(stdout.decode() or "null")
    except json.JSONDecodeError:
        log.warning("Failed to parse JSON for %s", dataset)
        if verbose:
            log.debug(stdout.decode(errors="replace"))
        return

    with Session(engine) as session:
        dataset_obj = Dataset(name=dataset)
        session.add(dataset_obj)

        for entry in info:
            for file_entry in entry["file"]:
                file_obj = File(
                    path=file_entry["name"],
                    size=int(file_entry["size"]),
                    checksum=int(file_entry["adler32"], 16),
                    entries=int(file_entry["nevents"]),
                    dataset=dataset_obj,
                )
                session.add(file_obj)
        session.commit()

    if verbose:
        log.info(f"Finished loading dataset: {dataset}")
