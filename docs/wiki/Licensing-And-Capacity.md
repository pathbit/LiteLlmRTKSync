# Licensing and Capacity

*(Versão em português ao final.)*

"We are twelve developers — how many licences do I buy?" is the question everyone
arrives with, and in this repository it is **the wrong shape of question**.

The siblings multiplex consumer subscriptions: for them a licence is an account,
with a holder, and the count is the answer. LiteLLM multiplexes **virtual keys
issued over an API credential you already own**. Nobody buys "one more licence"
here. You split a ceiling you already bought, and you move up a tier when it gets
tight. What you size is not a headcount — it is `rpm_limit`, `tpm_limit` and a
budget, arranged in a chain that this tool exists to check:

```
Σ (keys of a team)  ≤  team ceiling  ≤  PLATFORM_*  ≤  the provider's API tier
```

The bottom link is what [Rate Limit Coherence](Rate-Limit-Coherence) verifies and
reports. The top link is **published**, so unlike the subscription path this one
resolves to real numbers. This page is how you get from a headcount to those
numbers, and back to the panel that tells you whether you got it right.

---

## What is published, and what is not

**Consumer subscriptions publish no absolute capacity.** Anthropic publishes a
window and a multiplier — *"Your session-based usage limit will reset every five
hours"*, *"Max 5x provides five times more usage per session than the Pro plan"* —
and never a number
`[FONTE: https://support.claude.com/en/articles/11049741-what-is-the-max-plan — read 2026-09-12]`.
Its limits page says usage depends on *"the length and complexity of your
conversations, the features you use, which Claude model you're chatting with, and
the effort level you've selected"*
`[FONTE: https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work — read 2026-09-12]`.
That is why a table reading "1 licence = 4 devs" cannot be written honestly by
anyone, here or in the sibling wikis.

**The API path is the exception, and it is this project's path**
`[FONTE: https://platform.claude.com/docs/en/api/rate-limits — read 2026-09-12]`:

| Tier | RPM (Opus 5 / Sonnet 5) | ITPM | OTPM | Monthly spend cap |
| :--- | ---: | ---: | ---: | ---: |
| Start | 1,000 | 2,000,000 | 400,000 | US$ 500 |
| Build | 5,000 | 5,000,000 | 1,000,000 | US$ 1,000 |
| Scale | 10,000 | 10,000,000 | 2,000,000 | US$ 200,000 |

Two caveats from the same page matter to a team onboarding on a single day: new
organizations may start in the **Evaluation tier, with limits below the standard
limits** shown there, and a sharp increase in usage triggers **acceleration
limits** (429). The tier chosen below is the steady-state tier, not the first
day's.

**That table is Anthropic's, and LiteLLM is multi-provider on purpose** — every
entry in `/model/info` carries its own `provider`
`[FONTE: src/litellm_rtksync/models.py:164]`. For each other provider in your
catalogue the top of the chain is *that* provider's published rate-limit page,
`[A VERIFICAR: read it and cite URL + date, the way this page cites Anthropic's]`.
Do not assume symmetry between vendors; only Anthropic's was read here.

## The one rule that changes the arithmetic

Quoted exactly from the same page: *"For most Claude models, only uncached input
tokens count toward your ITPM rate limits."* `input_tokens` counts,
`cache_creation_input_tokens` counts, `cache_read_input_tokens` **does not**. In
the history measured below, **98.2% of all tokens moved are cache reads**, and the
ratio between median total input and median input that counts is **23.6×** — on
this corpus, at this instant; the ratio moves with the workload, so measure your
own. Size this gateway's path by summing total tokens and you buy something like
twenty times the tier you need.

The rule is from the rate-limit page of the **API**, which is exactly where
LiteLLM sits — so here demand is measured in uncached input. The subscription
meters of the sibling gateways promise nothing of the sort, which is why their
pages use total tokens instead.

## The demand, measured

`[FONTE: tools/measure_agent_usage.py over ~/.claude/projects/**/*.jsonl, one
machine, snapshot taken 2026-09-13T02:11:02Z]`

**Read the block below as a snapshot, not as a constant.** The corpus is one
operator's own Claude Code history, and it grows with every session: running the
script again on this same machine gives different numbers — it gave a different
answer on every single run while this page was being rewritten. So the numbers
here are dated to the second and
tied to one machine, and **the only profile that should size your chain is the
one you get running the script yourself**. What reproduces across machines is the
method; the values never were going to.

This is the script's complete output at that instant — every line it prints, none
edited:

```
== historico inteiro (sem filtro de sessao) ==
linhas type=assistant com usage             : 36200
message.id distintos                        : 16698
fator de inflacao por contar linha          : 2.17x

== amostra (sessoes com >= 10 turnos e >= 300s ativos) ==
sessoes analisadas                          : 134
turnos unicos (dedup por message.id)        : 7441
T_in  entrada que conta p/ ITPM  mediana    : 3745
T_in  entrada que conta p/ ITPM  p90        : 7668
T_out saida                      mediana    : 732
T_out saida                      p90        : 1351
T_cache leitura de cache         mediana    : 83413
T_tot entrada total (conta+cache) mediana   : 88353
T_tot entrada total (conta+cache) p90       : 205437
R_h   requisicoes por hora ativa mediana    : 213
R_h   requisicoes por hora ativa p90        : 342
fracao de leitura de cache no total         : 98.2%
razao entrada total / entrada que conta     : 23.6x
pico de sessoes simultaneas                 : 13
```

