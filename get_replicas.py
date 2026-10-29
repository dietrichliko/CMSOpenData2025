#!/usr/bin/env python

import argparse
import json
import re
import subprocess
from collections import defaultdict

from rucio.client.didclient import DIDClient
from rucio.client.replicaclient import ReplicaClient

RED = "\033[31m"
RESET = "\033[0m"


def get_dbs_file_count(dataset_name):
    try:
        result = subprocess.run(
            ["dasgoclient", "-query", f"summary dataset={dataset_name}", "-json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            print(f"  [dasgoclient error: {result.stderr.strip()}]")
            return None
        data = json.loads(result.stdout)
        for entry in data:
            for item in entry.get("summary", []):
                if "nfiles" in item:
                    return item["nfiles"]
    except Exception as e:
        print(f"  [dasgoclient exception: {e}]")
    return None


def get_sites_for_dataset(did_client, replica_client, dataset_name):
    rucio_total = sum(1 for _ in did_client.list_files(scope="cms", name=dataset_name))
    dbs_total = get_dbs_file_count(dataset_name)

    replicas = replica_client.list_replicas(
        dids=[{"scope": "cms", "name": dataset_name}],
    )
    site_counts = defaultdict(int)
    no_replica = 0
    replica_total = 0
    for replica in replicas:
        replica_total += 1
        rses = replica.get("rses", {})
        if not rses:
            no_replica += 1
        for site in rses.keys():
            site_counts[site] += 1

    preferred = "T3_CH_CERN_OpenData_Stage"
    parts = []
    parts.append(f"DBS: {dbs_total if dbs_total is not None else 'N/A'}")
    parts.append(f"Rucio: {rucio_total}")
    parts.append(f"With replicas: {replica_total}")
    if no_replica:
        parts.append(f"No replica: {no_replica}")
    if preferred in site_counts:
        parts.append(f"{preferred}: {site_counts[preferred]}")
    for site, count in sorted(site_counts.items()):
        if site != preferred:
            parts.append(f"{site}: {count}")

    color = RED if dbs_total is not None and dbs_total != rucio_total else ""
    reset = RESET if color else ""
    print(f"{color}{dataset_name}{reset}\n  {' | '.join(parts)}")


def main():
    parser = argparse.ArgumentParser(
        description="Get replicas for CMS datasets from Rucio."
    )
    parser.add_argument("datasets", nargs="*", help="Dataset name(s) to query")
    parser.add_argument(
        "-f", "--file", help="File containing dataset names (one per line)"
    )
    args = parser.parse_args()

    dataset_names = list(args.datasets)

    if args.file:
        with open(args.file, "r") as f:
            lines = [
                re.sub(r"^\s*\[\d+/\d+\]\s*", "", line.strip())
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
            lines = [line for line in lines if line]
            dataset_names.extend(lines)

    if not dataset_names:
        parser.error("Provide at least one dataset name or use -f to specify a file.")

    did_client = DIDClient()
    replica_client = ReplicaClient()

    for dataset_name in dataset_names:
        get_sites_for_dataset(did_client, replica_client, dataset_name)


if __name__ == "__main__":
    main()
