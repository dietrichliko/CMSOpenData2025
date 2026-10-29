import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

import click
from sqlalchemy import select
from sqlalchemy.orm import Session

from cmsopendata.models import FAILED, TRANSFERRED, Dataset, File, create_engine

log = logging.getLogger("cmsopendata")

DEFAULT_PREFIX = "root://eospublic.cern.ch//eos/opendata/cms/upload"


def check_file(file: File, prefix: str) -> tuple[File, bool]:
    """Run xrdadler32 for a single file. Returns (file, ok)."""
    path = file.path.removeprefix("/store")
    url = prefix + path
    try:
        result = subprocess.run(
            ["xrdadler32", url],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            log.error("FAIL (xrdadler32 error): %s — %s", url, result.stderr.strip())
            return file, False
        actual = result.stdout.strip().split()[0]
        if int(actual, 16) != int(file.checksum):
            log.error(
                "FAIL (checksum mismatch): %s — expected %s, got %s",
                url,
                file.checksum,
                actual,
            )
            return file, False
        log.debug("OK: %s (%s)", url, actual)
        return file, True
    except subprocess.TimeoutExpired:
        log.error("FAIL (timeout): %s", url)
        return file, False
    except FileNotFoundError:
        log.error("xrdadler32 not found in PATH")
        return file, False


@click.command(name="verify")
@click.option(
    "-d",
    "--dataset-pattern",
    required=False,
    type=str,
    help="Dataset name pattern to verify (glob)",
    default="",
)
@click.option(
    "--prefix",
    required=False,
    type=str,
    default=DEFAULT_PREFIX,
    show_default=True,
    help="XRootD URL prefix prepended to each file path",
)
@click.option(
    "-j",
    "--jobs",
    required=False,
    type=int,
    default=4,
    show_default=True,
    help="Number of parallel xrdadler32 commands",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="List files to verify without actually running xrdadler32",
)
@click.option(
    "--no-update",
    is_flag=True,
    help="Run verification but do not update the database",
)
@click.option(
    "--force",
    is_flag=True,
    help="Verify datasets regardless of their status (useful when files have been moved to a new location).",
)
@click.option(
    "--include-failed",
    is_flag=True,
    help="Also verify datasets with FAILED status.",
)
@click.option(
    "--failed-files",
    required=False,
    type=click.Path(writable=True),
    default=None,
    help="Write LFNs of failed files to this text file.",
)
@click.option(
    "--failed-datasets",
    required=False,
    type=click.Path(writable=True),
    default=None,
    help="Write summary of failed datasets to this text file.",
)
@click.pass_context
def command(
    ctx: click.Context,
    dataset_pattern: str,
    prefix: str,
    jobs: int,
    dry_run: bool,
    no_update: bool,
    force: bool,
    include_failed: bool,
    failed_files: str | None,
    failed_datasets: str | None,
) -> None:
    """Verify transferred datasets by checking file checksums at their new location."""

    database_path = ctx.obj["database_path"]
    engine = create_engine(database_path)

    with Session(engine) as session:
        stmt = select(Dataset)
        if not force:
            if include_failed:
                stmt = stmt.filter(Dataset.status.in_([TRANSFERRED, FAILED]))
            else:
                stmt = stmt.filter(Dataset.status == TRANSFERRED)
        if dataset_pattern:
            stmt = stmt.filter(Dataset.name.op("GLOB")(dataset_pattern))

        datasets = list(session.scalars(stmt))
        if not datasets:
            log.info("No datasets found to verify.")
            return

        if force:
            log.info(
                "Found %d dataset(s) to verify (--force, ignoring status).",
                len(datasets),
            )
            if not no_update:
                log.info(
                    "--force implies --no-update: database status will not be changed."
                )
                no_update = True
        else:
            log.info("Found %d transferred dataset(s) to verify.", len(datasets))

        failed_dataset_list: list[tuple[str, int, int]] = []

        for dataset in datasets:
            log.info(
                "Verifying dataset %s (%d files)...", dataset.name, len(dataset.files)
            )

            if dry_run:
                for file in dataset.files:
                    path = file.path.removeprefix("/store")
                    log.info(
                        "[dry-run] Would check: %s%s (expected: %s)",
                        prefix,
                        path,
                        file.checksum,
                    )
                continue

            all_ok = True
            ok_count = 0
            failed_lfns: list[str] = []
            with ThreadPoolExecutor(max_workers=jobs) as executor:
                futures = {
                    executor.submit(check_file, file, prefix): file
                    for file in dataset.files
                }
                for future in as_completed(futures):
                    file, ok = future.result()
                    if ok:
                        ok_count += 1
                    else:
                        all_ok = False
                        failed_lfns.append(file.path)

            total_count = len(dataset.files)
            if all_ok:
                log.info(
                    "Dataset %s verified successfully (%d/%d files), marking as DONE.",
                    dataset.name,
                    ok_count,
                    total_count,
                )
                if not no_update:
                    dataset.set_done()  # type: ignore
            else:
                log.error(
                    "Dataset %s has checksum failures (%d/%d files ok), marking as FAILED.",
                    dataset.name,
                    ok_count,
                    total_count,
                )
                failed_dataset_list.append((dataset.name, total_count, ok_count))
                if not no_update:
                    dataset.set_failed()  # type: ignore

            if failed_lfns and failed_files:
                with open(failed_files, "a") as fh:
                    for lfn in failed_lfns:
                        fh.write(lfn + "\n")

            if not all_ok and failed_datasets:
                with open(failed_datasets, "a") as fh:
                    fh.write(f"[{ok_count}/{total_count}] {dataset.name}\n")

            if not no_update:
                session.commit()

        if failed_dataset_list:
            log.error(
                "Summary: %d dataset(s) failed verification:", len(failed_dataset_list)
            )
            for name, total, ok in failed_dataset_list:
                log.error("  FAILED: %s (%d/%d files ok)", name, ok, total)
        else:
            log.info("Summary: all %d dataset(s) verified successfully.", len(datasets))