**The two headings are two different universes, and mixing them is the easiest
mistake to make here.** `historico` walks every line of the history, unfiltered.
`amostra` keeps only sessions with at least 10 turns and 300 active seconds,
because a three-message session says nothing about pace, and pace is what the
rest of the block measures. That is why the 7,441 turns of the sample and the
16,698 distinct ids of the history are **not** the same quantity — an earlier
version of this page printed both as if they were, and the reader had no way to
tell which one was wrong. Neither was; the filter was simply never declared. It
lives in `MIN_TURNS_PER_SESSION` and `MIN_ACTIVE_SECONDS`, and the script now
prints it in the heading.

Three caveats travel with those numbers, and none of them is decoration:

1. **Deduplication is not optional.** One API response is written to several
   `type: "assistant"` lines — the text plus each tool block — repeating the same
   `message.id` and the same `usage`. Counting lines inflates everything by
   **2.17×** — `36,200 lines against 16,698 distinct ids`, the three lines under
   `historico` above, printed by the same script in the same run. Any
   consumption figure derived from Claude Code JSONL without dedup by
   `message.id` is wrong by a factor of two.
2. **23.6× is a ratio of two medians**, not the median of the ratios: an order of
   magnitude, not accounting.
3. **The machine measured runs orchestration with subagents.** Read
   `R_h ≈ 213 req/h` as *one agent session* — a request every ~17 s — not as *one
   developer typing*. Interactive CLI use without subagents measures lower. Run
   the script on your own machine before trusting the table below.

## Sizing the chain — the table that does resolve

`[FONTE: tools/sizing.py, run 2026-09-13, with the measured profile above and the
published tier table]`

`c` is the concurrency factor: the fraction of the team requesting at the same
moment. **`c` and the slack `F` are arbitrated, not measured.** The 0.4 / 0.6 /
1.0 and the 30% below were chosen, and are varied across the rows on purpose, to
show how far they move the answer — `tools/sizing.py` is the place where those two
numbers were *picked*, so citing it as their source would be circular. Measure
your own `c` in the spend log, the way the panel section below describes, and
replace them. The arithmetic is per minute, because a per-minute ceiling blows
before a per-window one does:

```
RPM_exigido  = N × c × R_h / 60 × (1 + F)
ITPM_exigido = RPM_exigido × T_in        (uncached input — see the rule above)
OTPM_exigido = RPM_exigido × T_out
```

**Median profile** (`R_h`=213, `T_in`=3,745, `T_out`=732), slack `F` = 30%:

| devs | c | sessions | RPM | ITPM | OTPM | smallest tier |
| ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| 3 | 0.4 | 1.2 | 6 | 20,740 | 4,054 | Start |
| 3 | 0.6 | 1.8 | 8 | 31,110 | 6,081 | Start |
| 3 | 1.0 | 3.0 | 14 | 51,850 | 10,135 | Start |
| 12 | 0.4 | 4.8 | 22 | 82,959 | 16,215 | Start |
| 12 | 0.6 | 7.2 | 33 | 124,439 | 24,323 | Start |
| 12 | 1.0 | 12.0 | 55 | 207,398 | 40,538 | Start |
| 40 | 0.4 | 16.0 | 74 | 276,531 | 54,051 | Start |
| 40 | 0.6 | 24.0 | 111 | 414,796 | 81,076 | Start |
| 40 | 1.0 | 40.0 | 185 | 691,327 | 135,127 | Start |

**p90 profile** (`R_h`=342, `T_in`=7,668, `T_out`=1,351), same slack:

| devs | c | sessions | RPM | ITPM | OTPM | smallest tier |
| ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| 3 | 0.4 | 1.2 | 9 | 68,184 | 12,013 | Start |
| 3 | 0.6 | 1.8 | 13 | 102,276 | 18,020 | Start |
| 3 | 1.0 | 3.0 | 22 | 170,460 | 30,033 | Start |
| 12 | 0.4 | 4.8 | 36 | 272,735 | 48,052 | Start |
| 12 | 0.6 | 7.2 | 53 | 409,103 | 72,079 | Start |
| 12 | 1.0 | 12.0 | 89 | 681,839 | 120,131 | Start |
| 40 | 0.4 | 16.0 | 119 | 909,118 | 160,175 | Start |
| 40 | 0.6 | 24.0 | 178 | 1,363,677 | 240,262 | Start |
| 40 | 1.0 | 40.0 | 296 | 2,272,795 | 400,436 | **Build** |

Both tables are the script's full output — nine rows per profile, nothing
selected out.

Three things to read off it:

- **Forty developers on the median profile still fit in Start.** Only the extreme
  corner — forty devs, all concurrent, on the p90 profile — asks for Build, and
  what pushes it there is **ITPM** (2.27 M against 2.00 M) with **OTPM** crossing
  by a hair right behind it (400,436 against 400,000). RPM is not close: 296
  against 1,000.
- **The burst ceiling is rarely what hurts.** At `c` = 0.6 on the median profile,
  Start's 1,000 RPM covers 120× the demand of three devs, 30× of twelve and 9× of
  forty.
- **`c` and `N` enter the formula the same way** — `U_sim` is their product, so
  doubling either doubles the demand. What makes `c` worth measuring is not extra
  leverage in the arithmetic; it is that you already know `N` exactly and are
  guessing `c`. Forty devs at `c` = 0.4 and twelve at `c` = 1.0 land in the same
  tier, which is a fact about the guess, not about the formula.

## From the table to `PLATFORM_*`

This is the whole point of the page. The tier you picked is the top of the chain,
and LiteLLM has no field for it — so it comes from the environment
`[FONTE: src/litellm_rtksync/config.py:89-91]`:

```bash
PLATFORM_RPM_LIMIT=1000
PLATFORM_TPM_LIMIT=2000000
PLATFORM_MAX_BUDGET=500
```

