# Installation

Two supported paths: Docker Compose alongside your LiteLLM proxy, or a local virtual environment
for development. Both need the same two things — the proxy's address and its master key.

---

## Docker Compose

The synchronizer needs no volume of its own for state it must keep beyond the data directory, and
it never touches the proxy's Postgres. It talks to the administrative API.

```yaml
services:
  litellmrtksync:
    image: ghcr.io/pathbit/litellmrtksync:latest
    container_name: litellmrtksync
    restart: unless-stopped
    ports:
      # Porta interna 9090, igual nos tres sincronizadores; publicada em 9093
      # para que os tres possam rodar lado a lado. O bind em 127.0.0.1 mantem o
      # painel fora da rede.
      - "127.0.0.1:9093:9090"
    volumes:
      - litellmrtksync_data:/app/data
    environment:
      - LITELLM_URL=http://litellm:4000
      - LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY:?defina LITELLM_MASTER_KEY no .env}
      - SYNC_INTERVAL=${SYNC_INTERVAL:-300}
      - REFRESH_MARGIN=${REFRESH_MARGIN:-900}
      - PLATFORM_RPM_LIMIT=${PLATFORM_RPM_LIMIT:-}
      - PLATFORM_TPM_LIMIT=${PLATFORM_TPM_LIMIT:-}
      - PLATFORM_MAX_BUDGET=${PLATFORM_MAX_BUDGET:-}
      - ENABLE_WEB_DASHBOARD=${ENABLE_WEB_DASHBOARD:-1}
      - WEB_PORT=${WEB_PORT:-9090}
      - DASHBOARD_USER=${DASHBOARD_USER:-admin}
      - DASHBOARD_PASSWORD=${DASHBOARD_PASSWORD:-}
    depends_on:
      litellm:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "/opt/venv/bin/python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/healthz', timeout=3)"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 10s

volumes:
  litellmrtksync_data:
```

Two details worth not skipping:

**`condition: service_healthy`, not `service_started`.** LiteLLM runs its Prisma migrations
during boot; a cycle that starts before that finds an API that answers but has no tables yet.
The proxy needs a `healthcheck` of its own for this condition to have anything to wait for.

**No fallback value on the secrets.** `${LITELLM_MASTER_KEY:?...}` makes the stack refuse to
start without it. A default published in an example file becomes the real secret of every
deployment that copied and pasted.

### Running the bundled test stack

The repository ships a complete stack — Postgres, a real LiteLLM proxy and the synchronizer:

```bash
cp .env.example .env    # fill LITELLM_MASTER_KEY, POSTGRES_PASSWORD, LITELLM_SALT_KEY
docker compose -f docker-compose.test.yml up -d
```

Everything binds to loopback and the project name is its own, so it never collides with a stack
you already have running.

---

## Local virtual environment

Python 3.14.7 is what CI runs; 3.11 and up are tested.

### 1. Clone

```bash
git clone https://github.com/pathbit/LiteLlmRTKSync.git
cd LiteLlmRTKSync
```

### 2. Create and activate

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

### 3. Configure

```bash
cp .env.example .env
```

`.env` is ignored by git and must stay that way. The example file carries no secret value by
design — see [Configuration](Configuration) for every variable.

### 4. Run

```bash
# Inspect once and print every finding; exits 1 when a limit is incoherent
litellmrtksync --status --url http://127.0.0.1:4000

# One-shot pass
litellmrtksync --once

# Daemon with the dashboard
litellmrtksync --daemon
```

Both `litellmrtksync` and `litellm-rtksync` are installed as entry points; they are the same
command.

---

## First access to the panel

There is **no factory password**. On first boot, if `DASHBOARD_PASSWORD` is empty, a recovery
credential is generated and written with mode `0600` inside the data directory. The log names
the file, never the value. Sign in with it, then set your own password on the screen.

See [Authentication](Authentication) for the whole flow, including headless mode.

---

## Verifying it works

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9093/healthz   # 200 = OK
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9093/          # 401 unauthenticated
```

`/healthz` is intentionally unauthenticated — it is the container's health probe — and answers
`OK` or `LITELLM_UNREACHABLE`. Every other route requires credentials.
