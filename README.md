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
| `--max-size TEXT` | `200TB` | Maximum total size of datasets occupying space on the RSE (subscribed, transferred, stuck or done but not yet released) |
| `--watch` | False | Keep polling Rucio and subscribe more datasets as budget frees up, until all matching datasets are subscribed |
| `--poll-interval INTEGER` | 300 | Seconds to wait between polls when `--watch` is set |
| `--dry-run` | False | Show which datasets would be subscribed without contacting Rucio or changing the database |

Datasets are only subscribed while the total size of datasets still occupying
space on the RSE (i.e. not yet `released`, see the [`release`](#release)
command) stays below `--max-size`. Datasets that do not fit in the remaining
budget are skipped for the current run. With `--watch`, the command instead
keeps polling Rucio for transfer completion and subscribing more datasets as
space is freed (e.g. once an operator runs `cmstool release`), until every
matching dataset has been subscribed.

**Examples:**

```bash
# Subscribe all initialized datasets to the default RSE, up to 200TB in flight
cmstool subscribe

# Subscribe datasets matching a pattern
cmstool subscribe -d "/DoubleMuon/Run2012*"

# Subscribe to a specific RSE
cmstool subscribe --rse T2_CH_CERN

# Use a smaller size budget
cmstool subscribe --max-size 50TB

# Keep subscribing more datasets as quota frees up (long-running)
cmstool subscribe --watch --poll-interval 600

# Preview what would be subscribed without contacting Rucio or the database
cmstool subscribe --dry-run

# Force resubscription of failed datasets
cmstool subscribe --force

# Force resubscription of failed datasets matching a pattern
cmstool subscribe --force -d "/DoubleMuon/Run2012*"
```

---

### `release`

Mark `done` datasets as `released` once an external actor has copied their
verified data elsewhere and removed it from the RSE. This frees the
dataset's size quota so `subscribe --max-size` can subscribe more data.

```bash
cmstool release [OPTIONS] [DATASET_IDS]...
```

| Option | Default | Description |
|--------|---------|-------------|
| `-d, --dataset-pattern TEXT` | | Glob pattern to select datasets by name |
| `--delete-rule` | False | Also delete the Rucio replication rule for the released datasets |
| `DATASET_IDS` | | Optional list of dataset IDs to release |

**Examples:**

```bash
# Release all done datasets
cmstool release

# Release datasets matching a pattern
cmstool release -d "/DoubleMuon/Run2012*"

# Release specific datasets by ID
cmstool release 42 43

# Release and also delete the Rucio replication rule
cmstool release --delete-rule
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
initialized → subscribed → transferred → done → released
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
| `released` | An external actor has copied and removed the data from the RSE; no longer counts against the `subscribe --max-size` quota |
| `stuck` | Rucio has marked the rule as stuck |
| `failed` | Subscription attempt failed |