**But check the unit before you copy the number.** The provider's ITPM counts
uncached *input*; LiteLLM's `tpm_limit` counts something else, and which one
depends on the proxy you are running. Verified by reading the LiteLLM upstream
vendored at `tmp/litellm`, commit `2618071`, which declares version **1.102.0** at
`pyproject.toml:3` — read 2026-09-12. The line numbers below are from that tree,
and resolve upstream at `https://github.com/BerriAI/litellm/blob/2618071/`:

- The default limiter is **v3** — the hook registry binds
  `parallel_request_limiter` to the v3 handler
  `[FONTE: litellm/proxy/hooks/__init__.py:21]`. Its rate-limit type defaults to
  `total` `[FONTE: litellm/proxy/hooks/parallel_request_limiter_v3.py:4002]`, and
  for `input` and `total` it **subtracts cached tokens** — *"providers like AWS
  Bedrock don't count cached tokens toward rate limits"*
  `[FONTE: same file, lines 3715-3761]`. So `tpm_limit` ≈ uncached input **plus
  output**: on the measured profile that is 4,477 tokens per request against the
  3,745 the provider's ITPM counts, and setting `PLATFORM_TPM_LIMIT` to the tier's
  ITPM is conservative by about **20%** — `tools/sizing.py` prints that comparison
  for both profiles. Fine.
- The **legacy** limiter counts `usage.total_tokens` whole, cache reads included
  `[FONTE: litellm/proxy/hooks/parallel_request_limiter.py:37-41]`. It comes back
  only when `LEGACY_MULTI_INSTANCE_RATE_LIMITING` is true
  `[FONTE: litellm/proxy/hooks/__init__.py:31]`. On that path `tpm_limit` and ITPM
  differ by the full **23.6×** of the cache ratio, and a `PLATFORM_TPM_LIMIT`
  copied straight from the tier throttles you at roughly 4% of what you paid for.

Check which one your proxy runs before copying the number. `[A VERIFICAR: your
LiteLLM version and that feature flag in your own deployment]`

Then split downward — team ceilings under the platform, key ceilings under their
team — and use `--status` as the gate:

```bash
litellmrtksync --status --url http://127.0.0.1:8083 || echo "revisar limites"
```

**Declaring `PLATFORM_*` above your tier is writing fiction into the environment**
— the same failure this project exists to catch, one level up. A key that claims
600 against a team capped at 60 gets 60; a platform that claims 5,000 RPM against
a Start tier gets 1,000, and in both cases the record says otherwise and nothing
complains. The four fields compared live in `CAMPOS_DE_TETO`
`[FONTE: src/litellm_rtksync/gateway.py:246]`.

Leaving `PLATFORM_*` empty is a **choice, not an error** — the panel reports *no
cap declared* and goes on comparing key against team. But an empty platform level
means the sizing above was never written down anywhere the tool can check. Full
variable reference in [Configuration](Configuration).

## What this synchronizer shows you about it

Capacity questions have a specific screen: the **virtual keys** table on the
[Dashboard](Dashboard) and the detail modal behind each row's `(i)` button. Field
by field, with the names as the code writes them:

| Question | Field | Where it is read | Panel label |
| :--- | :--- | :--- | :--- |
| What did this key promise per minute? | `rpmLimit`, `tpmLimit` | `models.py:129-130` | *RPM limit*, *TPM limit* (`i18n.py:113-114`) |
| What is its ceiling in money? | `maxBudget` | `models.py:83-90` | *Budget ceiling* (`i18n.py:115`) |
| How much has it burned? | `spend` | `models.py:76-81` | *Spend* (`i18n.py:110`) |
| Did it hit the ceiling? | `healthStatus` = `over_budget` | `models.py:92-95` | *Budget exhausted* (`i18n.py:100`) |
| How many keys hit it? | `summarize` | `models.py:203-210` | header counter, `/api/status` |
| Is the declared chain coherent? | `capValue`, `capLevel`, `severity` | `gateway.py:285-287` | *Limit findings* |

Four readings that are easy to get wrong:

**`over_budget` is the saturation signal, and it is a fact, not a forecast.** It
fires when `spend >= max_budget` `[FONTE: src/litellm_rtksync/models.py:92-95]`,
constant `SAUDE_ESTOURADA` `[FONTE: src/litellm_rtksync/models.py:12]`, badge
rendered from `render.py:44`. A key that reaches it every budget period is
under-sized; a key that never comes close is slack you can hand to someone else.
That count, per key per period, is the only evidence that closes the loop — the
rest is projection.

**A budget of `0` or absent reads *not declared*, never "unlimited."**
`max_budget` returns `None` for anything not strictly positive
`[FONTE: src/litellm_rtksync/models.py:83-90]` — the same reasoning as *zero is
not a ceiling of zero* in [Rate Limit Coherence](Rate-Limit-Coherence). An absent
value is missing data, not a promise.

**`SEVERIDADE_SEM_TETO` says *this key* has nothing above it — read it precisely.**
A key's cap level is its team when it has one, and the platform default only when
it has none `[FONTE: src/litellm_rtksync/gateway.py:361-369]`; the finding fires
when that level declares nothing for the field
`[FONTE: src/litellm_rtksync/gateway.py:376-382]`. A key inside a capped team will
**not** raise it, however empty `PLATFORM_*` is. The real cost of an empty
platform level is quieter, and it is in the second loop: a team is compared
against the platform only when **both** values exist, otherwise the field is
skipped `[FONTE: src/litellm_rtksync/gateway.py:390-401]`. With `PLATFORM_*` unset,
a team declaring 5,000 RPM on a Start tier is never examined at all. Sizing a tier
and not writing it into the environment does not merely omit a warning; it
switches off the check that would have caught you.

