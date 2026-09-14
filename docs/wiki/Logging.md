# Logging

Container stdout is volatile: it disappears on `docker rm`, gets truncated by the log driver and
does not survive a restart. Events that matter — cycles that failed, keys found expired,
incoherent limits, panel access — are therefore also written to a file, with daily rotation and
age-based purge.

Implemented in [`src/litellm_rtksync/logs.py`](https://github.com/pathbit/LiteLlmRTKSync/blob/master/src/litellm_rtksync/logs.py).

---

## Configuration

| Variable | Default | Description |
| :--- | :--- | :--- |
| `LOG_DIR` | `<DATA_DIR>/logs` | Destination directory. Falls back to `~/.litellmrtksync/logs` when the data directory is not writable |
| `LOG_RETENTION_DAYS` | `30` | Days a rotated file is kept. Minimum `1`; an unparseable value falls back to 30 |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_TO_STDOUT` | `1` | `0` stops mirroring on stdout. The file keeps receiving everything |

---

## Rotation and retention

- One file, `litellmrtksync.log`, rotated at **UTC midnight**.
- Rotated files are named `litellmrtksync.log.YYYY-MM-DD`.
- `backupCount` equals `LOG_RETENTION_DAYS`, so daily rotation keeps exactly that many days.
- On every startup, `purge_expired_logs()` also deletes rotated files older than the retention
  window. This catches files left behind by a container that was down for a while, which
  `backupCount` alone never revisits.

---

## The level follows the prefix

A line logged as `[FALHA]` is written at `ERROR`, `[AVISO]` at `WARNING`, and so on. It sounds
obvious and was not: when every line was emitted at `INFO` regardless of prefix, setting
`LOG_LEVEL=WARNING` to reduce noise hid the failures along with the noise — precisely backwards.

---

## What never reaches the log

- The **master key** of the proxy.
- A **virtual key** in full — a key with no alias is identified by the last six characters of its
  token.
- The **panel password**, or the value of the recovery credential. The startup line names the
  *file* that holds it, never its contents:

```
[AUTH] Recovery credential generated at /app/data/.dashboard_recovery (mode 0600)
```

Log lines get shipped to aggregators, pasted into tickets and read by people who should not hold
the credential. A credential in a log line is a credential published.

---

## Reading it

```bash
# Follow the container output
docker logs -f litellmrtk-sync

# The persistent file
docker exec litellmrtk-sync cat /app/data/logs/litellmrtksync.log

# Only the findings
docker exec litellmrtk-sync grep -E "\[FALHA\]|\[AVISO\]" /app/data/logs/litellmrtksync.log
```

---

## Not versioned

Logs are not committed, and neither is anything else the container writes. `.gitignore` covers
`logs/`, `tmp/`, `*.sqlite*`, `.env` and the recovery file. A log carries operational detail
about a real installation and belongs in the volume, never in the repository.
