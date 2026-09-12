# LiteLlmRTKSync

**LiteLLM virtual key, credential and rate-limit synchronizer.**
Third of the RTKSync family, after [9RTKSync](https://github.com/pathbit/9RTKSync)
(9Router) and [OminiRTkSync](https://github.com/pathbit/OminiRTkSync) (OmniRoute).

*(Versão em português ao final.)*

---

## Why this is not a copy of its siblings

9RTKSync and OminiRTkSync **renew** OAuth credentials: their gateway stores
tokens that expire, and nobody notices when one dies until a request fails.

LiteLLM has no consumer OAuth. Its credentials are provider API keys in the
model catalogue and virtual keys the proxy issues itself, and its state lives in
Postgres behind Prisma — there is no SQLite file to read. So there is nothing to
renew here. There are three things nobody checks on their own:

1. **A virtual key expires quietly.** `LiteLLM_VerificationToken.expires` passes,
   and the first sign is a request failing.
2. **A provider key in the model catalogue can have been revoked.** The proxy
   only finds out when it tries to use it.
3. **A key limit above its team's limit is accepted without complaint.**
   Verified against a real proxy: a team capped at `rpm_limit=60` accepts a key
   declaring `rpm_limit=600`. The effective limit is always the most restrictive
   on the path, so the larger number exists only in the record — whoever
   configured it believes they have 600 and gets 60.

Everything this tool does is **read-only**. It reports and validates; changing a
limit, a key or a model is the operator's call, through LiteLLM's own screens.

## What it reads

Through the administrative API, never the database — the Prisma schema changes
between releases, and writing to the table would skip the invariants the proxy
enforces:

| Route | What comes back |
| --- | --- |
| `/health/liveliness` | whether the proxy is up |
| `/key/list` | virtual keys, paginated to the end |
| `/team/list` | teams and their caps |
| `/model/info` | registered models and their `litellm_params` |
| `/credentials` | named credentials, when the version has them |

## Rate-limit coherence

One rule, applied field by field across `tpm_limit`, `rpm_limit`,
`max_parallel_requests` and `max_budget`:

> **No level may declare a value greater than the level above it.**
> key ≤ team ≤ platform default.

The platform default comes from `PLATFORM_RPM_LIMIT`, `PLATFORM_TPM_LIMIT` and
`PLATFORM_MAX_BUDGET`. Leaving them empty is a choice, not an error — the panel
reports "no cap declared" and moves on.

## Running

```bash
cp .env.example .env     # fill LITELLM_MASTER_KEY and the platform caps
docker compose -f docker-compose.test.yml up -d
```

The panel answers on `http://127.0.0.1:9093`, bound to loopback. The first login
uses the recovery credential generated on first boot; the log says which file
holds it, never its value. Set your own password on the screen — there is no
factory password, because a static default is a public credential by definition.

Without Docker:

```bash
make venv && make test
PYTHONPATH=src python3 -m litellm_rtksync.cli --status
```

`--status` exits `1` when it finds an incoherent limit, so it drops straight
into CI.

## Security posture

Inherited from the siblings, for the same reasons:

- the page is rendered entirely on the server — no endpoint serves proxy state
  to an unauthenticated browser;
- security headers, including a Content-Security-Policy, on **every** response,
  the `401` body among them — that body is what the browser shows when you press
  **ESC** on the Basic Auth dialog;
- no credential ever reaches a response body, a log line or a banner: a key with
  no alias is shown masked, and a model reports *whether* it has a key, never
  which;
- a cross-origin `POST` is refused, because the browser attaches Basic Auth to a
  third-party form on its own;
- an undeclared expiry is reported as undeclared, never as "unlimited".

---

# Em português

Terceiro da família RTKSync. **Não é cópia dos irmãos:** o LiteLLM não tem OAuth
de consumidor, e o estado dele vive em Postgres via Prisma, não num SQLite em
disco. Aqui não há o que renovar — há o que verificar, e três coisas que ninguém
verifica sozinho:

1. uma **chave virtual vence em silêncio**, e o primeiro sinal é a requisição
   falhando;
2. uma **chave de provedor cadastrada num modelo pode ter sido revogada**, e o
   proxy só descobre na hora de usar;
3. um **limite de chave acima do limite do time é aceito sem reclamação** —
   verificado contra um proxy real: um time com `rpm_limit=60` aceita uma chave
   declarando `rpm_limit=600`. Vale sempre o teto mais restritivo do caminho,
   então o número maior existe só no cadastro.

Tudo aqui é **somente leitura**. A ferramenta relata e valida; alterar limite,
chave ou modelo é decisão do operador, pelas telas do próprio LiteLLM.

**Coerência de limites:** uma regra só, campo a campo em `tpm_limit`,
`rpm_limit`, `max_parallel_requests` e `max_budget` — nenhum nível pode declarar
valor maior que o de cima: chave ≤ time ≤ padrão da plataforma.

**Como rodar:** copie `.env.example` para `.env`, preencha a master key e os
tetos, e suba com `docker compose -f docker-compose.test.yml up -d`. O painel
responde em `http://127.0.0.1:9093`, preso ao loopback. O primeiro acesso usa a
credencial de recuperação gerada no primeiro boot — o log diz em qual arquivo
ela está, nunca o valor. Defina a sua senha pela tela: não existe senha de
fábrica, porque um valor estático é, por definição, uma credencial pública.

---

MIT. Veja [`LICENSE`](LICENSE).