**And the boundary: the panel shows declared ceilings and cumulative spend — it
does not show `R_h` or `T_in`.** The client reads `/key/list`, `/team/list`,
`/model/info`, `/health`, `/credentials` and `/health/liveliness`, and nothing
else `[FONTE: src/litellm_rtksync/gateway.py:114-201]`; see
[Architecture](Architecture) for why the administrative API is the only surface it
touches. For per-hour rate and per-request tokens, query LiteLLM's own spend log
grouped by hour — each entry already carries key, model and tokens — or run
`tools/measure_agent_usage.py` against your agent history. The same log is where
`c` comes from — the one input above that is arbitrated rather than measured:
count **distinct keys active in a minute, divided by `N`**, over a working week.
Replacing that guess with a measurement is the single most valuable thing you can
do to the table above, because `N` you already know exactly. This is
a boundary of the tool, not a gap in it: it validates what was declared, and
declaring is what you do after measuring.

## Two clocks, and only one scales with headcount

| | Key validity | Capacity |
| :--- | :--- | :--- |
| Field | `expiresAt`, `remainingSeconds` (`models.py:122-123`) | `max_budget`, `rpm_limit`, `tpm_limit` |
| State | `expiring_soon`, `expired` (`models.py:9-10`) | `over_budget` (`models.py:12`) |
| Symptom | the request is refused as unauthenticated | 429, or spend frozen at the ceiling |
| Fix | reissue the key | wait for the budget period, or raise the ceiling |
| Scales with team size? | **No** | **Yes** |

A key expiring at 2am is not a capacity problem and buying tier does not fix it.
Both live in the same modal, which is precisely why they get confused.

## The constraint that must not stay buried

Everything above sizes **technical capacity**. It does not authorise sharing a
subscription, and the provider's text is explicit
`[FONTE: https://code.claude.com/docs/en/legal-and-compliance — read 2026-09-12]`:
*"OAuth authentication is intended exclusively for purchasers of Claude Free, Pro,
Max, Team, and Enterprise subscription plans…"* and *"Customers may not pay for,
resell, or intermediate Claude usage on their end users' behalf. Each end user
must authenticate with their own Anthropic API key, Claude subscription plan
credentials, or 3P inference provider credential."*

For the subscription path this closes the question without measuring anything:
**twelve devs, twelve subscriptions**, each bought and authenticated by its holder.
That is the sibling gateways' answer, and their pages carry it.

**This gateway is the path the same text explicitly permits**: *"This does not
restrict how customers provision and manage their own API keys … for use by the
customer's own authorized users."* An organisation API key, billed to the
organisation, split into virtual keys per team and per developer, is exactly the
arrangement described — and the chain above is how you keep the split honest.

One line on egress, because it is the siblings' shape and not this one's: what
draws attention is many accounts leaving through one address, and a LiteLLM
instance has one egress address for one credential. [Egress Testing](Egress-Testing)
is the bench that proves where the traffic actually leaves from.

## Reproducing all of it

```bash
python3 tools/measure_agent_usage.py    # your demand profile
python3 tools/sizing.py                 # the tables above, with your constants
```

`tools/sizing.py` separates its inputs by provenance, and the separation is the
point: `PROFILES` is **measured** (a snapshot copied from the run above, stamped
in `PROFILE_SOURCE`), `API_TIERS` is **published**, and `TEAM_SIZES`,
`CONCURRENCY` and `SLACK` are **arbitrated** — the knobs. Replace the profile
with your own output and the tables recalculate.

Both scripts are versioned on purpose — a number without a script that reproduces
it becomes, given enough time, an invented number. Note what that buys and what
it does not: it makes the **method** reproducible, not the values. The demand
profile is a snapshot of one growing corpus and will never come back the same,
which is why it is dated to the second above instead of being presented as a
constant. The published tier table and the sizing arithmetic, on the other hand,
reproduce exactly.

`tools/measure_agent_usage.py` reads **only** numeric `usage` fields, `message.id`
and `timestamp`. No conversation content is read, aggregated or printed.

And to inspect what was actually injected into the service, always run
`docker compose config` with `--no-interpolate` — without it the command prints
`LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY` and `DASHBOARD_PASSWORD` to your
terminal:

```bash
docker compose -f docker-compose.example.yml config --no-interpolate
```

---

# Em português

"Somos doze devs, quantas licenças eu compro?" é a pergunta com que todo mundo
chega, e neste repositório ela tem **a forma errada**.

Os irmãos multiplexam assinatura de consumo: para eles licença é conta, com
titular, e a contagem é a resposta. O LiteLLM multiplexa **chaves virtuais sobre
uma credencial de API que já é sua**. Ninguém compra "mais uma licença" aqui; você
reparte um teto que já comprou e sobe de tier quando ele aperta. O que se
dimensiona não é headcount — é `rpm_limit`, `tpm_limit` e orçamento, numa cadeia
que este projeto existe para conferir:

```
Σ (chaves de um time)  ≤  teto do time  ≤  PLATFORM_*  ≤  tier da API do fornecedor
```

O elo de baixo é o que [Coerência de limites](Rate-Limit-Coherence) verifica e
denuncia. O elo de cima é **publicado** — por isso, ao contrário do caminho de
assinatura, este fecha com número. Esta página vai do headcount até esses números,
e de volta ao painel que diz se você acertou.

## O que é publicado e o que não é

