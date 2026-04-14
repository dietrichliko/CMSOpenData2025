import logging
import os
import pathlib
import subprocess

import click
from rich.logging import RichHandler

from cmsopendata.commands import register_commands

DEFAULT_DB_PATH = f"/afs/cern.ch/work/{os.environ['USER'][0]}/{os.environ['USER']}/cmsopendata2025.sqlite"

handler = RichHandler(
    rich_tracebacks=True,
    tracebacks_show_locals=True,
    show_time=True,
    show_level=True,
    show_path=False,
    omit_repeated_times=True,
)

logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[handler])
log = logging.getLogger("cmsopendata")
logging.getLogger('transitions').setLevel(logging.WARNING)

@click.group()
@click.option(
    "--db-path",
    type=click.Path(dir_okay=False, path_type=pathlib.Path),
    default=DEFAULT_DB_PATH,
    help="Path to the SQLite database file",
)
@click.option(
    "--log-level",
    type=click.Choice(
        ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False
    ),
    default="INFO",
    help="Logging level",
)
@click.pass_context
def main(ctx: click.Context, db_path: pathlib.Path, log_level: str) -> None:
    """CMS Open Data Command Line Interface"""

    ctx.ensure_object(dict)
    ctx.obj["database_path"] = db_path

    # configure logging level from CLI option
    level = getattr(logging, log_level.upper(), logging.INFO)
    log.setLevel(level)

    # checck if the grid proxy is still valid for 1 hour
    try:
        proc = subprocess.run(
            ["voms-proxy-info", "-timeleft"], capture_output=True, text=True
        )
    except FileNotFoundError:
        log.warning("voms-proxy-info not found; cannot verify grid proxy lifetime.")
    else:
        if proc.returncode != 0:
            log.warning(
                "voms-proxy-info failed: %s", (proc.stderr or proc.stdout).strip()
            )
            raise click.Abort()
        else:
            try:
                timeleft = int(proc.stdout.strip())
            except ValueError:
                log.warning(
                    "Unexpected output from voms-proxy-info: %r", proc.stdout.strip()
                )
                raise click.Abort()
            else:
                if timeleft < 3600:
                    log.error(
                        "Grid proxy expires in %d seconds (<1 hour). Please renew with voms-proxy-init.",
                        timeleft,
                    )
                    raise click.Abort()
                else:
                    log.info("Grid proxy valid for %d seconds.", timeleft)


register_commands(main)
