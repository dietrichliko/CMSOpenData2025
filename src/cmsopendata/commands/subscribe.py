import logging

import click
import rucio  # type: ignore
import rucio.client  # type: ignore
import rucio.common.exception  # type: ignore
from sqlalchemy import select
from sqlalchemy.orm import Session

from cmsopendata.models import FAILED, INITIALIZED, SUBSCRIBED, Dataset, create_engine

log = logging.getLogger("cmsopendata")

DEFAULT_RSE = "T3_CH_CERN_OpenData_Stage"


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
@click.pass_context
def command(ctx: click.Context, dataset_pattern: str, rse: str, force: bool) -> None:
    """Subscribe a dataset to a site."""

    database_path = ctx.obj["database_path"]
    engine = create_engine(database_path)
    with Session(engine) as session:
        if dataset_pattern:
            log.info(
                "Subscribing datasets matching pattern '%s' to RSE '%s'...",
                dataset_pattern,
                rse,
            )
            if force:
                stmt = select(Dataset).filter(
                    Dataset.name.op("GLOB")(dataset_pattern),
                    (Dataset.status == INITIALIZED)
                    | (Dataset.status == FAILED)
                    | (Dataset.status == SUBSCRIBED),
                )
            else:
                stmt = select(Dataset).filter(
                    Dataset.name.op("GLOB")(dataset_pattern),
                    Dataset.status == INITIALIZED,
                )
        else:
            log.info("Subscribing all datasets to RSE '%s'...", rse)
            if force:
                stmt = select(Dataset).filter(
                    (Dataset.status == INITIALIZED)
                    | (Dataset.status == FAILED)
                    | (Dataset.status == SUBSCRIBED),
                )
            else:
                stmt = select(Dataset).filter(Dataset.status == INITIALIZED)

        client = rucio.client.Client()
        # response = client.whoami()
        # log.info("Rucio account: %s", response["account"])
        for dataset in session.scalars(stmt):
            log.info(
                "Processing dataset %s with status %s", dataset.name, dataset.status
            )
            dids = [{"scope": "cms", "name": dataset.name}]
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
                    except rucio.common.exception.RucioException:
                        log.exception(
                            "Error deleting existing replication rule %s for %s",
                            dataset.subscription,
                            dataset.name,
                        )
            try:
                response = client.add_replication_rule(
                    dids=dids,
                    copies=1,
                    rse_expression=rse,
                )
                log.info("Subscribed %s with rule ID %s", dataset.name, response[0])
                dataset.subscription = response[0]

                dataset.set_subscribed()  # type: ignore
            except rucio.common.exception.DuplicateRule:
                rules = list(
                    client.list_replication_rules(
                        {
                            "scope": "cms",
                            "name": dataset.name,
                            "rse_expression": rse,
                        }
                    )
                )
                if rules:
                    log.info("Already subscribed %s", dataset.name)
                    if dataset.status == "failed":
                        log.info("Clearing failed status for %s", dataset.name)
                        dataset.clear_failed()  # type: ignore
                    dataset.subscription = rules[0]["id"]
                    dataset.ok_cnt = rules[0]["locks_ok_cnt"]
                    dataset.replicating_cnt = rules[0]["locks_replicating_cnt"]
                    dataset.stuck_cnt = rules[0]["locks_stuck_cnt"]
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