**Assinatura de consumo não publica capacidade absoluta.** A Anthropic publica
janela e multiplicador — *"reset every five hours"*, *"Max 5x provides five times
more usage per session than the Pro plan"* — e nunca um número
`[FONTE: https://support.claude.com/en/articles/11049741-what-is-the-max-plan — lido em 12/09/2026]`.
A página de limites diz que o consumo depende do tamanho e da complexidade da
conversa, dos recursos usados, do modelo e do nível de esforço
`[FONTE: https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work — lido em 12/09/2026]`.
É por isso que uma tabela "1 licença = 4 devs" não pode ser escrita com
honestidade por ninguém.

**O caminho de API é a exceção, e é o caminho deste projeto**
`[FONTE: https://platform.claude.com/docs/en/api/rate-limits — lido em 12/09/2026]`:

| Tier | RPM (Opus 5 / Sonnet 5) | ITPM | OTPM | Teto de gasto mensal |
| :--- | ---: | ---: | ---: | ---: |
| Start | 1.000 | 2.000.000 | 400.000 | US$ 500 |
| Build | 5.000 | 5.000.000 | 1.000.000 | US$ 1.000 |
| Scale | 10.000 | 10.000.000 | 2.000.000 | US$ 200.000 |

Duas ressalvas da mesma página, para um time que entra de uma vez: organizações
novas podem começar no **Evaluation tier, com limites abaixo dos da tabela**, e um
salto brusco de uso aciona **acceleration limits** (429). O tier escolhido abaixo
é o de regime, não o do primeiro dia.

**Essa tabela é da Anthropic, e o LiteLLM é multi-provedor de propósito** — cada
entrada de `/model/info` carrega o seu `provider`
`[FONTE: src/litellm_rtksync/models.py:164]`. Para cada outro provedor do seu
catálogo, o topo da cadeia é a página de rate limits *daquele* provedor,
`[A VERIFICAR: leia e cite URL + data, como esta página cita a da Anthropic]`. Não
presuma simetria entre fornecedores: aqui só a Anthropic foi lida.

## A regra que muda a conta inteira

Literalmente, da mesma página: *"For most Claude models, only uncached input
tokens count toward your ITPM rate limits."* `input_tokens` conta,
`cache_creation_input_tokens` conta, `cache_read_input_tokens` **não conta**. No
histórico medido abaixo, **98,2% de todos os tokens trafegados são leitura de
cache**, e a razão entre a mediana da entrada total e a mediana da entrada que
conta é **23,6×** — neste corpus, neste instante; a razão se move com a carga de
trabalho, então meça a sua. Quem dimensiona este caminho somando cache lido compra
algo como vinte vezes o tier de que precisa.

A regra é da página de rate limits **da API** — exatamente onde o LiteLLM está.
Os medidores de assinatura dos irmãos não prometem nada disso, e por isso as
páginas deles usam token total.

## A demanda, medida

`[FONTE: tools/measure_agent_usage.py sobre ~/.claude/projects/**/*.jsonl, uma
máquina, instantâneo de 13/09/2026 02:11:02 UTC]`

**Leia o bloco abaixo como instantâneo, não como constante.** O corpus é o
histórico do Claude Code de um operador só, e ele cresce a cada sessão: rodar o
script de novo na mesma máquina dá outro número — deu resultado diferente em
todas as rodadas feitas enquanto esta página era reescrita. Por isso os valores
estão datados ao segundo e presos a uma
máquina, e **o único perfil que deve dimensionar a sua cadeia é o que você obtiver
rodando o script**. O que reproduz entre máquinas é o método; os valores nunca
iriam reproduzir.

Esta é a saída completa do script naquele instante — todas as linhas que ele
imprime, nenhuma editada:

```
== historico inteiro (sem filtro de sessao) ==
linhas type=assistant com usage             : 36200
message.id distintos                        : 16698
fator de inflacao por contar linha          : 2.17x

== amostra (sessoes com >= 10 turnos e >= 300s ativos) ==
sessoes analisadas                          : 134
turnos unicos (dedup por message.id)        : 7441
T_in  entrada que conta p/ ITPM  mediana    : 3745
T_in  entrada que conta p/ ITPM  p90        : 7668
T_out saida                      mediana    : 732
T_out saida                      p90        : 1351
T_cache leitura de cache         mediana    : 83413
T_tot entrada total (conta+cache) mediana   : 88353
T_tot entrada total (conta+cache) p90       : 205437
R_h   requisicoes por hora ativa mediana    : 213
R_h   requisicoes por hora ativa p90        : 342
fracao de leitura de cache no total         : 98.2%
razao entrada total / entrada que conta     : 23.6x
pico de sessoes simultaneas                 : 13
```

**Os dois cabeçalhos são dois universos diferentes, e misturá-los é o erro mais
fácil de cometer aqui.** O `historico` percorre todas as linhas do histórico, sem
filtro. A `amostra` só fica com sessões de pelo menos 10 turnos e 300 segundos
ativos, porque sessão de três mensagens não diz nada sobre ritmo, e é ritmo o que
o resto do bloco mede. Por isso os 7.441 turnos da amostra e os 16.698 ids
distintos do histórico **não** são a mesma grandeza — uma versão anterior desta
página publicava os dois como se fossem, e o leitor não tinha como saber qual
estava errado. Nenhum estava; o filtro é que nunca foi declarado. Ele vive em
`MIN_TURNS_PER_SESSION` e `MIN_ACTIVE_SECONDS`, e agora o script o imprime no
cabeçalho.

Três ressalvas que viajam junto, e nenhuma delas é enfeite:

1. **A deduplicação não é opcional.** Uma resposta da API é gravada em várias
   linhas `type: "assistant"` — texto e cada bloco de ferramenta — repetindo o
   mesmo `message.id` e o mesmo `usage`. Contar linhas infla tudo em **2,17×** —
   `36.200 linhas contra 16.698 ids distintos`, as três linhas sob `historico` na
   saída acima, impressas pelo mesmo script na mesma rodada. Qualquer número de
   consumo tirado do JSONL do Claude Code sem deduplicar por `message.id` está
   errado por um fator dois.
