# Dashboard

A single server-rendered page. The HTML is assembled on the server with the data already
embedded — **the browser never queries the proxy**, and no endpoint serves LiteLLM state to an
unauthenticated request. It works with JavaScript disabled; jQuery is there for comfort, not for
correctness.

Available at `http://localhost:9093` when `ENABLE_WEB_DASHBOARD=1` (port `9090` inside the
container, published on `9093` so the three RTKSync panels can run side by side).

---

## What the page shows

**Operational metrics.** Virtual keys, teams, models registered, and findings grouped by
severity.

**Virtual keys.** Each key with its expiry countdown. A key whose expiry is already past is
separated from one merely inside the margin — they need different actions. A key with **no
declared expiry** is reported as *undeclared*, never as "unlimited": an absent value is missing
data, not a promise of eternity.

**Identification without exposure.** A key with an alias is shown by its alias. A key without
one has only its token as an identifier, and the token is shown **masked to the last six
characters**. A key is a credential; printing it whole to make a table readable would trade one
problem for a worse one.

**Models.** What is registered and whether each one carries a credential — *whether*, never
which. The provenance cell reads `Named credential` when the model is bound to one, and
**`Not exposed by the gateway`** otherwise: `GET /model/info` runs `pop("api_key", None)` before
answering, so a model with a perfectly valid inline key arrives here indistinguishable from one
with no credential at all. Calling that "none declared" would send the operator looking for
configuration that is already in place.

**The credential verdict comes from the proxy.** Since the record never carries the key, the
status column asks `GET /health` on the proxy itself — which makes a real call to each provider
with the key it actually holds — and translates the answer into `Accepted`, `Rejected`,
`Rate limited` or `Unreachable`. A model the gateway did not judge stays `Not checked`, because
inventing a verdict is worse than admitting the gap. The whole path runs under
`CREDENTIAL_CHECK_ENABLED` and costs one real provider call per model per cycle. To see it with
real data, see [Test Bench](Test-Bench).

**The team column carries the alias, not the raw id.** `/key/list` returns `team_alias` null on
every key, so the readable name is joined in from `/team/list` at render time. Without that join the
page contradicted itself: a limit finding naming `bancada-time-estrito` sat beside a table cell
showing that team's UUID. The id survives in the cell's `title` for anyone matching the screen
against the API, and a team the proxy did not list falls back to showing the id.

**A detail button per row.** Each line of both tables ends in a narrow `(i)` column that opens a
modal with the full detail: for a virtual key, its team, the expiry instant, `rpm_limit`,
`tpm_limit`, the budget ceiling and the models it may call; for a model, its provider, its API
base and where its credential comes from. Same pattern as the siblings, for the same reason —
a limit or an address squeezed into a cell pushed the readable columns off the screen. What is
absent reads *not declared*, never "unlimited", and the key itself never appears.

**Limit findings.** Each one names the field, both values and the consequence. See
[Rate Limit Coherence](Rate-Limit-Coherence).

**Proxy liveness.** A card against `/health/liveliness`, so "the panel is up" and "the proxy is
up" are never confused for each other.

**Inspection scheduler.** Shows the next run, how many findings have accumulated and the result
of the last cycle. The **Logs** button opens the history: one entry per cycle, each with the
actions that cycle produced — a limit above its ceiling, a provider key refused, a route that
could not be read. Without it, a counter reading zero cannot be told apart from a cycle that
failed. `CRON_ENABLED=0` stops the automatic loop; the **Run now** button and the history keep
working.

**One cycle trigger, not two.** The header's **Sync now** button and the scheduler card's **Run
now** submit the same route, `/acoes/cron`, as they do in 9RTKSync and OminiRTkSync. There used
to be a second route that ran the same cycle outside the scheduler, and a cycle triggered that
way never appeared in the history the screen shows.

---

## Routes

| Route | Method | Purpose |
| :--- | :--- | :--- |
| `/healthz` | GET | Unauthenticated liveness probe. `OK` or `LITELLM_UNREACHABLE` |
| `/` | GET | The panel |
| `/api/status` | GET | Full state as JSON |
| `/api/cron-status` | GET | Scheduler state and run history as JSON |
| `/acoes/atualizar` | POST | Reload the page from the last known state |
| `/acoes/cron` | POST | Run a cycle now, through the scheduler — the header's primary button |
| `/acoes/testar-gateway` | POST | Probe the proxy's liveness right now |
| `/acoes/idioma` | POST | Store the interface language |
| `/acoes/credenciais` | POST | Change the panel password |
| `/credenciais-atualizadas` | GET | Confirmation page after a password change |

Everything except `/healthz` requires authentication. `/healthz` is deliberately open because it
is what the container's `HEALTHCHECK` calls, and it reveals nothing beyond whether the proxy
answers.

`/credenciais-atualizadas` is served **before** the authentication check on purpose: at that
exact moment the browser still holds the old password, and demanding credentials there would
produce a bare `401` right after the change succeeded.

---

## Language

English by default, with Portuguese and Spanish in the flag selector. The choice is stored on the
server, in the panel's own preferences database, so it survives a browser change and a cache wipe.

Flag icons come from `flag-icons` on jsDelivr; the Content-Security-Policy allows that host for
images precisely so the flags render.

---

## Security of the page itself

- **Security headers on every response**, including the `401` body. That body is what the browser
  shows when you press **ESC** on the Basic Auth dialog — a page with no headers, if they were
  only applied to the authenticated route.
- **Content-Security-Policy** among them.
- **Cross-origin `POST` is refused.** The browser attaches Basic Auth to a third-party form on
  its own; without this check, a page on another site could trigger an action on your panel.
- **No credential ever reaches a response body, a log line or a banner** — not the master key,
  not a virtual key, not the panel password.

---

## Changing the password

The form writes the new password to the panel's own storage, inside the data directory.

If `DASHBOARD_PASSWORD` is set in the environment, the environment is the source of truth and the
panel **refuses** the change with an explicit notice — *credentials defined by environment
variable; change them in the environment and restart*. Accepting it would be worse: the change
would appear to work and be lost at the next recreate.

See [Authentication](Authentication).
