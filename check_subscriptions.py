#!/usr/bin/env python3
"""
Check Rucio subscription state of datasets at OpenData sites and other sites.
Usage: python check_subscriptions.py [input_file]
"""

import sys
import subprocess

INPUT_FILE = sys.argv[1] if len(sys.argv) > 1 else "failed.txt"

SITE1 = "T3_CH_CERN_OpenData"
SITE2 = "T3_CH_CERN_Opendata_Stage"

def get_rules(dataset):
    """Retrieve Rucio rules for a dataset as a list of dicts."""
    try:
        result = subprocess.run(
            ["rucio", "list-rules", "--csv", dataset],
            capture_output=True, text=True, timeout=30
        )
        rules = []
        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            # CSV columns: id, account, scope:name, state, rse_expression, copies, ...
            if len(parts) >= 5:
                rules.append({
                    "id":    parts[0],
                    "state": parts[3],
                    "rse":   parts[4],
                })
        return rules
    except Exception as e:
        print(f"  Error querying Rucio: {e}", file=sys.stderr)
        return []

def read_datasets(filepath):
    datasets = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("//") and not line.startswith("#"):
                datasets.append(line)
    return datasets

def main():
    datasets = read_datasets(INPUT_FILE)

    col_ds    = 100
    col_site  = 20
    col_other = 60
    header = (
        f"{'Dataset':<{col_ds}} "
        f"{SITE1:<{col_site}} "
        f"{SITE2:<{col_site}} "
        f"{'Other subscribed sites':<{col_other}}"
    )
    print(header)
    print("-" * len(header))

    for dataset in datasets:
        # Strip DBS-style scope prefix for Rucio (cms:/<name>)
        rucio_name = f"cms:{dataset}" if not dataset.startswith("cms:") else dataset

        rules = get_rules(rucio_name)

        site1_states = [r["state"] for r in rules if r["rse"] == SITE1]
        site2_states = [r["state"] for r in rules if r["rse"] == SITE2]
        other_sites  = [
            f"{r['rse']}:{r['state']}"
            for r in rules
            if r["rse"] not in (SITE1, SITE2)
        ]

        site1_str = ",".join(site1_states) if site1_states else "NOT SUBSCRIBED"
        site2_str = ",".join(site2_states) if site2_states else "NOT SUBSCRIBED"
        other_str = "  ".join(other_sites)  if other_sites  else "none"

        print(
            f"{dataset:<{col_ds}} "
            f"{site1_str:<{col_site}} "
            f"{site2_str:<{col_site}} "
            f"{other_str:<{col_other}}"
        )

if __name__ == "__main__":
    main()