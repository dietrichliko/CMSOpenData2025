import logging

import click
import rucio.client  # type: ignore

log = logging.getLogger("cmsopendata")


@click.command(name="whoami")
@click.pass_context
def command(ctx: click.Context) -> None:
    """Display current RUCIO user identity."""

    client = rucio.client.Client()
    response = client.whoami()
    log.info("Rucio account: %s", response["account"])
