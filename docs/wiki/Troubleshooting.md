# Troubleshooting

Concrete symptoms, and what each one actually means.

---

## The container is `unhealthy` forever

**With the dashboard disabled.** The image ships a `HEALTHCHECK` that probes `/healthz`, and only
the dashboard serves it. With `ENABLE_WEB_DASHBOARD=0` the probe can never succeed, so the
container is marked unhealthy permanently — which also stops any `depends_on: service_healthy`
from ever being satisfied. Disable the check along with the dashboard:

```yaml
healthcheck:
  disable: true
```

**With the dashboard enabled.** Then `/healthz` is answering `LITELLM_UNREACHABLE`: the panel is
up and the proxy is not. See the next entry.

---

## `/healthz` answers `LITELLM_UNREACHABLE`

The panel is running; the proxy is not answering `/health/liveliness`. In order of likelihood:

1. **Wrong address.** Inside Compose, `LITELLM_URL` must be the *service name*
   (`http://litellmrtk-router:4000`), not `localhost` — `localhost` inside the synchronizer's container is
   the synchronizer itself.
2. **The proxy is still booting.** LiteLLM runs Prisma migrations on first start; it can take
   more than a minute. `start_period` on its healthcheck should allow for that.
3. **The proxy is genuinely down.**

```bash
docker exec litellmrtk-sync /opt/venv/bin/python3 -c \
  "import urllib.request;print(urllib.request.urlopen('http://litellmrtk-router:4000/health/liveliness',timeout=5).status)"
```

---

## Every key reports "no limit declared"

`/key/list` returns bare strings instead of objects unless it is called with
**`return_full_object=true`**. Without the full object there are no `rpm_limit`, `tpm_limit` or
team association to compare, so nothing can be checked. The client always sends the parameter;
if you see this against a proxy version that ignores it, report it — the version matters.

---

## The cycle reports models as failing on a brand-new install

`/model/info` answers **500**, not 200 with an empty list, when no model is registered. That is a
normal state for a fresh installation. The client tolerates it and reports an empty catalogue;
each section degrades on its own, so the key and team checks keep working.

If you see a hard failure here, check that you are not on an old build that treated the 500 as
fatal.

---

## An incoherent limit is reported and the numbers look fine to me

Read the message: it names both values and which one applies. The most common surprise is that
**a key *below* its team is never reported** — that is a deliberate tightening. Only a value
*above* the applicable ceiling is a finding.

The second most common: `0` means **no limit** in LiteLLM, not "zero allowed". It is skipped in
the comparison; treating it as a ceiling of zero would raise a finding against every key in the
installation.

See [Rate Limit Coherence](Rate-Limit-Coherence).

---

## `--status` exits 1 and I expected 0

That is the contract: `--status` exits `1` when it finds an incoherent limit, so it works as a CI
gate. If you want the report without the gate, use `--once`.

---

## The panel refuses to change the password

`DASHBOARD_PASSWORD` is set in the environment, which makes the environment the source of truth.
The panel says so explicitly rather than accepting a change it would lose on the next recreate.
Change the variable and recreate the container:

```bash
docker compose -f docker-compose.example.yml up -d --force-recreate litellmrtk-sync
```

See [Authentication](Authentication).

---

## I lost the panel password

The **recovery credential** stays valid after a normal password is set, precisely for this:

```bash
docker exec litellmrtk-sync cat /app/data/.dashboard_recovery
```

Sign in as `admin` with that value and set a new password on the screen.

If the data directory was recreated, the recovery credential was regenerated with it — the log
line at startup names the file.

---

## The stored password disappeared after recreating the container

`DATA_DIR` is pointing somewhere outside the volume. Everything the panel stores — preferences,
the hashed password, the recovery file — lives there. The directory is created at startup if it
does not exist, which means a typo produces a working panel that forgets everything on restart,
rather than an error.

Check that the path in `DATA_DIR` is the one the volume mounts.

---

## Provider probes all fail in an isolated network

Expected: the probe asks the provider whether the key is still accepted, and there is no outbound
access. Turn it off and keep the rest:

```yaml
- CREDENTIAL_CHECK_ENABLED=0
```

Expiry watching and limit coherence keep working — neither needs the internet.

---

## A log line I need is missing

Check `LOG_LEVEL`. The level of each line follows its prefix: `[FALHA]` is `ERROR`, `[AVISO]` is
`WARNING`, `[INFO]` is `INFO`. Setting `LOG_LEVEL=WARNING` keeps failures and warnings and drops
the rest.
