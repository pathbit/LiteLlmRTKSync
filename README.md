# LiteLlmRTKSync · LiteLLM Universal Token & Connection Synchronizer

[![CI](https://github.com/pathbit/LiteLlmRTKSync/actions/workflows/ci.yml/badge.svg)](https://github.com/pathbit/LiteLlmRTKSync/actions/workflows/ci.yml)
[![Release and Docker Package](https://github.com/pathbit/LiteLlmRTKSync/actions/workflows/release.yml/badge.svg)](https://github.com/pathbit/LiteLlmRTKSync/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.14.7-blue.svg)](https://www.python.org/ftp/python/3.14.7/python-3.14.7-macos11.pkg)
[![Docker Package](https://img.shields.io/badge/docker-ghcr.io%2Fpathbit%2Flitellmrtksync-blue)](https://github.com/pathbit/LiteLlmRTKSync/pkgs/container/litellmrtksync)

**`LiteLlmRTKSync`** (*LiteLLM Universal Token & Connection Synchronizer*) is the
read-only health guardian for [LiteLLM](https://github.com/BerriAI/litellm)
proxies. It reports virtual keys about to expire, provider keys the catalogue
still trusts but the provider has revoked, and rate-limit ceilings that
contradict each other — three states nobody is told about until a request fails.

Third of the RTKSync family, after [9RTKSync](https://github.com/pathbit/9RTKSync)
(9Router) and [OminiRTkSync](https://github.com/pathbit/OminiRTkSync) (OmniRoute).

## Documentation

The full documentation lives in the [project wiki](../../wiki): installation, the complete
environment-variable contract, the dashboard, authentication and break-glass recovery,
persistent logging, architecture, troubleshooting, rate-limit coherence, and how to size a
team against an API tier.

Wiki pages are generated from [`docs/wiki/`](docs/wiki) — edit them there and open a pull
request; a push to `master` republishes the wiki automatically.

---

## Why this is not a copy of its siblings

9RTKSync and OminiRTkSync **renew** OAuth credentials: their gateway stores
tokens that expire, and nobody notices when one dies until a request fails.

LiteLLM has no consumer OAuth. Its credentials are provider API keys in the
model catalogue and virtual keys the proxy issues itself, and its state lives in
Postgres behind Prisma — there is no SQLite file to read. **So there is nothing
to renew here**, and everything this tool does is **read-only**: it reports and
validates. Changing a limit, a key or a model is the operator's call, through
LiteLLM's own screens.

---

## Core Features

* **Virtual Key Expiry Watch**
  * `LiteLLM_VerificationToken.expires` passes in silence, and the first sign is
    a request failing. Keys already expired and keys inside the renewal margin
    are reported separately.
  * An undeclared expiry is reported as *undeclared*, never as "unlimited".
* **Live Provider Credential Validation**
  * A provider key registered in the model catalogue may already have been
    revoked; the proxy only finds out when it tries to use it. Each key is
    checked against its own provider instead of being assumed healthy.
* **Rate-Limit Coherence Enforcement**
  * One rule, applied field by field across `tpm_limit`, `rpm_limit`,
    `max_parallel_requests` and `max_budget`: **key ≤ team ≤ platform default**.
  * Verified against a real proxy: a team capped at `rpm_limit=60` accepts a key
    declaring `rpm_limit=600` without complaint. The effective limit is always
    the most restrictive on the path, so the larger number exists only in the
    record — whoever configured it believes they have 600 and gets 60.
  * Upstream report: [BerriAI/litellm#40866](https://github.com/BerriAI/litellm/issues/40866).
* **Administrative API Only, Never the Database**
  * The Prisma schema changes between releases, and writing to the table would
    skip the invariants the proxy enforces.
* **Built-in Web Dashboard**
  * Lightweight server on port `9090` (published on `9093` in the test stack),
    rendered entirely server-side, with findings grouped by severity.
* **Strict Virtual Environment Execution**
  * All Python execution strictly isolated in dedicated virtual environments both
    in Docker containers (`/opt/venv`) and in local setups (`.venv`).

---

---

## 🔑 Signing in to the dashboard

| | |
| :--- | :--- |
| **Address** | `http://localhost:9093` |
| **User** | `admin` — or whatever you set in `DASHBOARD_USER` |
| **Password** | the value of `DASHBOARD_PASSWORD` in your `.env` |

There is **no factory password**, and that is deliberate: a fixed password shipped
in an image is public the moment the image is. You choose it once, in one place:

```bash
cp .env.example .env
# edit .env:
DASHBOARD_USER=admin
DASHBOARD_PASSWORD=<the password you choose>
```

Then bring the stack up. That user and that password are what the panel accepts.

### Did not set a password, and now cannot get in?

On first boot with `DASHBOARD_PASSWORD` empty, the container generates a
**recovery credential** and writes it inside the data directory. Read it:

```bash
docker exec litellmrtk-sync cat /app/data/.dashboard_recovery
```

Sign in as `admin` with that value, then set a real password on the screen. The
recovery credential keeps working afterwards — it is break-glass, and one that
stopped working the moment you set a password would be useless exactly when you
need it.

> **Português:** o painel pede usuário e senha. O usuário é `admin` (ou o que
> estiver em `DASHBOARD_USER`) e a senha é a que **você** definir em
> `DASHBOARD_PASSWORD` no `.env` — não existe senha de fábrica, porque um valor
> fixo publicado na imagem é uma credencial pública. Se subiu sem definir senha,
> use o comando acima para ler a credencial de recuperação e entre com ela.

## Running with Docker

### Configuration: `.env` from the example

The whole configuration comes from environment variables, read from a `.env`
next to `docker-compose.yml` — Compose finds it on its own, with no flag.

```bash
make setup      # creates .env from .env.example, never overwriting an existing one
```

At the end, the target lists exactly which variables were left blank and need
filling in. Fill them and bring the stack up.

The `.env` is **never** committed, and `.env.example` carries no secret value —
a value published in an example file is, by definition, a public credential. A
test guarantees that every variable a compose file requires exists in the
example, so that `cp .env.example .env` never produces an incomplete `.env`.

### Ports, and why each one differs

The three synchronizers listen on the **same port inside the container**
(`9090`) and publish on different host ports, so that all three can run side by
side. The same goes for the gateways: each one has its own.

| Service | Internal port | Published on the host |
| :--- | :--- | :--- |
| 9Router | `20128` | `8081` |
| OmniRoute | `20128` | `8082` |
| LiteLLM | `4000` | `8083` |
| 9RTKSync (dashboard) | `9090` | `9091` |
| OminiRTkSync (dashboard) | `9090` | `9092` |
| LiteLlmRTKSync (dashboard) | `9090` | `9093` |

The article stack (`claudegravity`) keeps **`20128`**, the default 9Router port.
The repository stacks deliberately move out of that range: that way you can run
the article and all three synchronizers at the same time, with no conflict.

Everything is bound to `127.0.0.1`: the gateway carries real credentials and
must not be reachable on the local network. To change any of them, edit the left
side of the mapping in the compose file — the right side is the internal port,
the one the process listens on.


Official multi-architecture Docker images (`linux/amd64` and `linux/arm64`) are published automatically to the GitHub Container Registry (GHCR) on pushes to `master` with changes in `src/`. The registry enforces an automated retention policy keeping strictly the last 3 versions:

```bash
docker pull ghcr.io/pathbit/litellmrtksync:latest
```

### Docker Compose Example

Add `litellmrtk-sync` to your `docker-compose.yml` alongside your LiteLLM proxy.
This is the same shape as [`docker-compose.example.yml`](docker-compose.example.yml)
in the repository — service, container and hostname carry the same name, so the
address you read in one place is the address that resolves:

```yaml
services:
  litellmrtk-db:
    # O LiteLLM guarda chaves virtuais, times e orcamentos em Postgres via
    # Prisma. Sem banco o proxy sobe sem API administrativa, e e a API
    # administrativa que este sincronizador le.
    image: postgres:16-alpine
    container_name: litellmrtk-db
    hostname: litellmrtk-db
    networks:
      - litellmrtksync-net
    restart: unless-stopped
    environment:
      - POSTGRES_USER=litellm
      - POSTGRES_DB=litellm
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD:?defina POSTGRES_PASSWORD no .env}
    volumes:
      - litellmrtksync_db:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U litellm -d litellm"]
      interval: 10s
      timeout: 5s
      retries: 20

  litellmrtk-router:
    image: ghcr.io/berriai/litellm:main-stable
    container_name: litellmrtk-router
    hostname: litellmrtk-router
    networks:
      - litellmrtksync-net
    restart: unless-stopped
    ports:
      # 4000 dentro do container; 8083 no host.
      - "127.0.0.1:8083:4000"
    environment:
      # Sem valor de fallback: um segredo publicado em arquivo de exemplo vira o
      # segredo real de toda implantacao que so copiou e colou.
      - LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY:?defina LITELLM_MASTER_KEY no .env}
      - LITELLM_SALT_KEY=${LITELLM_SALT_KEY:?defina LITELLM_SALT_KEY no .env}
      - DATABASE_URL=postgresql://litellm:${POSTGRES_PASSWORD:?defina POSTGRES_PASSWORD}@litellmrtk-db:5432/litellm
      - STORE_MODEL_IN_DB=True
      # Credencial da UI do LiteLLM. Sem estas duas, a tela de login aceita a
      # MASTER_KEY como senha -- ou seja, para ver um painel o operador digita
      # a credencial que administra a instalacao inteira.
      - UI_USERNAME=${DASHBOARD_USER:-admin}
      - UI_PASSWORD=${DASHBOARD_PASSWORD:?defina DASHBOARD_PASSWORD no .env}
    depends_on:
      litellmrtk-db:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:4000/health/liveliness',timeout=3)\""]
      interval: 15s
      timeout: 5s
      retries: 10
      start_period: 40s

  litellmrtk-sync:
    image: ghcr.io/pathbit/litellmrtksync:latest
    container_name: litellmrtk-sync
    hostname: litellmrtk-sync
    networks:
      - litellmrtksync-net
    restart: unless-stopped
    ports:
      # Porta interna 9090, igual nos tres sincronizadores; publicada em 9093.
      - "127.0.0.1:9093:9090"
    volumes:
      - litellmrtksync_data:/app/data
    environment:
      - LITELLM_URL=http://litellmrtk-router:4000
      - LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY:?defina LITELLM_MASTER_KEY no .env}
      - SYNC_INTERVAL=${SYNC_INTERVAL:-300}
      - REFRESH_MARGIN=${REFRESH_MARGIN:-900}
      # Teto da plataforma: nenhum time e nenhuma chave pode declarar acima.
      - PLATFORM_RPM_LIMIT=${PLATFORM_RPM_LIMIT:-}
      - PLATFORM_TPM_LIMIT=${PLATFORM_TPM_LIMIT:-}
      - PLATFORM_MAX_BUDGET=${PLATFORM_MAX_BUDGET:-}
      - ENABLE_WEB_DASHBOARD=${ENABLE_WEB_DASHBOARD:-1}
      - WEB_PORT=${WEB_PORT:-9090}
      - DASHBOARD_USER=${DASHBOARD_USER:-admin}
      - DASHBOARD_PASSWORD=${DASHBOARD_PASSWORD:-}
    depends_on:
      litellmrtk-router:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "/opt/venv/bin/python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/healthz', timeout=3)"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 10s

volumes:
  litellmrtksync_db:
  litellmrtksync_data:

networks:
  litellmrtksync-net:
    # Rede propria da stack, com nome explicito. Na rede default, duas stacks no
    # mesmo daemon resolvem o mesmo nome curto e nao da para saber a qual
    # gateway o sincronizador se conectou.
    name: litellmrtksync-net
```

---

## Local Development in Virtual Environment

Following standard environment isolation, local runs strictly use a Python virtual environment with [Python 3.14.7](https://www.python.org/ftp/python/3.14.7/python-3.14.7-macos11.pkg):

### 1. Clone the Repository

```bash
git clone https://github.com/pathbit/LiteLlmRTKSync.git
cd LiteLlmRTKSync
```

### 2. Create and Activate the Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

### 3. Configure Environment Variables (.env)

Copy the official template to create your local `.env` file (the `.env` file is strictly ignored by git):

```bash
cp .env.example .env
```

### 4. Available CLI Commands

```bash
# Inspect the proxy once and print every finding
litellmrtksync --status --url http://127.0.0.1:8083

# Run an immediate one-shot inspection pass
litellmrtksync --once

# Run continuous background daemon with web dashboard on port 9090 (published on 9093)
litellmrtksync --daemon
```

`--status` exits `1` when it finds an incoherent limit, so it drops straight into CI.

---

## Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `LITELLM_URL` | `http://litellmrtk-router:4000` | Base URL of the LiteLLM proxy — the router service's name inside the stack |
| `LITELLM_MASTER_KEY` | *(empty)* | Master key used to read administrative state. Required; a secret — set it in `.env`, never in the example |
| `SYNC_INTERVAL` | `300` | Inspection cycle interval in seconds |
| `REFRESH_MARGIN` | `900` | How far ahead a virtual key starts being reported as expiring |
| `CRON_ENABLED` | `1` | Internal scheduler (`1` to enable, `0` to disable) |
| `CRON_INTERVAL` | inherits `SYNC_INTERVAL` | Scheduler interval when it should differ from the cycle |
| `PLATFORM_RPM_LIMIT` | *(empty)* | Platform-wide requests-per-minute ceiling. No team or key may declare above it |
| `PLATFORM_TPM_LIMIT` | *(empty)* | Platform-wide tokens-per-minute ceiling |
| `PLATFORM_MAX_BUDGET` | *(empty)* | Platform-wide budget ceiling |
| `ENABLE_WEB_DASHBOARD` | `1` | Enable the embedded web dashboard (`1` to enable, `0` to disable) |
| `WEB_PORT` | `9090` | HTTP port for the web dashboard |
| `WEB_HOST` | `0.0.0.0` | Network binding interface for the dashboard web server |
| `DASHBOARD_USER` | `admin` | HTTP Basic Auth username for web dashboard access |
| `DASHBOARD_PASSWORD` | *(empty)* | Panel password. Left empty, the first sign-in uses the recovery credential generated on first boot |
| `DASHBOARD_RECOVERY_HASH` | auto | Break-glass credential hash. Generated on first boot when omitted |
| `CREDENTIAL_CHECK_ENABLED` | `1` | Ask each provider whether the key declared on the model is still accepted |
| `CREDENTIAL_CHECK_TIMEOUT` | `8` | Timeout in seconds for each credential probe |
| `DATA_DIR` | `/app/data` | Base directory for everything this container writes. Created on startup |
| `LOG_DIR` | `<DATA_DIR>/logs` | Directory for persistent logs |
| `LOG_RETENTION_DAYS` | `30` | Days of log history to keep |
| `LOG_LEVEL` | `INFO` | Minimum level written to the log |
| `LOG_TO_STDOUT` | `1` | Also write the log to stdout (`1`/`0`) |

Leaving the three `PLATFORM_*` values empty is a choice, not an error — the panel
reports "no cap declared" and moves on.

---

## Web Dashboard

When running with `ENABLE_WEB_DASHBOARD=1`, access the dashboard in your browser:

👉 **http://localhost:9093**

Dashboard capabilities:
* Live operational metrics (virtual keys, teams, models, findings by severity).
* Expiry countdown per virtual key, with an undeclared expiry shown as undeclared.
* Rate-limit coherence report naming the field, both values and the consequence.
* Proxy liveness card against `/health/liveliness`, with the measured latency and the proxy base URL; the panel's own `/healthz` answers `OK` or `LITELLM_UNREACHABLE`.
* English, Portuguese and Spanish in the flag selector, stored server-side so the choice survives a browser change.
* Background scheduler (`CRON_ENABLED`/`CRON_INTERVAL`) with a per-cycle log of what it found, readable from the panel itself.
* One cycle trigger and one only (`POST /acoes/cron`), which is also what the header's primary button submits, page reload (`POST /acoes/atualizar`), on-demand proxy probe (`POST /acoes/testar-gateway`) and full state as JSON (`GET /api/status`, `GET /api/cron-status`).
* One `(i)` button per table row, opening a modal with the full detail — virtual key limits, budget ceiling and expiry instant; a model's API base and where its credential comes from. Never the key itself.

---

## Unit and Integration Testing

You can run the test suite with zero installations on your host machine (Docker only), or locally via your virtual environment.

### Option 1. Container Testing (Zero Host Installation)

The only requirement is Docker. Nothing else needs to be installed on your machine:

```bash
# Against a real LiteLLM + Postgres stack
docker compose -f docker-compose.test.yml up -d
```

### Option 2. Local Virtual Environment (Optional Prerequisites)

If you prefer testing directly on your host with Python 3.14+:

```bash
source .venv/bin/activate
make test
# Or directly
PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py"
```

### Option 3. Automated Docker Image Validation with Testcontainers

Validates that the generated Docker image boots cleanly, exposes the web dashboard on port 9090, responds on `/login` (200 OK) and `/healthz`, and executes CLI commands:

```bash
# Install package with test dependencies
pip install ".[test]"

# Run Testcontainers validation suite
python3 -m unittest tests/test_container.py -v
```

---

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
- there is no factory password: a static default is a public credential by
  definition.

---

## Contributing and Branch Protection

* The `master` branch is protected. All contributions must be submitted through Pull Requests and pass all CI checks.
* For bug reports or new provider requests, please open an issue in [GitHub Issues](https://github.com/pathbit/LiteLlmRTKSync/issues).
* Official upstream proxy: [LiteLLM on GitHub](https://github.com/BerriAI/litellm).

---

## 📄 License

Distributed under the MIT License. The full text is available in [LICENSE](https://github.com/pathbit/LiteLlmRTKSync/blob/master/LICENSE).

In practice: use, copy, modify, and distribute freely, including commercially, provided that copyright and license notices accompany copies. The software is provided as is, without warranty.

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
   então o número maior existe só no cadastro, e quem configurou acredita ter
   600 e recebe 60.

Tudo aqui é **somente leitura**. A ferramenta relata e valida; alterar limite,
chave ou modelo é decisão do operador, pelas telas do próprio LiteLLM.

**Coerência de limites:** uma regra só, campo a campo em `tpm_limit`,
`rpm_limit`, `max_parallel_requests` e `max_budget` — nenhum nível pode declarar
valor maior que o de cima: **chave ≤ time ≤ padrão da plataforma**. O teto da
plataforma vem de `PLATFORM_RPM_LIMIT`, `PLATFORM_TPM_LIMIT` e
`PLATFORM_MAX_BUDGET`; deixá-los vazios é uma escolha, não um erro.

**Documentação completa:** [wiki do projeto](../../wiki), gerada de
[`docs/wiki/`](docs/wiki) — instalação, contrato de variáveis, painel,
autenticação e recuperação, log persistente, arquitetura, diagnóstico, coerência
de limites e como dimensionar um time contra um tier de API.

**Como rodar:** copie `.env.example` para `.env`, preencha a master key e os
tetos, e suba com `docker compose -f docker-compose.example.yml up -d`. O painel
responde em `http://127.0.0.1:9093`, preso ao loopback. O primeiro acesso usa a
credencial de recuperação, gerada no primeiro boot — o log diz em qual arquivo
ela está, nunca o valor. Defina a sua senha pela tela: não existe senha de
fábrica, porque um valor estático é, por definição, uma credencial pública.

---

Developed with ❤️ by [Pathbit](https://pathbit.co/)
