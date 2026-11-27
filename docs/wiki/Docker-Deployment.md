# Docker & Docker Compose Deployment Guide

*(Versão em português disponível na segunda metade desta página.)*

Complete guide to deploying **LiteLlmRTKSync** and **LiteLLM Proxy** via Docker and Docker Compose, including token optimization with **Headroom** and **Caveman**, remote access via **Cloudflare Tunnel** and **Tailscale**.

---

## 1. Official packages and image versions

Official multi-architecture Docker images (`linux/amd64` and `linux/arm64`) are published to the GitHub Container Registry (GHCR):

Use a numeric tag to pin a release or `:latest` to follow the newest stable release. A pull request to `main` runs CI. The release workflow publishes the image from the version tag. Find the current release tag in the [latest GitHub Release](https://github.com/pathbit/LiteLlmRTKSync/releases/latest). To pin Compose, use the same numeric tag in its `image` field.

```bash
docker pull ghcr.io/pathbit/litellmrtksync:1.0.4
docker pull ghcr.io/pathbit/litellmrtksync:latest
```

For rollback, pull the previously deployed numeric tag. For exact verification, compare its manifest digest with the digest in the GitHub release notes.

> [!NOTE]
> **GHCR Visibility & Permissions**: GitHub Container Registry packages in organizations default to Private upon initial push. If `docker pull` returns `unauthorized`, an organization administrator must navigate to `github.com/orgs/pathbit/packages/container/litellmrtksync/settings` and set the package visibility to **Public**.

---

## 2. Quick Start with Standalone Docker

If you already have a LiteLLM proxy instance running on your host machine or network:

```bash
docker run -d \
  --name litellmrtk-sync \
  --restart unless-stopped \
  -p 127.0.0.1:9093:9090 \
  -v litellmrtksync_data:/app/data \
  -e LITELLM_URL=http://host.docker.internal:4000 \
  -e LITELLM_MASTER_KEY=sk-your-master-key \
  -e DASHBOARD_USER=admin \
  -e DASHBOARD_PASSWORD=your-secure-password \
  ghcr.io/pathbit/litellmrtksync:latest
```

---

## 3. Production Deployment with Docker Compose

The standard production setup runs LiteLLM (gateway proxy), PostgreSQL (state & storage), and LiteLlmRTKSync (synchronizer) on an isolated management network (`litellmrtksync-net`), with optional shared inference networking (`rtk-inference-net`).

### Step 1: Initialize Configuration

```bash
cp .env.example .env
# Edit .env and configure LITELLM_MASTER_KEY, POSTGRES_PASSWORD, and DASHBOARD_PASSWORD
```

### Step 2: Launch Stack

```bash
docker compose -f docker-compose.example.yml up -d
```

### Service Map and Port Allocation

| Service | Container Name | Internal Port | Host Published Port | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **LiteLLM** | `litellmrtk-router` | `4000` | `127.0.0.1:8083` | OpenAI-compatible Gateway Proxy |
| **LiteLlmRTKSync** | `litellmrtk-sync` | `9090` | `127.0.0.1:9093` | Guardian Dashboard & Healthz |
| **Headroom** *(optional)* | `litellmrtk-headroom` | `8787` | `127.0.0.1:8787` | Token Compression Proxy |

Dashboard access: **http://localhost:9093**

---

## 4. Token & Cost Optimization: Headroom & Caveman

AI coding agents (such as **Claude Code**, **Cursor**, **Windsurf**, and **Roo Code**) consume vast amounts of tokens when sending large files, terminal logs, git diffs, and conversational explanations. Using **Headroom** and **Caveman** together drastically cuts token consumption and prevents rate limits.

```
┌────────────────────────────────┐
│   Coding Agent / Developer     │
│   (Claude Code / Cursor)       │
└───────────────┬────────────────┘
                │
                ▼
┌────────────────────────────────┐
│      Caveman (Plugin/Skill)    │  <-- Minimizes OUTPUT tokens
│   (Enforces concise responses) │
└───────────────┬────────────────┘
                │
                ▼
┌────────────────────────────────┐
│     Headroom (Proxy: 8787)     │  <-- Compresses INPUT tokens
│  (Trims logs, AST & context)   │
└───────────────┬────────────────┘
                │
                ▼
┌────────────────────────────────┐
│     LiteLLM (Gateway: 8083)    │  <-- Multi-Provider Model Routing
└───────────────▲────────────────┘
                │
┌───────────────┴────────────────┐
│ LiteLlmRTKSync (Guardian: 9093)│  <-- Coherence & rate-limit healing
└────────────────────────────────┘
```

### Headroom: Input Token Compression Proxy

Headroom acts as a middleware proxy sitting between your coding agent and LiteLLM:
- Analyzes context payloads, logs, git diffs, and structured JSON.
- Compresses input tokens by 20% to 80% without losing critical code semantics.
- To start with Docker Compose:

```bash
docker compose -f docker-compose.example.yml --profile headroom up -d
```

Configure your coding tool to point to Headroom (`http://127.0.0.1:8787/v1`) with your LiteLLM master key. Headroom automatically proxies optimized requests to `http://litellmrtk-router:4000`.

### Caveman: Output Token Reduction Skill

While Headroom compresses what the LLM *reads*, Caveman minimizes what the LLM *writes*:
- Enforces an ultra-terse, compact agent communication style ("concise mode").
- Strips conversational filler, preamble, and pleasantries while maintaining strict accuracy for code edits and terminal actions.
- Configured in Claude Code (`settings.local.json` prompt instructions) or agent system instructions.

---

## 5. Remote Access: Cloudflare Tunnel & Tailscale

By default, ports are bound to `127.0.0.1` for loopback security. When you need remote access from laptops, mobile devices, or team members, enable Cloudflare Tunnel or Tailscale.

> [!WARNING]
> **Security Prerequisite**: Before enabling any public tunnel or remote exposure, ensure that your LiteLLM master key is strong and that administrative access is protected. Exposing the gateway without authentication allows anyone with the URL to consume your LLM balances.

### Option A: Cloudflare Quick Tunnel (Ephemeral URL)

Useful for temporary demonstrations and remote testing without domain setup:

```bash
docker compose -f docker-compose.example.yml --profile tunel up -d
```

View the generated `https://...trycloudflare.com` URL in container logs:

```bash
docker logs litellmrtk-tunel
```

### Option B: Cloudflare Named Tunnel (Persistent Domain)

For permanent custom domains (`litellm.yourdomain.com`) with Cloudflare Zero Trust:

1. Create a tunnel in the Cloudflare Zero Trust dashboard.
2. Add your tunnel token to `.env`:
   ```bash
   TUNNEL_TOKEN=eyJhIjoi...
   ```
3. Start the tunnel profile:
   ```bash
   docker compose -f docker-compose.example.yml --profile tunel up -d
   ```

### Option C: Tailscale (Private Zero-Trust Tailnet — Recommended)

The safest method: your gateway joins your private tailnet and is never exposed to the public internet.

1. Generate an ephemeral or reusable auth key at `https://login.tailscale.com/admin/settings/keys`.
2. Add `TS_AUTHKEY` to `.env`:
   ```bash
   TS_AUTHKEY=tskey-auth-...
   ```
3. Start the tailnet container:
   ```bash
   docker compose -f docker-compose.example.yml --profile tailnet up -d
   ```
4. Access via MagicDNS at `http://litellmrtk:4000` or Tailscale IP `100.x.y.z:4000`.

Alternatively, install Tailscale directly on the host machine:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4
```

---

## 6. Container Healthchecks & Monitoring

Verify the status of all services:

```bash
docker compose -f docker-compose.example.yml ps
```

The synchronizer exposes `/healthz` on port 9090 (host 9093):
- `OK` (HTTP 200): Synchronizer active and LiteLLM reachable.
- `LITELLM_UNREACHABLE` (HTTP 503): LiteLLM proxy not answering.

---

*(Versão em Português)*

# Guia de Implantação via Docker e Docker Compose

Guia completo para implantar o **LiteLlmRTKSync** e o **LiteLLM Proxy** via Docker e Docker Compose, com otimização de tokens via **Headroom** e **Caveman**, acesso remoto seguro via **Cloudflare Tunnel** e **Tailscale**.

## 1. Pacotes oficiais e versões de imagem

As imagens oficiais para `linux/amd64` e `linux/arm64` são publicadas no GitHub Container Registry. Use a tag numérica para fixar uma release ou `:latest` para acompanhar a versão estável mais recente. Pull requests para `main` passam pelo CI. O fluxo de release publica a imagem a partir da tag da versão. Consulte a [última release no GitHub](https://github.com/pathbit/LiteLlmRTKSync/releases/latest) para ver a tag atual. No Compose, fixe a mesma tag no campo `image`.

```bash
docker pull ghcr.io/pathbit/litellmrtksync:1.0.4
docker pull ghcr.io/pathbit/litellmrtksync:latest
```

Para reverter, use a tag numérica que estava em produção. Para conferir a imagem exata, compare o digest do manifesto com o digest nas notas da release no GitHub.

> [!NOTE]
> **Visibilidade no GHCR**: Por padrão de segurança do GitHub, pacotes criados sob organizações nascem com visibilidade Privada. Se o comando `docker pull` responder com `unauthorized`, o administrador da organização no GitHub deve acessar as configurações do pacote (`github.com/orgs/pathbit/packages/container/litellmrtksync/settings`) e alterar a visibilidade para **Public**.

## 2. Implantação Rápida com Docker Run

Para executar o sincronizador apontando para um LiteLLM já existente:

```bash
docker run -d \
  --name litellmrtk-sync \
  --restart unless-stopped \
  -p 127.0.0.1:9093:9090 \
  -v litellmrtksync_data:/app/data \
  -e LITELLM_URL=http://host.docker.internal:4000 \
  -e LITELLM_MASTER_KEY=sua-master-key \
  -e DASHBOARD_USER=admin \
  -e DASHBOARD_PASSWORD=sua-senha-segura \
  ghcr.io/pathbit/litellmrtksync:latest
```

## 3. Implantação Completa com Docker Compose

```bash
# 1. Prepare as variáveis de ambiente
cp .env.example .env

# 2. Inicie a stack
docker compose -f docker-compose.example.yml up -d
```

Acesse o painel web em: **http://localhost:9093**

## 4. Otimização de Tokens: Headroom e Caveman

A combinação de **Headroom** e **Caveman** atua em duas frentes complementares:
1. **Headroom (Entrada/Input):** Middleware proxy que intercepta context windows, logs de compilação e comandos git diff, compactando a carga antes do envio ao LiteLLM. Ative via perfil:
   ```bash
   docker compose -f docker-compose.example.yml --profile headroom up -d
   ```
   Aponte seus clientes para `http://127.0.0.1:8787/v1`.
2. **Caveman (Saída/Output):** Instrução de estilo conciso para agentes (Claude Code / Cursor) que elimina rodeios e respostas prolixas, gerando respostas diretas e economizando 30% a 60% de tokens de saída.

## 5. Acesso Remoto: Cloudflare Tunnel e Tailscale

Regra essencial de segurança: antes de expor qualquer rota, garanta que sua chave mestra é forte e segura.

- **Cloudflare Tunnel Rápido:** `docker compose -f docker-compose.example.yml --profile tunel up -d`
- **Cloudflare Tunnel Nomeado:** Preencha `TUNNEL_TOKEN` no `.env` e suba o perfil `tunel`.
- **Tailscale (VPN Privada):** Preencha `TS_AUTHKEY` no `.env` e execute `docker compose -f docker-compose.example.yml --profile tailnet up -d`.


## Imágenes oficiales y versión fija

Las imágenes de GitHub Container Registry admiten `linux/amd64` y `linux/arm64`. Usa una etiqueta numérica para fijar una versión o `:latest` para seguir la versión estable más reciente. Las solicitudes de cambio a `main` pasan por CI. El flujo de publicación toma la imagen de la etiqueta de versión. Consulta la [última versión de GitHub](https://github.com/pathbit/LiteLlmRTKSync/releases/latest) para conocer la etiqueta actual. En Compose, fija la misma etiqueta numérica en el campo `image`. Para volver a una versión previa, usa la etiqueta numérica que estaba en producción. El digest del manifiesto permite comprobar que coincide con las notas de la versión en GitHub.

```bash
docker pull ghcr.io/pathbit/litellmrtksync:1.0.4
docker pull ghcr.io/pathbit/litellmrtksync:latest
```
