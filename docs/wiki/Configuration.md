# Configuration

Everything is configured by environment variable. There is no configuration file to mount and no
interactive setup step — the container is fully headless, and the panel is a view over that
configuration, never a second source of truth for it.

A regression test compares this contract against what the code actually reads: a variable added
to the code and not to [`.env.example`](https://github.com/pathbit/LiteLlmRTKSync/blob/master/.env.example)
fails the build.

---

## 1. LiteLLM proxy

| Variable | Default | Description |
| :--- | :--- | :--- |
| `LITELLM_URL` | `http://litellm:4000` | Base URL of the proxy. Inside Compose this is the service name |
| `LITELLM_MASTER_KEY` | *(empty)* | Master key used to read administrative state. **Required, and a secret** |

The master key grants full administrative read access to the proxy. Put it in `.env`, never in
an example file, never in a compose default, never in a log line.

---

## 2. Inspection cycle

| Variable | Default | Description |
| :--- | :--- | :--- |
| `SYNC_INTERVAL` | `300` | Seconds between cycles |
| `REFRESH_MARGIN` | `900` | How far ahead a virtual key starts being reported as expiring |
| `CRON_ENABLED` | `1` | Internal scheduler (`1`/`0`) |
| `CRON_INTERVAL` | inherits `SYNC_INTERVAL` | Scheduler interval, when it should differ from the cycle |

`REFRESH_MARGIN` does not renew anything here — there is nothing to renew in LiteLLM. It is the
window in which a key is reported as *expiring* rather than merely *valid*, so somebody has time
to issue a replacement.

---

## 3. Platform ceiling

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PLATFORM_RPM_LIMIT` | *(empty)* | Platform-wide requests per minute |
| `PLATFORM_TPM_LIMIT` | *(empty)* | Platform-wide tokens per minute |
| `PLATFORM_MAX_BUDGET` | *(empty)* | Platform-wide budget |

No team and no key may declare above these. LiteLLM accepts the incoherent configuration without
complaint, so this is where the ceiling is declared. Empty means *no cap declared* — reported as
a choice, not as an error. See [Rate Limit Coherence](Rate-Limit-Coherence).

---

## 4. Panel

| Variable | Default | Description |
| :--- | :--- | :--- |
| `ENABLE_WEB_DASHBOARD` | `1` | Serve the dashboard (`1`/`0`) |
| `WEB_HOST` | `0.0.0.0` | Binding interface |
| `WEB_PORT` | `9090` | HTTP port inside the container |
| `DASHBOARD_USER` | `admin` | Basic Auth username |
| `DASHBOARD_PASSWORD` | *(empty)* | Panel password. Empty means the recovery credential is used for the first sign-in |
| `DASHBOARD_RECOVERY_HASH` | auto | Break-glass credential hash; generated on first boot when omitted |

`WEB_HOST=0.0.0.0` binds inside the container. What decides network exposure is the port
publication — keep it on `127.0.0.1` unless you have a reason not to.

> **Running with `ENABLE_WEB_DASHBOARD=0`?** The image ships a `HEALTHCHECK` that probes
> `/healthz`, which only the dashboard serves. With the dashboard off, that probe can never
> succeed and the container is reported unhealthy forever — which also stops any
> `depends_on: service_healthy` from ever being satisfied. Disable the check along with the
> dashboard:
>
> ```yaml
> healthcheck:
>   disable: true
> ```

### When the password comes from the environment

Setting `DASHBOARD_PASSWORD` makes the environment the source of truth. The panel then **refuses**
a password change on screen and says so, instead of accepting it and losing it on the next
restart. Change it in the environment and recreate the container.

---

## 5. Credential validation

| Variable | Default | Description |
| :--- | :--- | :--- |
| `CREDENTIAL_CHECK_ENABLED` | `1` | Ask each provider whether the key declared on the model is still accepted |
| `CREDENTIAL_CHECK_TIMEOUT` | `8` | Timeout in seconds per probe |

Turning it off leaves the expiry watch and the limit coherence check running; only the live probe
stops. Useful in an environment with no outbound access, where every probe would fail for reasons
that say nothing about the key.

---

## 6. Storage and logging

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DATA_DIR` | `/app/data` | Base for everything this container writes. **Created on startup** |
| `LOG_DIR` | `<DATA_DIR>/logs` | Log directory |
| `LOG_RETENTION_DAYS` | `30` | Days of history kept |
| `LOG_LEVEL` | `INFO` | Minimum level written |
| `LOG_TO_STDOUT` | `1` | Also write to stdout (`1`/`0`) |

The directory is created at startup rather than assumed. When it was assumed, a `DATA_DIR`
pointing somewhere that did not exist made the panel's stored credential land outside the
volume — and vanish on the next recreate.

---

## 7. Test stack only

| Variable | Description |
| :--- | :--- |
| `POSTGRES_PASSWORD` | Database password for the bundled stack |
| `LITELLM_SALT_KEY` | Salt the proxy uses for stored credentials |

Both are required and have no default on purpose: `docker-compose.test.yml` refuses to start
without them.

---

## Fully headless example

No dashboard, scheduler on a one-minute cadence, logs kept for 90 days:

```yaml
environment:
  - LITELLM_URL=http://litellm:4000
  - LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY:?}
  - SYNC_INTERVAL=60
  - ENABLE_WEB_DASHBOARD=0
  - LOG_DIR=/app/data/logs
  - LOG_RETENTION_DAYS=90
  - LOG_TO_STDOUT=0
healthcheck:
  disable: true
```
