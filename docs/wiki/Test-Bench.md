# Test Bench

A fresh LiteLLM proxy has nothing worth looking at. No key carries an expiry, no model carries a
readable credential, no team sits above a ceiling. Open the dashboard against it and every
interesting cell is honest and empty:

```
Remaining    Expiry unknown
Credential   Not exposed by the gateway
Status       Not checked
```

None of that is a dashboard defect. It is the absence of data on the other side — and it makes
the synchronizer impossible to exercise, because there is nothing for it to find.

`tools/popula_bancada.py` creates the other side.

## What it builds

| Object | Name | Why it exists |
| :--- | :--- | :--- |
| Team | `bancada-time-estrito` | rpm 60 / tpm 20 000 / budget 5 — sits under the platform ceiling |
| Team | `bancada-time-folgado` | rpm 300 / tpm 400 000 / budget 250 — **above** it, in all three fields |
| Key | `bancada-chave-ativa` | 30 days of validity → `Active` |
| Key | `bancada-chave-renovando` | 14 minutes against a 15-minute margin → `Expiring` |
| Key | `bancada-chave-vencida` | one second of validity → `Expired`, and still registered |
| Key | `bancada-chave-incoerente` | rpm 600 inside a team capped at 60 → a limit finding |
| Key | `bancada-chave-bloqueada` | created blocked → `Blocked` |
| Credential | `bancada-cred-<provider>` | named credential, value read from the environment |
| Model | `bancada-<provider>-<model>` | real provider, bound to the named credential |

Every object carries the `bancada-` prefix. That prefix is the contract with `--limpar`: nothing
without it is ever deleted, and nothing with it is assumed to belong to somebody else.

## Running it

```bash
docker compose -f docker-compose.example.yml --env-file .env up -d
python3 tools/popula_bancada.py
docker compose -f docker-compose.example.yml --env-file .env restart litellmrtk-sync
```

The restart is not decoration. The virtual-key table is read live on every page load, but the
model table comes from the last completed cycle — without a new cycle the models keep the verdict
they had before the bench existed.

| Flag | Effect |
| :--- | :--- |
| *(none)* | creates whatever is missing; running twice creates nothing the second time |
| `--conferir` | prints the current state and writes nothing |
| `--limpar` | deletes everything with the `bancada-` prefix, and only that |
| `--aposentar-demo` | also removes the hand-made demo objects the bench supersedes, named one by one |
| `--referencia-ambiente` | stores the credential as `os.environ/NAME` — see the limitation below |
| `--url` | the proxy address, when it is not `http://127.0.0.1:8083` |

## Where the secrets come from

The script holds none. It reads `GROQ_API_KEY`, `MISTRAL_API_KEY` and `GEMINI_API_KEY` from the
environment, falling back to the repository `.env`, which is gitignored. A provider whose variable
is empty is skipped with a notice — no model, no credential, no failure.

The same variables are passed to the **proxy** container by `docker-compose.example.yml`, so a
model may point at `os.environ/NAME` instead of storing the secret in the proxy database.

## Two limitations you will meet, and what the screen says about them

### The gateway removes the model's API key — it does not mask it

`GET /model/info` runs `remove_sensitive_info_from_deployment`, which does
`litellm_params.pop("api_key", None)` before any masking. The field does not arrive redacted; it
does not arrive at all. Neither the secret nor an `os.environ/NAME` reference survives the trip.

Two consequences:

* a model with a perfectly valid inline key reads, from the API, exactly like a model with no
  credential at all. The dashboard used to call that **"None declared"**, which sent operators
  looking for configuration that was already there. It now says **"Not exposed by the gateway"** —
  the honest statement of what happened;
* the credential cannot be validated from the record, because the record has no credential. The
  verdict now comes from `GET /health` on the proxy itself, which makes a real call to each
  provider with the key it actually holds. `Accepted`, `Rejected`, `Rate limited` and `Unreachable`
  on the model table are that answer, translated. It runs under `CREDENTIAL_CHECK_ENABLED`, and it
  costs one real provider call per model per cycle.

A **named credential** is the one provenance that survives, because `litellm_credential_name` is on
the masker's exception list. That is why the bench binds every model to one.

