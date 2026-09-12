# Rate Limit Coherence

This is the rule the project exists for.

LiteLLM enforces limits at three levels — the **key**, the **team** it belongs to, and whatever
ceiling the platform itself declares. At request time the **most restrictive** of them wins.
What it does *not* do is check that they agree when you configure them.

## What was verified

Against a real proxy, not a mock:

```
team 'time-restrito'      rpm_limit = 60
key  'chave-acima-do-teto' rpm_limit = 600   ← accepted without complaint
```

The key is created, the record says 600, and the operator believes they have 600. Every request
is measured against 60. Nothing in the proxy, the UI or the API says otherwise. The number in
the record is simply fiction.

Reported upstream: [BerriAI/litellm#40866](https://github.com/BerriAI/litellm/issues/40866).

## The rule

One sentence, applied field by field:

> **No level may declare a value greater than the level above it.**
> **key ≤ team ≤ platform default.**

Four fields carry a ceiling, and each is checked independently:

| Field | What it caps |
| :--- | :--- |
| `rpm_limit` | Requests per minute |
| `tpm_limit` | Tokens per minute |
| `max_parallel_requests` | Concurrent requests |
| `max_budget` | Spend over the budget period |

A key may declare a *smaller* value than its team — that is a deliberate tightening, and it is
never reported. The finding is only raised when the declared value is **greater** than the
ceiling that will actually apply.

## Zero is not a ceiling of zero

In LiteLLM, `0` in a limit field means **no limit**, not "zero allowed". Treating it as a ceiling
of zero would raise a finding against every key in the installation. The comparison skips it.

## The platform ceiling

The top level comes from the environment, because LiteLLM has no field for it:

```bash
PLATFORM_RPM_LIMIT=600
PLATFORM_TPM_LIMIT=200000
PLATFORM_MAX_BUDGET=500
```

Leaving them empty is **a choice, not an error**. The panel reports *no cap declared* for that
field and moves on to compare key against team, which it can always do.

This is where the tenant-versus-platform requirement lands: the per-tenant default is the team
ceiling, the platform-wide one is `PLATFORM_*`, and a tenant is never allowed to declare above
the platform.

## What a finding looks like

Each finding names the field, both values and the consequence — never just "incoherent":

```
[limite] rpm_limit: a chave 'chave-acima-do-teto' declara 600, acima do teto de 60
do time 'time-restrito' — na prática vale o teto, não o que está na chave
```

A key with no alias is identified by its token, **masked** to the last six characters. A key is a
credential; printing it whole to diagnose a limit would trade one problem for a worse one.

## In CI

`--status` exits `1` when it finds an incoherent limit:

```bash
litellmrtksync --status --url http://127.0.0.1:8083 || echo "revisar limites"
```

That makes it usable as a gate: a pull request that introduces a key above its team's ceiling
fails the pipeline instead of being discovered in production.

## What it does not do

It never corrects the number. Lowering a key's limit changes what a real user can do, and that
decision belongs to whoever runs the platform — through LiteLLM's own screens.
