# LiteLlmRTKSync

**LiteLLM Universal Token & Connection Synchronizer** — the read-only health guardian for
[LiteLLM](https://github.com/BerriAI/litellm) proxies. It reports virtual keys about to expire,
provider keys the catalogue still trusts but the provider has revoked, and rate-limit ceilings
that contradict each other.

This wiki is generated from [`docs/wiki/`](https://github.com/pathbit/LiteLlmRTKSync/tree/master/docs/wiki)
in the main repository. Edit the files there and open a pull request — a push to `master`
republishes these pages automatically. Editing a page directly here will be overwritten.

---

## Pages

| Page | What it covers |
| :--- | :--- |
| [Installation](Installation) | Docker Compose and local virtual environment |
| [Configuration](Configuration) | Every environment variable — the full headless contract |
| [Dashboard](Dashboard) | The server-rendered panel, language switcher, findings |
| [Authentication](Authentication) | Credentials, headless mode, break-glass recovery |
| [Logging](Logging) | Persistent file log, rotation, 30-day retention |
| [Architecture](Architecture) | How the inspection engine talks to the LiteLLM admin API |
| [Remote Access](Remote-Access) | Tunnel, Tailscale, and what has to be on before either |
| [Egress Testing](Egress-Testing) | A bench that proves where the traffic actually leaves from |
| [Rate Limit Coherence](Rate-Limit-Coherence) | The rule this project exists for: key ≤ team ≤ platform |
| [Troubleshooting](Troubleshooting) | Concrete symptoms and what they actually mean |
| [Upstream Fixes](Upstream-Fixes) | What was found in LiteLLM and reported upstream |

---

## Why this one is different from its siblings

9RTKSync and OminiRTkSync **renew** OAuth credentials: their gateway keeps tokens that expire in
a SQLite file on disk, and nobody notices when one dies until a request fails.

LiteLLM has no consumer OAuth. Its credentials are provider API keys in the model catalogue and
virtual keys the proxy issues itself, and its state lives in **Postgres behind Prisma** — there
is no file to read and no `expires_at` to heal. So there is nothing to renew here.

What there is, instead, are three states nobody checks on their own:

**A virtual key expires quietly.** `LiteLLM_VerificationToken.expires` passes, and the first sign
is a request failing. Keys already expired and keys inside the margin are reported apart.

**A provider key in the catalogue may already have been revoked.** The proxy only finds out when
it tries to use it. Each key is asked of its own provider instead of being assumed healthy.

**An incoherent limit is accepted without complaint.** A team capped at `rpm_limit=60` accepts a
key declaring `rpm_limit=600` — verified against a real proxy. The effective limit is always the
most restrictive on the path, so the larger number exists only in the record. See
[Rate Limit Coherence](Rate-Limit-Coherence).

---

## Read-only, on purpose

The tool reports and validates. It never writes a limit, a key or a model: those are the
operator's call, through LiteLLM's own screens. It reads exclusively through the
**administrative API**, never the database — the Prisma schema changes between releases, and
writing to the table would skip the invariants the proxy enforces.

---

## The sibling projects

| Project | Gateway | Panel port (host) |
| :--- | :--- | :--- |
| [9RTKSync](https://github.com/pathbit/9RTKSync) | [9Router](https://github.com/decolua/9router) | `9091` |
| [OminiRTkSync](https://github.com/pathbit/OminiRTkSync) | [OmniRoute](https://github.com/diegosouzapw/OmniRoute) | `9092` |
| **LiteLlmRTKSync** | [LiteLLM](https://github.com/BerriAI/litellm) | `9093` |

All three listen on **port 9090 inside their container**; the published host ports differ so the
three can run side by side. They share the dashboard, logging, authentication and configuration
contract; the data layer and what each one checks are what differ.

---

---

## Signing in to the dashboard

| | |
| :--- | :--- |
| **Address** | `http://localhost:9093` |
| **User** | `admin` — or whatever `DASHBOARD_USER` says |
| **Password** | the value you set in `DASHBOARD_PASSWORD` |

There is **no factory password**: a fixed one shipped in an image is public the
moment the image is. Set yours in `.env` before bringing the stack up.

Brought it up without setting one? The container generated a recovery
credential on first boot — read it and sign in as `admin`, then set a real
password on the screen:

```bash
docker exec litellm-rtksync cat /app/data/.dashboard_recovery
```

Full detail in [Authentication](Authentication).

## License

MIT — see [LICENSE](https://github.com/pathbit/LiteLlmRTKSync/blob/master/LICENSE).

Built by [Pathbit](https://pathbit.co/).