### A named credential does not resolve `os.environ/`

`CredentialAccessor.get_credential_values` returns the stored value verbatim; it never passes
through `get_secret`. Write `os.environ/GROQ_API_KEY` into a named credential and the provider
receives that literal string and answers `Invalid API Key`. The reference *is* resolved when it
sits in `litellm_params.api_key` on the model — but then `/model/info` removes the field, and the
provenance disappears from the screen again.

So the two goals are mutually exclusive in LiteLLM 1.100.1:

| Path | Works at runtime | Visible on the dashboard |
| :--- | :--- | :--- |
| `litellm_params.api_key = os.environ/NAME` | yes | no — the field is removed |
| Named credential holding the value | yes | yes — `Named credential` |
| Named credential holding `os.environ/NAME` | **no** | yes |

The bench takes the middle row by default. `--referencia-ambiente` takes the bottom one, and exists
for the day upstream fixes it.

## Transient states

`bancada-chave-renovando` is born one minute inside the current renewal window, so it reads
`Expiring` until the window closes and `Expired` after that. That is the point — the state is real,
not simulated. It is also the only cell of the bench that decays on its own, so it is the only object
the script re-evaluates instead of skipping by alias:

```bash
python3 tools/popula_bancada.py      # rearms the key if it left the renewal window
```

The key carries `"ephemeral": True` in its design. On every run the script reads the live `expires`
and rearms — deletes and recreates — whenever the remaining time is gone or larger than
`REFRESH_MARGIN`. Its `duration` is derived from that same margin (`margin - 60s`, floor 60s), never
written by hand: a fixed `14m` would sit *outside* a margin of, say, 600s, and the key would then be
rearmed on every single run — idempotence lost, and the screen showing `Active` where `Expiring` was
the whole point. The margin is read at call time, not at import, so a value living only in the `.env`
is honoured rather than silently replaced by the 900s default.

Every other object is skipped while its alias exists, so a second run still writes nothing:

```
= time 'bancada-time-estrito' já existe
= chave 'bancada-chave-ativa' já existe
```

Without this, idempotency by alias would skip the expired key forever and the screen would lose the
`Expiring` state permanently — the one state the renewal margin exists to show.

## The team column shows an alias, and where it comes from

`/key/list` serializes `team_alias` and returns it **null every time** — measured against the live
proxy, including with `include_team_keys=true`. The only route that carries the readable name is
`/team/list`.

So the dashboard joins the two: `collect_dashboard_state` builds a `{team_id: team_alias}` map from
`/team/list` and hands it to the keys table. Before that join the screen contradicted itself — the
limit finding said `bancada-time-estrito` while the table beside it showed
`da2aa25d-5461-4dbe-b6d4-285d29802894`, the same team under two names.

The UUID is not thrown away: it stays in the cell's `title`, so an operator matching the screen
against the API still has it. A key whose team is missing from `/team/list` falls back to showing the
id, and a proxy version without the route degrades to the same fallback rather than taking the page
down.

## Exercising the limit detector

The team-versus-platform comparison is skipped entirely while the platform ceilings are empty. Set
them in the `.env` and recreate the synchronizer:

```
PLATFORM_RPM_LIMIT=120
PLATFORM_TPM_LIMIT=200000
PLATFORM_MAX_BUDGET=100
```

`bancada-time-folgado` then exceeds all three, and `bancada-chave-incoerente` exceeds its own team
on two more — six findings where an empty proxy produces none. See
[Rate Limit Coherence](Rate-Limit-Coherence) for the rule itself.

## Verifying on the screen, not on the API

An API returning `200` proves nothing about the dashboard. Read the page:

```bash
curl -s -u "admin:$DASHBOARD_PASSWORD" http://127.0.0.1:9093/ > /tmp/painel.html
grep -o "Expiry unknown" /tmp/painel.html | wc -l
grep -o "Not exposed by the gateway" /tmp/painel.html | wc -l
grep -o "Not checked" /tmp/painel.html | wc -l
```

Count occurrences, not lines: several cells share a line in the rendered HTML, and `grep -c` hides
the difference.
