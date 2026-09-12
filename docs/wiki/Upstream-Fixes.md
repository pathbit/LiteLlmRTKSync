# Upstream Fixes

What was found in LiteLLM while building this synchronizer, and what was reported back.

The rule we follow: describe the behaviour, show how to reproduce it, and never restate a
provider's policy we cannot verify.

---

## A key limit above its team's limit is accepted silently

**Reported:** [BerriAI/litellm#40866](https://github.com/BerriAI/litellm/issues/40866)

**What happens.** A virtual key can declare `rpm_limit=600` while belonging to a team capped at
`rpm_limit=60`. The key is created, the API returns it, the UI shows 600. Every request is
measured against 60.

**Why it matters.** The most restrictive ceiling on the path always wins, which is correct. What
is missing is any signal that the two numbers disagree. Whoever configured the key believes they
provisioned 600, and the difference only surfaces as unexplained throttling — at which point the
record and the behaviour say different things and nobody knows which to trust.

**Reproduction**, against a real proxy:

```bash
curl -s -X POST "$LITELLM_URL/team/new" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"team_alias":"time-restrito","rpm_limit":60}'

curl -s -X POST "$LITELLM_URL/key/generate" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"key_alias":"chave-acima-do-teto","team_id":"<id>","rpm_limit":600}'
```

The second call succeeds. No warning, no field flagged.

**What this project does about it.** Reports the disagreement, field by field, naming both values
and which one applies. It never corrects the number — lowering a limit changes what a real user
can do, and that is the operator's decision. See
[Rate Limit Coherence](Rate-Limit-Coherence).

---

## `/model/info` answers 500 on an installation with no models

**What happens.** A freshly created proxy with no model registered returns **HTTP 500** from
`/model/info`, rather than 200 with an empty list.

**Why it matters for a client.** "No models" is a perfectly normal state for an installation
somebody just created. A client that treats any 500 as a failure breaks its whole cycle on the
one installation most likely to be inspected first.

**What this project does about it.** `list_models()` returns an empty list on 404 and 500, and
each section of the cycle degrades independently — a failing `/model/info` does not stop the key
and team checks from reporting.

---

## `/key/list` returns strings unless asked for objects

**What happens.** Without `return_full_object=true`, the endpoint returns key identifiers as bare
strings. With it, full objects including `expires`, the limit fields and the team association.

**Why it matters.** Every check in this project needs the object. A client written against the
default response silently has nothing to compare and reports a healthy installation because it
found no problems — having been unable to look for any.

**What this project does about it.** Always sends the parameter, and paginates to the end rather
than assuming the first page is the whole set.

---

## Not reported, and why

**No consumer OAuth to renew.** This is not a bug; it is a design difference from the sibling
gateways, and the reason this project checks instead of renewing. It is documented in
[Home](Home) so nobody arrives expecting `expires_at` healing that cannot exist here.

**`0` meaning "no limit".** Idiomatic in LiteLLM and internally consistent. We handle it; there
is nothing to fix upstream.