2. **23,6× é razão entre duas medianas**, não a mediana das razões: serve para
   ordem de grandeza, não para contabilidade.
3. **A máquina medida roda orquestração com subagentes.** `R_h ≈ 213 req/h` é
   **sessão de agente** — uma requisição a cada ~17 s — e não "um dev digitando".
   Uso interativo sem subagentes mede menos. Rode o script no seu ambiente antes
   de confiar na tabela abaixo.

## Dimensionando a cadeia — a tabela que fecha

`[FONTE: tools/sizing.py, rodado em 13/09/2026, com o perfil medido acima e a
tabela publicada de tiers]`

`c` é o fator de concorrência: a fração do time requisitando ao mesmo tempo. **`c`
e a folga `F` são arbitrados, não medidos.** Os 0,4 / 0,6 / 1,0 e os 30% abaixo
foram escolhidos, e estão variados nas linhas de propósito, para mostrar o quanto
deslocam a resposta — o `tools/sizing.py` é o lugar onde esses dois números foram
*arbitrados*, então citá-lo como fonte deles seria circular. Meça o seu `c` no log
de gasto, do jeito que a seção do painel descreve, e troque. A conta é por minuto,
porque teto por minuto estoura antes de teto por janela:

```
RPM_exigido  = N × c × R_h / 60 × (1 + F)
ITPM_exigido = RPM_exigido × T_in        (entrada não-cacheada)
OTPM_exigido = RPM_exigido × T_out
```

**Perfil mediana** (`R_h`=213, `T_in`=3.745, `T_out`=732), folga `F` = 30%:

| devs | c | sessões | RPM | ITPM | OTPM | tier mínimo |
| ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| 3 | 0,4 | 1,2 | 6 | 20.740 | 4.054 | Start |
| 3 | 0,6 | 1,8 | 8 | 31.110 | 6.081 | Start |
| 3 | 1,0 | 3,0 | 14 | 51.850 | 10.135 | Start |
| 12 | 0,4 | 4,8 | 22 | 82.959 | 16.215 | Start |
| 12 | 0,6 | 7,2 | 33 | 124.439 | 24.323 | Start |
| 12 | 1,0 | 12,0 | 55 | 207.398 | 40.538 | Start |
| 40 | 0,4 | 16,0 | 74 | 276.531 | 54.051 | Start |
| 40 | 0,6 | 24,0 | 111 | 414.796 | 81.076 | Start |
| 40 | 1,0 | 40,0 | 185 | 691.327 | 135.127 | Start |

**Perfil p90** (`R_h`=342, `T_in`=7.668, `T_out`=1.351), mesma folga:

| devs | c | sessões | RPM | ITPM | OTPM | tier mínimo |
| ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| 3 | 0,4 | 1,2 | 9 | 68.184 | 12.013 | Start |
| 3 | 0,6 | 1,8 | 13 | 102.276 | 18.020 | Start |
| 3 | 1,0 | 3,0 | 22 | 170.460 | 30.033 | Start |
| 12 | 0,4 | 4,8 | 36 | 272.735 | 48.052 | Start |
| 12 | 0,6 | 7,2 | 53 | 409.103 | 72.079 | Start |
| 12 | 1,0 | 12,0 | 89 | 681.839 | 120.131 | Start |
| 40 | 0,4 | 16,0 | 119 | 909.118 | 160.175 | Start |
| 40 | 0,6 | 24,0 | 178 | 1.363.677 | 240.262 | Start |
| 40 | 1,0 | 40,0 | 296 | 2.272.795 | 400.436 | **Build** |

As duas tabelas são a saída inteira do script — nove linhas por perfil, nada
recortado.

Três leituras:

- **Quarenta devs no perfil mediano ainda cabem no tier Start.** Só o canto
  extremo — quarenta, todos simultâneos, no p90 — pede Build, e quem empurra é o
  **ITPM** (2,27 M contra 2,00 M), com o **OTPM** cruzando por um fio logo atrás
  (400.436 contra 400.000). O RPM não chega perto: 296 contra 1.000.
- **O teto de rajada raramente é o que dói.** Com `c` = 0,6 no perfil mediano, os
  1.000 RPM do Start cobrem 120× a demanda de três devs, 30× a de doze e 9× a de
  quarenta.
- **`c` e `N` entram na fórmula do mesmo jeito** — `U_sim` é o produto dos dois,
  então dobrar qualquer um dobra a demanda. O que faz valer a pena medir `c` não é
  alavancagem extra na conta; é que você já sabe `N` exatamente e está chutando
  `c`. Quarenta a `c` = 0,4 e doze a `c` = 1,0 caem no mesmo tier, o que é um fato
  sobre o chute, não sobre a fórmula.

## Da tabela para o `PLATFORM_*`

É o ponto da página. O tier escolhido é o topo da cadeia, e o LiteLLM não tem
campo para ele — então vem do ambiente
`[FONTE: src/litellm_rtksync/config.py:89-91]`:

```bash
PLATFORM_RPM_LIMIT=1000
PLATFORM_TPM_LIMIT=2000000
PLATFORM_MAX_BUDGET=500
```

**Mas confira a unidade antes de copiar o número.** O ITPM do fornecedor conta
entrada *não-cacheada*; o `tpm_limit` do LiteLLM conta outra coisa, e qual depende
do proxy que você roda. Verificado lendo o upstream do LiteLLM vendorizado em
`tmp/litellm`, commit `2618071`, que declara a versão **1.102.0** em
`pyproject.toml:3` — lido em 12/09/2026. Os números de linha abaixo são daquela
árvore, e resolvem upstream em
`https://github.com/BerriAI/litellm/blob/2618071/`:

