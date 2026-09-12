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
which.

**Limit findings.** Each one names the field, both values and the consequence. See
[Rate Limit Coherence](Rate-Limit-Coherence).

**Proxy liveness.** A card against `/health/liveliness`, so "the panel is up" and "the proxy is
up" are never confused for each other.

---

## Routes

| Route | Method | Purpose |
| :--- | :--- | :--- |
| `/healthz` | GET | Unauthenticated liveness probe. `OK` or `LITELLM_UNREACHABLE` |
| `/` | GET | The panel |
| `/api/status` | GET | Full state as JSON |
| `/acoes/atualizar` | POST | Run an inspection cycle now |
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

English by default, with Portuguese and Spanish in the flag selector. The choice is stored per
browser, in the panel's own preferences, and applies to every page including the findings.

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
