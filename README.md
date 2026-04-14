# CMSOpenData2025

A tool to manage the transfer of CMS Open Data datasets to a target RSE using Rucio.

## Installation

```bash
pip install -e .
```

## Usage

```bash
cmstool [OPTIONS] COMMAND [ARGS]...
```

## Commands

### `list`

Display the status of datasets in the local database.

```bash
cmstool list [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--limit INTEGER` | 10 | Limit the number of datasets displayed |
| `--status [initialized\|subscribed\|transferred\|stuck\|done\|failed]` | None | Filter datasets by status |

**Examples:**

```bash
# List the first 10 datasets
cmstool list

# List up to 50 datasets
cmstool list --limit 50

# List only failed datasets
cmstool list --status failed

# List only stuck datasets
cmstool list --status stuck
```

---

### `subscribe`

Subscribe datasets to a Rucio RSE (storage site) to initiate transfer.

```bash
cmstool subscribe [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `-d, --dataset-pattern TEXT` | | Glob pattern to select datasets by name |
| `--rse TEXT` | `T3_CH_CERN_OpenData_Stage` | Target RSE to subscribe the dataset to |
| `-f, --force` | False | Force resubscription of failed or stuck datasets |

**Examples:**

```bash
# Subscribe all initialized datasets to the default RSE
cmstool subscribe

# Subscribe datasets matching a pattern
cmstool subscribe -d "/DoubleMuon/Run2012*"

# Subscribe to a specific RSE
cmstool subscribe --rse T2_CH_CERN

# Force resubscription of failed datasets
cmstool subscribe --force

# Force resubscription of failed datasets matching a pattern
cmstool subscribe --force -d "/DoubleMuon/Run2012*"
```

---

### `inspect`

Inspect stuck datasets and query Rucio for the status of their replication
rules and individual stuck file locks. Useful for diagnosing why transfers
are not proceeding.

```bash
cmstool inspect [OPTIONS] [DATASET_IDS]...
```

| Option | Default | Description |
|--------|---------|-------------|
| `--limit INTEGER` | 10 | Limit the number of datasets inspected |
| `DATASET_IDS` | | Optional list of dataset IDs to inspect |

For each dataset the following information is shown:

- **Rule state** — the overall Rucio replication rule state (`OK`, `REPLICATING`, `STUCK`)
- **Rule error** — the error message from Rucio if the rule is stuck
- **Rule RSE** — the target RSE expression
- **Expires** — the expiry time of the rule
- **Stuck file locks** — the individual files that are stuck, with their error messages

**Examples:**

```bash
# Inspect all stuck datasets (up to 10)
cmstool inspect

# Inspect more stuck datasets
cmstool inspect --limit 50

# Inspect specific datasets by ID
cmstool inspect 42 43 44

# Inspect a single dataset by ID
cmstool inspect 42
```

---

## Dataset States

Datasets transition through the following states:

```
initialized → subscribed → transferred → done
     ↓              ↓
   failed          stuck
     ↓
initialized (via clear)
```

| State | Description |
|-------|-------------|
| `initialized` | Dataset is known but not yet submitted to Rucio |
| `subscribed` | A Rucio replication rule has been created |
| `transferred` | All files have been transferred |
| `done` | Transfer verified and complete |
| `stuck` | Rucio has marked the rule as stuck |
| `failed` | Subscription attempt failed |