- O limitador padrão é o **v3** — o registro de hooks liga
  `parallel_request_limiter` ao handler v3
  `[FONTE: litellm/proxy/hooks/__init__.py:21]`. O tipo de limite cai por padrão
  em `total` `[FONTE: litellm/proxy/hooks/parallel_request_limiter_v3.py:4002]`
  e, para `input` e `total`, ele **subtrai os tokens cacheados** — *"providers
  like AWS Bedrock don't count cached tokens toward rate limits"*
  `[FONTE: mesmo arquivo, linhas 3715-3761]`. Ou seja, `tpm_limit` ≈ entrada
  não-cacheada **mais saída**: no perfil medido são 4.477 tokens por requisição
  contra os 3.745 que o ITPM do fornecedor conta, e pôr o ITPM do tier em
  `PLATFORM_TPM_LIMIT` é conservador em cerca de **20%** — o `tools/sizing.py`
  imprime essa comparação para os dois perfis. Aceitável.
- O limitador **legado** conta `usage.total_tokens` inteiro, com leitura de cache
  dentro `[FONTE: litellm/proxy/hooks/parallel_request_limiter.py:37-41]`. Ele só
  volta quando `LEGACY_MULTI_INSTANCE_RATE_LIMITING` está ligada
  `[FONTE: litellm/proxy/hooks/__init__.py:31]`. Nesse caminho `tpm_limit` e ITPM
  diferem pelos **23,6×** inteiros da razão de cache, e um `PLATFORM_TPM_LIMIT`
  copiado direto do tier estrangula você em cerca de 4% do que foi pago.

Confira qual dos dois o seu proxy roda antes de copiar o número.
`[A VERIFICAR: a sua versão do LiteLLM e essa flag na sua implantação]`

Depois reparta para baixo — teto de time abaixo da plataforma, teto de chave
abaixo do time — e use `--status` como gate:

```bash
litellmrtksync --status --url http://127.0.0.1:8083 || echo "revisar limites"
```

**Declarar `PLATFORM_*` acima do seu tier é escrever ficção no ambiente** — o
mesmo erro que o projeto existe para pegar, um nível acima. Chave que declara 600
sob um time de 60 recebe 60; plataforma que declara 5.000 RPM sobre um tier Start
recebe 1.000 — e nos dois casos o cadastro diz outra coisa e nada reclama. Os
quatro campos comparados estão em `CAMPOS_DE_TETO`
`[FONTE: src/litellm_rtksync/gateway.py:246]`.

Deixar `PLATFORM_*` vazio é **uma escolha, não um erro**: o painel reporta *sem
teto declarado* e segue comparando chave contra time. Mas plataforma vazia
significa que o dimensionamento acima não foi anotado em lugar nenhum que a
ferramenta possa conferir. Variáveis em [Configuração](Configuration).

## O que este sincronizador te mostra sobre isso

A pergunta de capacidade tem uma tela: a tabela de **chaves virtuais** do
[Painel](Dashboard) e o modal de detalhe atrás do `(i)` de cada linha. Campo a
campo, com o nome que o código escreve:

| Pergunta | Campo | Onde é lido | Rótulo no painel |
| :--- | :--- | :--- | :--- |
| O que a chave prometeu por minuto? | `rpmLimit`, `tpmLimit` | `models.py:129-130` | *Limite RPM*, *Limite TPM* (`i18n.py:253-254`) |
| Qual o teto em dinheiro? | `maxBudget` | `models.py:83-90` | *Teto de orçamento* (`i18n.py:255`) |
| Quanto já queimou? | `spend` | `models.py:76-81` | *Gasto* (`i18n.py:250`) |
| Bateu no teto? | `healthStatus` = `over_budget` | `models.py:92-95` | *Orçamento esgotado* (`i18n.py:240`) |
| Quantas chaves bateram? | `summarize` | `models.py:203-210` | contador do cabeçalho, `/api/status` |
| A cadeia declarada é coerente? | `capValue`, `capLevel`, `severity` | `gateway.py:285-287` | *Incoerências de limite* |

Quatro leituras fáceis de errar:

**`over_budget` é o sinal de saturação, e é fato, não previsão.** Dispara quando
`spend >= max_budget` `[FONTE: src/litellm_rtksync/models.py:92-95]`, constante
`SAUDE_ESTOURADA` `[FONTE: src/litellm_rtksync/models.py:12]`, selo montado em
`render.py:44`. Chave que chega lá todo período está subdimensionada; chave que
nunca chega perto é folga que dá para passar para outra pessoa. Essa contagem, por
chave e por período, é a única evidência que fecha o laço — o resto é projeção.

**Orçamento `0` ou ausente lê-se *não declarado*, nunca "ilimitado".**
`max_budget` devolve `None` para qualquer coisa que não seja estritamente positiva
`[FONTE: src/litellm_rtksync/models.py:83-90]` — o mesmo raciocínio do *zero não é
teto de zero* em [Coerência de limites](Rate-Limit-Coherence). Valor ausente é
dado que falta, não promessa.

