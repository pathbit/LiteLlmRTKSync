# Architecture

One process. A cycle that reads the proxy's administrative API, a set of checks over what came
back, and a server-rendered page showing the result. No database of its own, no queue, no agent
on the proxy.

```
        ┌──────────────────────┐
        │  LiteLLM proxy       │
        │  (Postgres/Prisma)   │
        └──────────┬───────────┘
                   │  admin API, GET only
                   ▼
   ┌───────────────────────────────┐
   │ gateway.py  — the admin API,   │
   │               the cycle, and   │
   │               what this gateway│
   │               stores and where │
   │ models.py   — shapes the rows  │
   │ credential_check.py — live probe│
   └───────────────┬───────────────┘
                   │ findings
                   ▼
   ┌───────────────────────────────┐
   │ web.py  — server-rendered page │
   │ logs.py — persistent log       │
   └───────────────────────────────┘
```

---

## Why the API and never the database

LiteLLM keeps its state in Postgres behind Prisma. Reading it directly would be possible and is
the wrong choice for two reasons:

**The schema changes between releases.** A column renamed upstream breaks a reader that bypassed
the API, silently and at the worst moment.

**Writing would skip the invariants.** The proxy enforces rules when a key is issued or a limit
is set. A write straight to the table gets none of them. This project is read-only anyway, but
the reasoning is the same: the API is the contract, the table is an implementation detail.

### What it calls

| Route | What comes back |
| :--- | :--- |
| `/health/liveliness` | Whether the proxy is up |
| `/key/list` | Virtual keys, paginated to the end |
| `/team/list` | Teams and their ceilings |
| `/model/info` | Registered models and their `litellm_params` |
| `/credentials` | Named credentials, on versions that have them |

All GET. There is no code path in this project that writes to the proxy.

Two details learned the hard way:

- `/key/list` needs **`return_full_object=true`**, otherwise it returns bare strings and every
  limit check has nothing to compare.
- `/model/info` answers **500**, not 200 with an empty list, on an installation with no model
  registered. That is a normal state for a fresh install; treating it as a failure would break
  the cycle of exactly the installation someone just created. Each section degrades on its own —
  a failing `/model/info` does not stop the key and team checks from reporting.

---

## The cycle

1. **Liveness.** If the proxy does not answer, the cycle says so and stops. Reporting "0 findings"
   when nothing could be read would be a lie by omission.
2. **Collect.** Keys, teams, models, credentials — each section independently, so one failing
   endpoint does not silence the others.
3. **Check expiry.** `LiteLLM_VerificationToken.expires` against now and against
   `REFRESH_MARGIN`. Already expired and expiring soon are reported apart. An absent expiry is
   *undeclared*, not "unlimited".
4. **Probe credentials.** Each provider key registered on a model is asked of its own provider —
   when `CREDENTIAL_CHECK_ENABLED=1`.
5. **Check coherence.** Field by field, key ≤ team ≤ platform. See
   [Rate Limit Coherence](Rate-Limit-Coherence).
6. **Report.** To the panel, to the log, and — for `--status` — to the exit code.

---

## Modules

| Module | Responsibility |
| :--- | :--- |
| `gateway.py` | Everything that knows what THIS gateway stores and where: the admin API (GET only; tolerates 404 and 500 where those are normal states), `SyncEngine` orchestrating the cycle, and the `carregar_painel()` seam. |
| `identidade.py` | The only file that may differ from the sibling panels: name, gateway, palette, icon, ports, cookie names. |
| `models.py` | Turns API rows into objects with the derived properties the screen needs |
| `credential_check.py` | Live probe of a provider key |
| `config.py` | The environment contract, and the data directory created at startup |
| `cli.py` | `--status`, `--once`, `--daemon`, and the exit code |
| `cron.py` | Background scheduler; keeps per-cycle history with the actions each produced. |
| `web.py` | The server-rendered page and its routes |
| `render.py` | Server-side HTML rendering. |
| `paginacao.py` | The 10-per-page window every grid and log popup shares. |
| `sso.py` | OIDC and SAML2 sign-in, and the settings screen behind them. |
| `sessao.py`, `protecao.py` | Signed session cookie; rate limit, 429 and proof-of-work against bots. |
| `auth.py` | Password hashing and break-glass recovery |
| `logs.py` | Persistent log with rotation and retention |
| `prefs.py` | Panel preferences (language, stored credential) |
| `i18n.py` | English, Portuguese, Spanish |

Every module above is the SAME FILE in the three siblings, byte for byte, except `gateway.py`
and `identidade.py` — the same problems have the same answers, so they have the same code.
`gateway.py` is what this project adds: it is the only place that knows this is LiteLLM and
not 9Router or OmniRoute.

---

## Storage

The only thing written is inside `DATA_DIR`: the panel's preferences, the stored credential, the
recovery file and the logs. **The directory is created at startup**, not assumed — when it was
assumed, a path that did not exist sent the stored credential outside the volume, where it
vanished at the next recreate.

Nothing about the proxy is cached to disk. Every cycle reads fresh.