**`SEVERIDADE_SEM_TETO` diz que *aquela chave* não tem nada acima dela — leia com
precisão.** O nível de teto de uma chave é o time dela quando ela tem um, e o
padrão da plataforma só quando ela não tem
`[FONTE: src/litellm_rtksync/gateway.py:361-369]`; o achado dispara quando esse
nível não declara nada para o campo
`[FONTE: src/litellm_rtksync/gateway.py:376-382]`. Uma chave dentro de um time com
teto **não** levanta esse achado, por mais vazio que `PLATFORM_*` esteja. O custo
real de deixar a plataforma vazia é mais silencioso, e está no segundo laço: um
time só é comparado com a plataforma quando **os dois** valores existem — caso
contrário o campo é pulado `[FONTE: src/litellm_rtksync/gateway.py:390-401]`. Com
`PLATFORM_*` em branco, um time declarando 5.000 RPM sobre um tier Start nunca
chega a ser examinado. Dimensionar um tier e não escrevê-lo no ambiente não deixa
só de avisar: desliga a verificação que teria pegado o erro.

**E o limite: o painel mostra teto declarado e gasto acumulado — ele não mostra
`R_h` nem `T_in`.** O cliente lê `/key/list`, `/team/list`, `/model/info`,
`/health`, `/credentials` e `/health/liveliness`, e nada além disso
`[FONTE: src/litellm_rtksync/gateway.py:114-201]`; veja [Arquitetura](Architecture)
para o porquê de a API administrativa ser a única superfície tocada. Para ritmo
por hora e tokens por requisição, consulte o log de gasto do próprio LiteLLM
agrupado por hora — cada entrada já traz chave, modelo e tokens — ou rode
`tools/measure_agent_usage.py` sobre o seu histórico. É do mesmo log que sai o
`c` — a única entrada lá de cima que é arbitrada em vez de medida: conte **chaves
distintas ativas no mesmo minuto, dividido por `N`**, ao longo de uma semana de
trabalho. Trocar esse chute por uma medição é a coisa mais valiosa que se pode
fazer com a tabela acima, porque `N` você já sabe exatamente. É fronteira
da ferramenta, não buraco: ela valida o que foi declarado, e declarar é o que se
faz depois de medir.

## Dois relógios, e só um escala com o time

| | Validade da chave | Capacidade |
| :--- | :--- | :--- |
| Campo | `expiresAt`, `remainingSeconds` (`models.py:122-123`) | `max_budget`, `rpm_limit`, `tpm_limit` |
| Estado | `expiring_soon`, `expired` (`models.py:9-10`) | `over_budget` (`models.py:12`) |
| Sintoma | requisição recusada por autenticação | 429, ou gasto congelado no teto |
| Solução | reemitir a chave | esperar o período, ou subir o teto |
| Escala com o time? | **Não** | **Sim** |

Chave que vence às duas da manhã não é problema de capacidade, e comprar tier não
resolve. Os dois vivem no mesmo modal — é por isso que se confundem.

## A restrição que não pode ficar enterrada

Tudo acima dimensiona **capacidade técnica**. Não autoriza compartilhar
assinatura, e o texto do fornecedor é explícito
`[FONTE: https://code.claude.com/docs/en/legal-and-compliance — lido em 12/09/2026]`:
*"OAuth authentication is intended exclusively for purchasers of Claude Free, Pro,
Max, Team, and Enterprise subscription plans…"* e *"Customers may not pay for,
resell, or intermediate Claude usage on their end users' behalf. Each end user
must authenticate with their own Anthropic API key, Claude subscription plan
credentials, or 3P inference provider credential."*

Para o caminho de assinatura isso fecha a pergunta sem medir nada: **doze devs,
doze assinaturas**, cada uma comprada e autenticada pelo seu titular. É a resposta
dos gateways irmãos, e as páginas deles a carregam.

**Este gateway é justamente o caminho que o mesmo texto permite**: *"This does not
restrict how customers provision and manage their own API keys … for use by the
customer's own authorized users."* Uma chave de API da organização, cobrada da
organização, repartida em chaves virtuais por time e por dev, é exatamente o
arranjo descrito — e a cadeia acima é como se mantém a repartição honesta.

Uma linha sobre saída de rede, porque é a forma dos irmãos e não desta: o que
chama atenção é várias contas saindo pelo mesmo endereço, e uma instância do
LiteLLM tem um endereço de saída para uma credencial.
[Teste de saída de rede](Egress-Testing) é a bancada que prova por onde o tráfego
realmente sai.

## Reproduzindo tudo

```bash
python3 tools/measure_agent_usage.py    # o seu perfil de demanda
python3 tools/sizing.py                 # as tabelas acima, com as suas constantes
```

O `tools/sizing.py` separa as entradas por procedência, e a separação é o ponto:
`PROFILES` é **medido** (um instantâneo copiado da rodada acima, carimbado em
`PROFILE_SOURCE`), `API_TIERS` é **publicado**, e `TEAM_SIZES`, `CONCURRENCY` e
`SLACK` são **arbitrados** — são os botões. Troque o perfil pela sua saída e as
tabelas se refazem.

Os dois scripts são versionados de propósito — número sem script que o reproduza
vira, com o tempo, número inventado. Repare no que isso compra e no que não
compra: torna o **método** reproduzível, não os valores. O perfil de demanda é o
instantâneo de um corpus que cresce e nunca mais vai voltar igual — por isso está
datado ao segundo lá em cima, em vez de ser apresentado como constante. Já a
tabela publicada de tiers e a aritmética do dimensionamento reproduzem exatamente.

O `tools/measure_agent_usage.py` lê **apenas** os campos numéricos de `usage`, o
`message.id` e o `timestamp`. Nenhum conteúdo de conversa é lido, agregado ou
impresso.

E para conferir o que foi de fato injetado no serviço, rode sempre
`docker compose config` com `--no-interpolate` — sem ele o comando despeja
`LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY` e `DASHBOARD_PASSWORD` no seu
terminal:

```bash
docker compose -f docker-compose.example.yml config --no-interpolate
```
