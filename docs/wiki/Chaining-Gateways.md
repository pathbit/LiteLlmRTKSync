# Chaining gateways: LiteLLM in front of 9Router and OmniRoute

*(Versão em português ao final.)*

9Router and OmniRoute are already routers. Each one picks an account and a final
provider on its own. So putting a third proxy in front of them only earns its
keep if it does something neither of them does — and the honest answer is that it
does nothing about routing at all. What it adds happens **before** the request
reaches a gateway.

This page is how the chain was built and, more importantly, how it was *proved*.
Every number and every command below was executed against the running stacks on
2026-09-13, and the 9Router half was re-measured on 2026-09-14 after the change
described next.

---

## 0. What changed: this stack now owns its 9Router

Until 2026-09-14 the chain pointed at `9rtk-router`, the gateway that belongs to
the **9RTKSync** stack, reached across the shared inference network. It worked,
and it was fragile in a way that only shows up on a bad day:

* bringing up *this* stack alone gave you a proxy with nowhere to chain to;
* stopping the neighbour's stack broke inference here, with nothing in this
  repository having changed;
* `docker compose down -v` over there destroyed the key this repository's `.env`
  was still holding.

So the stack grew its own gateway, `litellmrtk-9router` — same image
(`decolua/9router:latest`), its own volume, its own key, its own lifecycle, named
in this stack's pattern (`litellmrtk-<role>`, service = container_name =
hostname, like `litellmrtk-db` and `litellmrtk-router`):

| | port inside | port on host | belongs to |
|---|---|---|---|
| `litellmrtk-9router` | `20128` | `8383` | **this stack** |
| `litellmrtk-router` (LiteLLM) | `4000` | `8083` | this stack |
| `9rtk-router` | `20128` | `8081` | 9RTKSync |
| `ominirtk-router` | `20128` | `8082` | OminiRTkSync |

Two consequences worth stating plainly:

1. **`api_base` for the 9Router is now `http://litellmrtk-9router:20128/v1`**,
   resolved on this stack's *own* management network. It never leaves home.
2. **The shared inference network is no longer on the critical path.** No request
   on the 9Router chain traverses `rtk-inference-net` any more; it is now only how
   this proxy reaches a gateway in *another* stack, which means only OmniRoute.
   The declaration on `litellmrtk-router` stays for that reason, so the network
   must still exist before an `up` — see §3.1 and §5.7. Dropping the OmniRoute leg
   is what lets you drop the network too.

What did **not** change: `INITIAL_PASSWORD` and `JWT_SECRET` are required with
no default (`${VAR:?}`), exactly as in the sibling composes, so a copied-and-pasted
example can never ship with a published password. They are announced empty in
`.env.example`, section 8.

---

## 1. Why chain at all

Without the chain, a caller has to know which gateway it wants, which port that
gateway lives on and which dialect it speaks. With it, there is one address, one
dialect and one credential, and the choice of gateway becomes a **model name**:

```
$ python3 tools/registra_gateways.py --conferir
modelos de gateway registrados neste proxy:
  gateway-omniroute-granite
    model      = openai/openrouter/ibm-granite/granite-4.2-8b
    api_base   = http://ominirtk-router:20128/v1
    credencial = gateway-cred-omniroute
  gateway-9router-gemini
    model      = openai/gemini/gemini-3.8-flash
    api_base   = http://litellmrtk-9router:20128/v1
    credencial = gateway-cred-9router
```

Three things follow from that, and only the first is cosmetic:

* **One door.** Both gateways answer at `127.0.0.1:8083` under the LiteLLM key.
  The caller never holds `NINEROUTER_API_KEY` or `OMNIROUTE_API_KEY` — those are
  sent to the proxy once and stored encrypted under `LITELLM_SALT_KEY`.
* **Keys, teams and ceilings.** A gateway key is one credential with one set of
  limits. In front of it, LiteLLM issues per-team keys whose limits are checked
  for coherence — see [Rate Limit Coherence](Rate-Limit-Coherence). That is the
  capability the gateways simply do not have.
* **Accounting across both.** Spend and rate counters live in one place instead
  of one per gateway.

What the chain does **not** give you for free is failover. It is a LiteLLM
feature, but nothing configures it here, and the proxy says so itself when a
gateway is down: `Available Model Group Fallbacks=None`. Treat a chained model as
a single point of failure until you declare a fallback group.

## 2. Why two networks, and never one

Each stack owns a **management** network — its synchronizer, its database, its
gateway. Gateways that must be reached *across* stacks additionally share a
second, **inference** network:

```
$ docker network inspect rtk-inference-net --format '{{range .Containers}}{{.Name}} {{end}}'
ominirtk-router 9rtk-router litellmrtk-router
```

Only routers join it. The synchronizers stay out, because they have no business
on the inference path — and since §0, so does `litellmrtk-9router`: it is a
member of this stack, so it is reached at home and putting it on a shared network
would only expose it to the neighbours for nothing.

**The inference network exists so traffic can pass between stacks.** Without it
the proxy cannot even name a gateway that lives elsewhere; with it, the service
name resolves:

```
$ docker exec litellmrtk-router python3 -c "..."
  RESOLVE     9rtk-router -> 172.24.0.2
  RESOLVE     ominirtk-router -> 172.24.0.3
  RESOLVE     litellmrtk-db -> 172.22.0.2
```

(Addresses from the 2026-09-13 run. Docker reallocates bridge subnets when
networks are recreated, so do not read the `172.24.x` below as the same network
as the `172.24.x` here — compare the network *names*, not the octets.)

**The management network is what the 9Router chain now rides on.** Measured on
2026-09-14, after the change:

```
$ docker exec litellmrtk-router python -c "..."
resolve litellmrtk-9router -> 172.24.0.5
GET http://litellmrtk-9router:20128/api/health -> 200 {"ok":true}

$ docker inspect litellmrtk-9router --format '{{json .NetworkSettings.Networks}}'
litellmrtksync-net 172.24.0.5        # e só essa
```

That is also why `api_base` is `http://litellmrtk-9router:20128/v1` and never
`http://127.0.0.1:8383`. Inside the proxy container, loopback is the proxy.

**The management network exists so the wrong conversation cannot happen.** Before
the stacks had their own networks, all of them sat on the Docker default network,
where the same short name resolved to different stacks and nobody could tell which
gateway a panel had connected to. That is the defect the split prevents, and it
still holds:

```
$ docker exec 9rtk-sync getent hosts <nome>
  RESOLVE     9rtk-router
  nao-resolve ominirtk-router (rc=2)
  nao-resolve litellmrtk-router (rc=2)
```

Each synchronizer sees its own gateway and no other. **Delete the management
network, put everything on one, and the ambiguity comes straight back.**

### Say what this isolation is, and what it is not

It is isolation **by name**, not by route. Measured from a synchronizer, by IP
rather than by hostname:

```
$ docker exec 9rtk-sync python3 -c "..."
  ALCANCA    ominirtk-router gestao     172.21.0.2 20128
  bloqueado  ominirtk-router inferencia 172.24.0.3 20128 ( TimeoutError )
```

The management address answers; the inference address times out. Note which way
round that is — **the shared network is the one that blocks.** It added a name,
not a path: the management address was already reachable by IP before it existed,
because publishing a port opens a container to other bridges regardless of
networks.

So the split is a correctness boundary against *accidents* — a panel that would
otherwise resolve the wrong gateway — and it is not a security boundary against
someone who uses an IP on purpose. It never was, before or after. Both routers
publish only on `127.0.0.1`, which is what keeps this off the machine's network.

## 3. The procedure, from zero

### 3.1 Create the shared network first

You still need it, because `litellmrtk-router` declares it — but since §0 it is
only about **OmniRoute**. If OmniRoute is not in your picture, the 9Router chain
in this stack works without any of it.

It is declared `external: true` in all three composes, on purpose: shared by three
stacks, owned by none, so any stack may start first and stopping one never takes
the network with it. The price is that it has to exist **before** the first `up`.
All three repositories carry the target:

```bash
make rede-de-inferencia      # em 9RTKSync, OminiRTkSync e LiteLlmRTKSync
```

```
rede rtk-inference-net pronta.
```

It is idempotent — running it again prints the same line and creates nothing. The
raw equivalent, if you are not using the Makefile:

```bash
docker network create rtk-inference-net
```

`setup: rede-de-inferencia` is declared in all three Makefiles, so the normal
`make setup` path already covers it.

### 3.2 Bring up the stack

```bash
docker compose -f docker-compose.example.yml --env-file .env up -d
```

This one command now gives you the whole chain — proxy, database, **9Router** and
synchronizer. Bring up the OminiRTkSync stack too only if you want the OmniRoute
leg.

Before it runs, fill `INITIAL_PASSWORD` and `JWT_SECRET` in `.env`. They have no
default on purpose, and the refusal names the variable, never a value:

```
$ docker compose -f docker-compose.example.yml config -q
error while interpolating services.litellmrtk-9router.environment.[]: required
variable INITIAL_PASSWORD is missing a value: defina INITIAL_PASSWORD no .env
error while interpolating services.litellmrtk-9router.environment.[]: required
variable JWT_SECRET is missing a value: defina JWT_SECRET no .env (openssl rand -hex 32)
```

With both filled, the gateway comes up and reports healthy on its own
`/api/health` — not on `/dashboard`, which answers `307` and would let a gateway
that only knows how to redirect pass as healthy:

```
$ docker ps --filter name=litellmrtk-9router
litellmrtk-9router  decolua/9router:latest  127.0.0.1:8383->20128/tcp  Up 11 seconds (healthy)

$ curl -s http://127.0.0.1:8383/api/health
{"ok":true}
```

### 3.3 Register at least one account in each gateway

A freshly installed gateway routes nothing, because it has no account to route
*to*, and the model ids in the next step will not exist. This is done in each
gateway's own panel (`127.0.0.1:8383` for this stack's 9Router, `127.0.0.1:8082`
for OmniRoute).

The 9Router log names the account in use on every request, as `ACC:<conta>` —
which is what makes the proof in §4 identify the *instance*, not just the image.

Measured on the freshly created `litellmrtk-9router`, which is the case the page
used to leave open. Panel session first (`POST /api/auth/login` with
`INITIAL_PASSWORD`, session comes back in the `auth_token` cookie), then
the gateway's own `POST /api/providers` with
`{"provider","authType","name","apiKey",...}` — the key field is called `apiKey`:

```
login no painel -> HTTP 200; cookies: ['auth_token']
provedores ja cadastrados: (nenhum)
conta gemini criada -> HTTP 201; chave AQ.Ab8*** (len=53)
```

### 3.4 Emit one key per gateway

Both gateways require their own key on `/v1/*`, validated against each one's own
local database. They are not interchangeable, which is why there are two
variables. This is the only manual step in the whole path, so it is worth being
precise about it.

The key routes need a **panel session cookie**, not a bearer header:

```
$ curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8383/api/keys   -> 401
$ curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8082/api/keys   -> 401
```

The session comes from the gateway's own `POST /api/auth/login`, with the password
in the body; on the gateway it arrives back as a cookie named `auth_token`:

```
gateway 9rtk-router (127.0.0.1:8081)
POST /api/auth/login -> HTTP 200 | corpo: {"success":true,"mustChangePassword":false}
cookies de sessao recebidos: ['auth_token']
```

With that cookie, the gateway's `GET /api/keys` lists what exists, and the key
value lives in a field called `key` — measured against the 9Router:

```
gateway 9rtk-router (127.0.0.1:8081)
GET /api/keys COM sessao -> HTTP 200 | chaves: 2
   id=ddc65e6b…  name='Default Key'     campos=['createdAt','id','isActive','key','machineId','name']
   id=487eb9dd…  name='litellm-bridge'  campos=['createdAt','id','isActive','key','machineId','name']
```

On that same gateway session, creating one is `POST /api/keys` with
`{"name": "litellm-bridge"}`; the panel does the same thing. Measured on
`litellmrtk-9router`, which closes the gap this page used to leave open — creating
a key on a *shared* gateway was a side effect nobody wanted, and on a gateway this
stack owns it is simply part of setup:

```
chave 'litellm-bridge' criada -> HTTP 201
valor da chave: sk-0fe*** (len=35)
NINEROUTER_API_KEY gravada no .env (gitignored)
```

Write the value into `LiteLlmRTKSync/.env` **in the same step** you create it:

```
NINEROUTER_API_KEY=sk-...
OMNIROUTE_API_KEY=sk-...
```

Do it with a script that reads and writes the file, not with `sed` on the command
line — an argument lands in `ps` and in shell history.

[A MEDIR: se o OmniRoute mostra o valor da chave uma vez só — comportamento de
painel, não conferido aqui. Na dúvida, grave no `.env` antes de fechar a tela.]

### 3.5 Find a model id that the gateway actually serves

List the catalogue with the key you just emitted:

```
9Router    /v1/models COM chave -> HTTP 200  bytes=18751
OmniRoute  /v1/models COM chave -> HTTP 200  bytes=683123
```

A gateway with exactly one account has a catalogue to match, which makes the
point better than a large one does — this is `litellmrtk-9router` right after the
single Gemini account of §3.3:

```
GET /v1/models -> 200 | modelos: 8
com 'flash' no id: ['gemini/gemini-3.8-flash', 'gemini/gemini-3.7-flash', ...]
```

Do not stop there. See §5.1 — being in the catalogue does not mean being served.

### 3.6 Register the gateways as providers

```bash
python3 tools/registra_gateways.py
```

On a clean install, where neither the credential nor the model exists yet:

```
[9router] → http://litellmrtk-9router:20128/v1
  chave NINEROUTER_API_KEY = sk-0fe*** (len=35)
  + credencial 'gateway-cred-9router' criada a partir de NINEROUTER_API_KEY
  + modelo 'gateway-9router-gemini' criado → openai/gemini/gemini-3.8-flash

[omniroute] → http://ominirtk-router:20128/v1
  chave OMNIROUTE_API_KEY = sk-cf2*** (len=35)
  + credencial 'gateway-cred-omniroute' criada a partir de OMNIROUTE_API_KEY
  + modelo 'gateway-omniroute-granite' criado → openai/openrouter/ibm-granite/granite-4.2-8b

2 modelo(s) criado(s) nesta execução.
```

It reads both keys from the environment, never from `argv` — a command-line
argument shows up in `ps` and in shell history — and it never prints a value, only
prefix and length. It is idempotent; a second run writes nothing:

```
  = credencial 'gateway-cred-9router' já existe
  = modelo 'gateway-9router-gemini' já existe → http://litellmrtk-9router:20128/v1
  = credencial 'gateway-cred-omniroute' já existe
  = modelo 'gateway-omniroute-granite' já existe → http://ominirtk-router:20128/v1
0 modelo(s) criado(s) nesta execução.
```

Use `--dry-run` to see what it would do and `--conferir` to see what is already
registered.

#### Idempotence by name alone was not enough

Repointing the chain exposed a real hole in this script. It used to check only
whether a model **name** existed, so after `api_base` changed in the source it
reported `= já existe` and left the *old* destination sitting in Postgres. The
call still returned 200, and the proof line still appeared — in the neighbour's
log. It now compares the destination too:

```
$ python3 tools/registra_gateways.py --dry-run
  ~ modelo 'gateway-9router-gemini' seria reapontado:
    http://9rtk-router:20128/v1 → http://litellmrtk-9router:20128/v1
```

The reconciliation deletes and recreates rather than patching, because
`/model/update` is not present in every proxy version and the id is derived from
the model name, so it comes back identical. Applied, on the proxy that had been
chained to the neighbour since 2026-09-13 — this is what an *existing* install
prints, not the clean-install block above:

```
[9router] → http://litellmrtk-9router:20128/v1
  chave NINEROUTER_API_KEY = sk-0fe*** (len=35)
  ~ credencial 'gateway-cred-9router' atualizada com a chave atual
  ~ modelo 'gateway-9router-gemini' reapontado:
    http://9rtk-router:20128/v1 → http://litellmrtk-9router:20128/v1
0 modelo(s) criado(s) nesta execução.
```

## 4. How to test — and which proof actually counts

```bash
python3 tools/registra_gateways.py --testar
```

```
[9router] chamando 'gateway-9router-gemini-flash' …
  ✓ HTTP 200 · conteúdo: 'PROVA-9ROUTER'
    tokens: entrada=2132 saída=6
    confira no gateway:  docker logs 9rtk-router --since 2m | tail -20

[omniroute] chamando 'gateway-omniroute-granite' …
  ✓ HTTP 200 · conteúdo: 'PROVA-OMNIROUTE'
    tokens: entrada=31 saída=94
```

**That output is not the proof.** It is the prompt to go and find it.

### The proof that does not count

A pretty answer from LiteLLM only tells you that *something* answered. It cannot,
by itself, rule out a cached reply, a fallback to another provider, or a second
deployment under the same name. And on OmniRoute it is much weaker than it looks —
see §5.2, where a call with **no key at all** returns HTTP 200.

### The proof that counts: the line in the gateway's own log

Send a unique nonce, note the host clock in UTC, then read the gateway log with
`docker logs -t`. The in-app stamp is the container's timezone; only `-t` gives
you something comparable.

**After the change of §0 there is a sharper version of this test**, and it is the
one that shows the stack is self-sufficient: run the call, then grep the *same*
window in both 9Routers. Measured 2026-09-14, window opening at `01:04:01Z`:

```
$ python3 tools/registra_gateways.py --testar
[9router] chamando 'gateway-9router-gemini' …
  ✓ credencial aceita pelo gateway (/v1/models)
  ✓ HTTP 200 · conteúdo: 'PROVA-9ROUTER'
    confira no gateway:  docker logs litellmrtk-9router --since 2m | tail -20

$ docker logs -t litellmrtk-9router --since 3m | grep -iE "gemini|DONE"
2026-09-14T01:04:01.927Z [01:04:01] 🟢 ▶ POST gemini/gemini-3.8-flash → gemini/gemini-3.8-flash
                                    · FMT: openai→gemini · 1 MSG · ACC:Google AI Studio (LiteLlmRTKSync)
2026-09-14T01:04:03.306Z [01:04:03] 🟢 📊 DONE 1405ms · IN 15 · OUT 6

$ docker logs -t 9rtk-router --since 3m | grep -iE "gemini|DONE"
(nothing)
```

Three things make that conclusive. The **timestamp** matches to the tenth of a
second. The **account name** is `Google AI Studio (LiteLlmRTKSync)` — the one
created in §3.3, in *this* stack's gateway and nowhere else. And the neighbour's
log is **empty in the same window**, which is what rules out the answer having
come from where it used to come from.

The proxy says the same thing in a header, which is cheaper to check than a log:

```
x-litellm-model-api-base: http://litellmrtk-9router:20128/v1
x-litellm-model-id: gateway-9router-gemini
conteudo: 'PROVA-CABECALHO'
```

The original 2026-09-13 measurement, against the neighbour's gateway, is kept
below because the *method* is what matters.

Through the proxy:

```
NONCE  = NONCE-1789329755-9R
ANTES(UTC) = 2026-09-13T20:02:35Z
HTTP = 200  tempo = 2.262s
x-litellm-model-api-base: http://9rtk-router:20128/v1
x-litellm-attempted-fallbacks: 0  retries: 0
conteudo = 'NONCE-1789329755-9R'
usage completion_tokens = 16
```

In the gateway:

```
$ docker logs -t 9rtk-router --since 3m | grep -E "POST ag/gemini|DONE"
2026-09-13T20:02:35.301310674Z [20:02:35] ▶ POST ag/gemini-3.8-flash → antigravity/gemini-3.8-flash · ACC:<conta>
2026-09-13T20:02:37.465544383Z [20:02:37] 📊 DONE 2167ms · IN 25 · OUT 16
```

Three independent things match: the **timestamp** (20:02:35Z against
20:02:35.301Z), the **duration** (2.262 s against 2167 ms) and the **token count**
(`completion_tokens 16` against `OUT 16`). A cache or a different provider would
break all three.

On OmniRoute the log carries the id of the key instead:

```
$ docker logs -t ominirtk-router --since 3m | grep -iE "apiKeyId|POST /v1/chat"
2026-09-13T20:04:07.123211341Z [SKILLS_INJECTION] {"apiKeyId":"11e94a34-efa5-4b9c-8c84-3e8c243633df",...}
2026-09-13T20:04:07.136928716Z ... "msg":"📥 POST /v1/chat/completions | openrouter/ibm-granite/granite-4.2-8b | 1 msgs"
```

That `apiKeyId` is the key created for this bridge. It is the only thing on
OmniRoute that proves the credential was used.

### The decisive test: take the gateway away

If the answer survives the gateway being down, it never came from the gateway.

```
$ docker stop 9rtk-router
9rtk-router parado. na rede de inferencia agora: ominirtk-router litellmrtk-router

HTTP=500 tempo=26.016s
{"error":{"message":"litellm.InternalServerError: OpenAIException - Connection error..
 Received Model Group=gateway-9router-gemini-flash
 Available Model Group Fallbacks=None"}}

$ docker start 9rtk-router
```

Since §0, **this test no longer runs against `9rtk-router`** — stopping it now
proves nothing here, because nothing here depends on it. Run it against
`docker stop litellmrtk-9router` instead. That inversion is the change, stated as
an experiment: the container whose absence breaks this stack is the one this
stack owns.

Answer gone, and `Fallbacks=None` confirms nothing else quietly served it. Note
the **26 seconds** — a chained call that seems to hang is often a gateway that is
down, not a slow model.

## 5. What goes wrong, and what you will actually see

### 5.1 A model id in the catalogue is not a model the gateway serves

This is the trap most worth knowing. OmniRoute's catalogue announces 1749 ids, and
`groq/llama-3.3-70b-versatile` is one of them:

```
total de ids no catalogo: 1749
contem 'groq/llama-3.3-70b-versatile'? True
```

Ask for it on the inference path and it is refused — with or without a valid key:

```
groq/llama-3.3-70b-versatile -> HTTP 400
{"error":{"message":"Model 'llama-3.3-70b-versatile' is not available in the
 active live catalog for provider 'groq'.","code":"bad_request"}}
```

Announcement and service are two different lists. **Always send one real call to
an id before pinning it.** `openrouter/ibm-granite/granite-4.2-8b` was chosen here
because it answered three out of three times, exactly.

### 5.2 On OmniRoute, HTTP 200 does not mean your key worked

9Router rejects an anonymous or wrong key on the chat path:

```
9router  /v1/chat/completions  SEM chave   -> 401 {"error":"API key required for remote API access"}
9router  /v1/chat/completions  chave BOGUS -> 401
```

OmniRoute does not — inference is open:

```
omniroute /v1/chat/completions SEM chave -> HTTP 200
```

Measured in a clean log window, one keyless call leaves a request line and **no
credential trace at all**:

```
  baseline (janela vazia) POST chat: 0
  omniroute SEM chave -> HTTP 200
  POST chat na janela : 1
  apiKeyId na janela  : 0
```

So with `OMNIROUTE_API_KEY` empty, wrong, rotated or revoked, `--testar` would
still print `✓ HTTP 200`. The absence of `apiKeyId` in the log is the only signal.

**The endpoint that does prove the key is `/v1/models`**, and it behaves the same
on both gateways:

```
9router  /v1/models sem chave -> 401      com chave BOGUS -> 401      com a chave -> 200
omniroute /v1/models sem chave -> 401     com chave BOGUS -> 401      com a chave -> 200
```

Use that, not a chat call, whenever the question is "is this key good?".

### 5.3 `REQUIRE_API_KEY=false` does not mean "no key needed"

The flag sits in both gateway composes and reads like permission to skip the key.
It is not. 9Router authorises by **peer**: loopback is trusted, a neighbour on a
Docker network is remote. The message says so — *remote*:

```
401  {"error":"API key required for remote API access"}
```

The proxy reaches the gateway as a container neighbour, so it is always remote and
always needs a key, whatever that flag says.

### 5.4 A rotated key never reaches the proxy on its own

`registrar` skips a credential that already exists, by name:

```
  = credencial 'gateway-cred-omniroute' já existe
```

Change the key in `.env` and run the script again and **nothing is updated** — the
proxy keeps the old value in Postgres. On 9Router you find out through a 401; on
OmniRoute, per §5.2, you never find out. Delete the credential in LiteLLM before
re-running, and verify with `/v1/models`.

The script now recreates the credential on every run for exactly that reason, and
since §3.6 it reconciles the model's `api_base` too. What it cannot do anything
about is the cause: the key lives in the gateway's SQLite, which lives in a
volume, so `docker compose down -v` destroys it while `.env` keeps holding the
dead value. That is true of `litellmrtksync_9router` just as it was of the
neighbour's volume — owning the gateway did not make the key durable, it only made
re-issuing it a step you can take without touching another repository.

### 5.5 Renaming a model collides on an id you cannot see

Each gateway gets a fixed `model_info.id` (`gateway-9router`, `gateway-omniroute`),
so there is one model per gateway. Idempotence is checked by *model name*, but the
database key is the id — so registering a renamed model while the old one is still
there fails with a message that names neither:

```
falhou: POST /model/new: HTTP 500 —
{"error":{"message":"{'error': 'Failed to add model to db. ...'}","code":"500"}}
```

Delete the old model by its id first (`POST /model/delete` with
`{"id": "gateway-omniroute"}`), then re-run.

### 5.6 `max_tokens` is a budget that reasoning eats first

A reasoning model spends the output budget thinking before it writes anything. At
40 tokens the answer came back **empty**, which reads exactly like a broken
integration and is not one:

```
max_tokens=40   HTTP 200 finish=length  conteudo=''
                reasoning_tokens=41
max_tokens=200  HTTP 200 finish=stop    conteudo='PROVA-OMNIROUTE'
                reasoning_tokens=86  completion_tokens=96
```

Empty `content` together with `finish_reason: length` means the budget ran out
during reasoning. Raise `max_tokens`; the transport was never the problem.

### 5.7 On a clean clone, every `up` fails until the network exists

Because the network is `external: true`, and this now affects all three stacks,
including the two gateways that used to start on their own:

```
$ docker compose up -d
network rede-que-nao-existe-ainda declared as external, but could not be found
```

Run `make rede-de-inferencia` (§3.1) first. The message names the missing network
but does not tell you to create it.

Since §0 this is the last tie between this stack and the shared network:
`litellmrtk-router` still declares it so it can reach OmniRoute. If you drop the
OmniRoute leg, you can drop the declaration too and this failure mode goes with
it.

### 5.8 A combo model makes a bad test

OmniRoute's `auto/best-fast` lets the gateway choose the account and provider,
which is the right thing in production and the wrong thing in a test. Four
identical calls, same prompt, same parameters:

```
1) servido=inference-net/schematron-v2-turbo  finish=stop  conteudo='{\n  "result": "PROVA-OMNIROUTE"\n}'
2) servido=inference-net/schematron-v2-turbo  finish=stop  conteudo='PROVA-OMNIROUTE'
3) servido=inference-net/schematron-v2-turbo  finish=stop  conteudo='PROVA-OMNIROUTE'
4) servido=inference-net/schematron-v2-turbo  finish=stop  conteudo='**Result:**  \nThe provided text contains no information that matches the re…'
```

The combo resolved to an **extraction** model every time, and even so only two of
the four answers matched exactly — one arrived wrapped in JSON, one was an
extraction-style refusal. A test that fails that way is not measuring the
integration, it is measuring the draw, and it produces false negatives (right
transport, different text) as easily as false positives. Register a concrete id
for testing, and use the combo for real traffic.

---

# Em português

9Router e OmniRoute já são roteadores — cada um escolhe conta e provedor sozinho.
Pôr um terceiro proxy na frente só se paga se ele fizer algo que nenhum dos dois
faz, e a resposta honesta é que ele não faz nada quanto a roteamento. O que ele
acrescenta acontece **antes** de a requisição chegar ao gateway.

Tudo abaixo foi executado contra as stacks em execução em 13/09/2026, e a parte
do 9Router foi remedida em 14/09/2026, depois da mudança descrita a seguir.

## 0. O que mudou: esta stack passou a ter o próprio 9Router

Até 14/09/2026 o encadeamento apontava para o `9rtk-router`, o gateway que
pertence à stack do **9RTKSync**, alcançado pela rede de inferência
compartilhada. Funcionava, e era frágil de um jeito que só aparece em dia ruim:

* subir *esta* stack sozinha dava um proxy sem para onde encadear;
* derrubar a stack do vizinho quebrava a inferência aqui, sem que nada neste
  repositório tivesse mudado;
* um `docker compose down -v` lá destruía a chave que o `.env` daqui continuava
  guardando.

Então a stack ganhou o gateway dela, `litellmrtk-9router` — mesma imagem
(`decolua/9router:latest`), volume próprio, chave própria, ciclo de vida próprio,
nomeado no padrão desta stack (`litellmrtk-<papel>`, serviço = container_name =
hostname, como `litellmrtk-db` e `litellmrtk-router`):

| | porta interna | porta no host | pertence a |
|---|---|---|---|
| `litellmrtk-9router` | `20128` | `8383` | **esta stack** |
| `litellmrtk-router` (LiteLLM) | `4000` | `8083` | esta stack |
| `9rtk-router` | `20128` | `8081` | 9RTKSync |
| `ominirtk-router` | `20128` | `8082` | OminiRTkSync |

Duas consequências que vale dizer sem rodeio:

1. **O `api_base` do 9Router agora é `http://litellmrtk-9router:20128/v1`**,
   resolvido na rede de gestão *desta* stack. Não sai de casa.
2. **A rede de inferência compartilhada saiu do caminho crítico.** Nenhuma
   requisição da cadeia do 9Router passa mais pela `rtk-inference-net`; ela agora
   é só como este proxy alcança um gateway de *outra* stack, ou seja, só o
   OmniRoute. A declaração no `litellmrtk-router` fica por causa disso, então a
   rede ainda precisa existir antes de um `up` — veja o passo 1 e o §5.7. Quem
   abre mão da perna do OmniRoute é que pode abrir mão da rede também.

O que **não** mudou: `INITIAL_PASSWORD` e `JWT_SECRET` são obrigatórias e sem
valor padrão (`${VAR:?}`), exatamente como nos composes irmãos, para que um
exemplo copiado e colado nunca saia com senha publicada. Estão anunciadas vazias
no `.env.example`, seção 8.

## 1. Por que encadear

Sem a cadeia, quem chama precisa saber qual gateway quer, em que porta ele vive e
que dialeto ele fala. Com ela há um endereço, um dialeto e uma credencial, e a
escolha do gateway vira um **nome de modelo** (`gateway-9router-gemini`,
`gateway-omniroute-granite`).

* **Uma porta só.** Os dois gateways atendem em `127.0.0.1:8083` sob a chave do
  LiteLLM. Quem chama nunca segura `NINEROUTER_API_KEY` nem `OMNIROUTE_API_KEY`.
* **Chaves, times e tetos.** Uma chave de gateway é uma credencial com um conjunto
  de limites. Na frente dela, o LiteLLM emite chaves por time com limites
  conferidos — veja [Rate Limit Coherence](Rate-Limit-Coherence). É a capacidade
  que os gateways não têm.
* **Contabilidade num lugar só**, em vez de uma por gateway.

O que a cadeia **não** dá de graça é failover. Existe no LiteLLM, mas nada o
configura aqui, e o próprio proxy avisa: `Available Model Group Fallbacks=None`.

## 2. Por que são duas redes, e nunca uma

Cada stack tem sua rede de **gestão**. Os routers que precisam ser alcançados
*entre* stacks compartilham uma segunda rede, de **inferência**
(`ominirtk-router 9rtk-router litellmrtk-router`). Os sincronizadores ficam de
fora — e, desde a seção 0, o `litellmrtk-9router` também: ele é de casa, e pô-lo
numa rede compartilhada só o exporia aos vizinhos sem dar nada em troca.

**A de inferência existe para o tráfego passar entre stacks.** Sem ela o proxy nem
nomeia um gateway que mora em outro lugar; com ela, `ominirtk-router ->
172.24.0.3` resolve de dentro do container.

**A de gestão é por onde a cadeia do 9Router passa agora.** Medido em
14/09/2026, depois da mudança:

```
$ docker exec litellmrtk-router python -c "..."
resolve litellmrtk-9router -> 172.24.0.5
GET http://litellmrtk-9router:20128/api/health -> 200 {"ok":true}

$ docker inspect litellmrtk-9router --format '{{json .NetworkSettings.Networks}}'
litellmrtksync-net 172.24.0.5        # e só essa
```

É por isso que `api_base` é `http://litellmrtk-9router:20128/v1` e nunca
`http://127.0.0.1:8383` — dentro do container do proxy, o loopback é o proxy.

**A de gestão existe para a conversa errada não acontecer.** Antes, com todas as
stacks na rede default do Docker, o mesmo nome curto resolvia para stacks
diferentes e ninguém sabia a qual gateway um painel havia se conectado. É esse o
defeito que a separação previne, e ele continua prevenido: `9rtk-sync` resolve
`9rtk-router` e **não** resolve `ominirtk-router` nem `litellmrtk-router`.
**Quem juntar tudo numa rede só traz a ambiguidade de volta.**

### O que esse isolamento é, e o que não é

É isolamento **por nome**, não por rota. Medido por IP a partir de um
sincronizador, o endereço de *gestão* do irmão responde (`172.21.0.2:20128`) e o
de *inferência* dá timeout (`172.24.0.3:20128`). Repare no sentido: **a rede
compartilhada é a que bloqueia.** Ela acrescentou um nome, não um caminho — o
endereço de gestão já era alcançável por IP antes dela existir, porque publicar
porta abre o container para outras bridges independentemente de rede.

Ou seja: é fronteira de correção contra **acidente** — um painel que resolveria o
gateway errado — e não fronteira de segurança contra quem use IP de propósito.
Nunca foi, nem antes nem depois. Os dois routers publicam só em `127.0.0.1`.

## 3. O procedimento, do zero

0. **Preencher `INITIAL_PASSWORD` e `JWT_SECRET` no `.env`.** Sem elas o compose
   recusa subir, e a recusa nomeia a variável, nunca um valor:

   ```
   $ docker compose -f docker-compose.example.yml config -q
   error while interpolating services.litellmrtk-9router.environment.[]: required
   variable INITIAL_PASSWORD is missing a value: defina INITIAL_PASSWORD no .env
   ```

1. **Criar a rede de inferência**, se você for usar o OmniRoute. Desde a seção 0
   ela é só para isso: a cadeia do 9Router desta stack não depende dela. Ela é
   `external: true` de propósito — compartilhada e de ninguém, então qualquer
   stack pode subir primeiro e derrubar uma não leva a rede junto. O preço é
   precisar existir antes do primeiro `up`. Os três repositórios têm o alvo:

   ```bash
   make rede-de-inferencia
   ```

   Imprime `rede rtk-inference-net pronta.` e é idempotente. O equivalente cru é
   `docker network create rtk-inference-net`. Os três Makefiles declaram `setup: rede-de-inferencia`, então o `make setup`
   normal já cobre isso.

2. **Subir a stack** com `docker compose ... up -d`. Um comando só entrega a
   cadeia inteira — proxy, banco, **9Router** e sincronizador. O gateway sobe
   saudável pelo próprio `/api/health` (e não por `/dashboard`, que responde 307
   e deixaria passar por saudável um gateway que só sabe redirecionar):

   ```
   litellmrtk-9router  decolua/9router:latest  127.0.0.1:8383->20128/tcp  Up 11 seconds (healthy)
   $ curl -s http://127.0.0.1:8383/api/health
   {"ok":true}
   ```

3. **Cadastrar ao menos uma conta em cada gateway**, pelo painel de cada um
   (`127.0.0.1:8383` para o 9Router desta stack, `127.0.0.1:8082` para o
   OmniRoute). Gateway recém-instalado não roteia nada, e os ids de modelo do
   passo 5 não existem. Medido no `litellmrtk-9router` recém-criado, que é o caso
   que esta página deixava em aberto — sessão de painel primeiro
   (`POST /api/auth/login` com o `INITIAL_PASSWORD`; a sessão volta no cookie
   `auth_token`), depois o `POST /api/providers` do próprio gateway, em que o
   campo da chave se chama `apiKey`:

   ```
   login no painel -> HTTP 200; cookies: ['auth_token']
   provedores ja cadastrados: (nenhum)
   conta gemini criada -> HTTP 201; chave AQ.Ab8*** (len=53)
   ```

4. **Emitir uma chave em cada gateway.** As duas são validadas contra o banco
   local de cada um: não são intercambiáveis, por isso são duas variáveis. As
   rotas de chave exigem **sessão de painel**, não header — sem sessão,
   `/api/keys` responde 401 nos dois. Grave em `LiteLlmRTKSync/.env` no mesmo
   passo: a do OmniRoute aparece uma vez só. Grave com um script que lê e
   escreve o arquivo, não com `sed` na linha de comando — argumento aparece em
   `ps` e no histórico.

   ```
   chave 'litellm-bridge' criada -> HTTP 201
   valor da chave: sk-0fe*** (len=35)
   NINEROUTER_API_KEY gravada no .env (gitignored)
   ```

5. **Achar um id de modelo que o gateway sirva**, listando `/v1/models` com a
   chave (9Router devolveu 18751 bytes; OmniRoute, 683123). Um gateway com uma
   conta só tem catálogo do tamanho dela, e isso mostra melhor o ponto: o
   `litellmrtk-9router`, logo depois da única conta Gemini do passo 3, devolveu
   `modelos: 8`, entre eles o `gemini/gemini-3.8-flash` que é usado aqui. Não
   pare aí — veja §5.1.

6. **Cadastrar**, com `python3 tools/registra_gateways.py`. Ele lê as duas chaves
   do ambiente, nunca de `argv` — argumento aparece em `ps` e no histórico — e
   nunca imprime o valor, só prefixo e tamanho. É idempotente: a segunda execução
   diz `= já existe` e cria `0 modelo(s)`.

   **Idempotência por nome não bastava.** Reapontar a cadeia expôs um buraco real
   neste script: ele conferia só se o **nome** do modelo existia, então, depois
   que o `api_base` mudou na fonte, ele dizia `= já existe` e deixava o destino
   *antigo* no Postgres. A chamada continuava respondendo 200, e a linha de prova
   continuava aparecendo — no log do vizinho. Agora ele compara o destino também:

   ```
   ~ modelo 'gateway-9router-gemini' seria reapontado:
     http://9rtk-router:20128/v1 → http://litellmrtk-9router:20128/v1
   ```

   A reconciliação apaga e recria em vez de atualizar, porque `/model/update` não
   existe em toda versão do proxy e o id deriva do nome do modelo — ou seja, ele
   volta idêntico.

## 4. Como testar — e qual prova vale

`python3 tools/registra_gateways.py --testar` devolve
`✓ HTTP 200 · conteúdo: 'PROVA-9ROUTER'` e `'PROVA-OMNIROUTE'`. **Essa saída não
é a prova** — é o convite para ir buscá-la.

**A prova que não vale:** uma resposta bonita do LiteLLM só diz que *alguma coisa*
respondeu. Sozinha, não exclui cache, fallback para outro provedor nem um segundo
deployment de mesmo nome. E no OmniRoute vale ainda menos do que parece — lá uma
chamada **sem chave nenhuma** devolve HTTP 200 (§5.2).

**A prova que vale é a linha no log do gateway.** Mande um nonce único, anote o
relógio do host em UTC e leia com `docker logs -t` — o carimbo do app é o fuso do
container. No 9Router casam três coisas independentes: horário
(`2026-09-13T20:02:35Z` contra `20:02:35.301Z`), duração (2,262 s contra 2167 ms)
e tokens (`completion_tokens 16` contra `OUT 16`). Cache ou outro provedor
quebraria as três. O cabeçalho `x-litellm-model-api-base` diz para onde foi, e
`x-litellm-attempted-fallbacks: 0` diz que não houve desvio.

**Depois da mudança da seção 0 existe uma versão mais afiada desse teste**, e é
ela que mostra a stack autossuficiente: rode a chamada e depois faça o mesmo grep,
na mesma janela, nos DOIS 9Routers. Medido em 14/09/2026, janela abrindo em
`01:04:01Z`:

```
$ python3 tools/registra_gateways.py --testar
[9router] chamando 'gateway-9router-gemini' …
  ✓ credencial aceita pelo gateway (/v1/models)
  ✓ HTTP 200 · conteúdo: 'PROVA-9ROUTER'
    confira no gateway:  docker logs litellmrtk-9router --since 2m | tail -20

$ docker logs -t litellmrtk-9router --since 3m | grep -iE "gemini|DONE"
2026-09-14T01:04:01.927Z [01:04:01] 🟢 ▶ POST gemini/gemini-3.8-flash → gemini/gemini-3.8-flash
                                    · FMT: openai→gemini · 1 MSG · ACC:Google AI Studio (LiteLlmRTKSync)
2026-09-14T01:04:03.306Z [01:04:03] 🟢 📊 DONE 1405ms · IN 15 · OUT 6

$ docker logs -t 9rtk-router --since 3m | grep -iE "gemini|DONE"
(nada)
```

Três coisas tornam isso conclusivo. O **horário** casa no décimo de segundo. O
**nome da conta** é `Google AI Studio (LiteLlmRTKSync)` — a criada no passo 3, no
gateway *desta* stack e em nenhum outro. E o log do vizinho está **vazio na mesma
janela**, que é o que descarta a resposta ter vindo de onde ela vinha antes.

O proxy diz a mesma coisa num cabeçalho, mais barato de conferir que um log:

```
x-litellm-model-api-base: http://litellmrtk-9router:20128/v1
x-litellm-model-id: gateway-9router-gemini
conteudo: 'PROVA-CABECALHO'
```

No OmniRoute o que casa é o `apiKeyId` (`11e94a34-efa5-4b9c-8c84-3e8c243633df`),
o id da chave criada para esta ponte — a única coisa que prova, lá, que a
credencial foi usada.

**O teste decisivo é tirar o gateway do ar.** Se a resposta sobrevive, ela nunca
veio de lá. Com `docker stop 9rtk-router`, a mesma chamada devolveu `HTTP=500` em
26,016 s com `Available Model Group Fallbacks=None` — resposta sumiu, e nada a
serviu em silêncio. Guarde os **26 segundos**: chamada encadeada que parece travar
costuma ser gateway fora do ar, não modelo lento.

Desde a seção 0, **esse teste não se faz mais com o `9rtk-router`**: pará-lo agora
não prova nada aqui, porque nada aqui depende dele. Faça com
`docker stop litellmrtk-9router`. Essa inversão *é* a mudança, dita como
experimento: o container cuja ausência quebra esta stack é o que esta stack tem.

## 5. O que dá errado e não é óbvio

**5.1 Estar no catálogo não é ser servido.** O catálogo do OmniRoute anuncia 1749
ids e contém `groq/llama-3.3-70b-versatile` — mas o caminho de inferência recusa
esse mesmo id com HTTP 400 `Model 'llama-3.3-70b-versatile' is not available in
the active live catalog for provider 'groq'`, com chave válida ou sem. Anúncio e
serviço são listas diferentes: **mande uma chamada real antes de fixar um id.**

**5.2 No OmniRoute, HTTP 200 não quer dizer que sua chave funcionou.** O 9Router
recusa chamada anônima ou com chave falsa (401 nos dois casos). O OmniRoute não —
a inferência é aberta. Numa janela de log limpa, uma chamada sem chave deixou a
linha de requisição e **nenhum rastro de credencial**: `POST chat na janela: 1`,
`apiKeyId na janela: 0`. Com `OMNIROUTE_API_KEY` vazia, trocada ou revogada, o
`--testar` imprimiria `✓ HTTP 200` do mesmo jeito. **Quem prova a chave é
`/v1/models`**, que responde 401 sem chave e 401 com chave falsa nos **dois**
gateways. Use ele, não uma chamada de chat, quando a pergunta for "esta chave
presta?".

**5.3 `REQUIRE_API_KEY=false` não quer dizer "não precisa de chave".** A flag está
nos composes dos dois gateways e parece permissão para pular a chave. Não é. O
9Router autoriza por **peer**: loopback é confiado, vizinho de rede Docker é
remoto — e a mensagem diz isso, *remote*:
`{"error":"API key required for remote API access"}`. O proxy chega como vizinho
de container, então é sempre remoto e sempre precisa de chave.

**5.4 Chave rotacionada não chega ao proxy sozinha.** O script pula credencial
existente pelo nome (`= credencial 'gateway-cred-omniroute' já existe`). Troque a
chave no `.env`, rode de novo e **nada é atualizado** — o Postgres do LiteLLM
segue com o valor antigo. No 9Router você descobre por um 401; no OmniRoute, pelo
§5.2, não descobre nunca. Apague a credencial no LiteLLM antes de recadastrar e
confira com `/v1/models`.

O script hoje recria a credencial a cada execução justamente por isso, e desde o
passo 6 reconcilia também o `api_base` do modelo. O que ele não tem como resolver
é a causa: a chave mora no SQLite do gateway, que mora num volume, então um
`docker compose down -v` a destrói enquanto o `.env` continua guardando o valor
morto. Vale igual para o `litellmrtksync_9router` — ter o gateway em casa não
tornou a chave durável, só tornou reemiti-la um passo que se dá sem entrar em
outro repositório.

**5.5 Renomear modelo colide num id que você não vê.** Cada gateway tem um
`model_info.id` fixo (`gateway-9router`, `gateway-omniroute`), então é um modelo
por gateway. A idempotência olha o *nome*, mas a chave do banco é o **id** — então
cadastrar um modelo renomeado com o antigo ainda lá falha com
`HTTP 500 — Failed to add model to db`, que não nomeia nem um nem outro. Apague o
modelo antigo pelo id (`POST /model/delete` com `{"id": "gateway-omniroute"}`) e
rode de novo.

**5.6 `max_tokens` é orçamento que o raciocínio come primeiro.** Com 40, a
resposta voltou **vazia** — 41 tokens gastos pensando, `finish_reason: length` — o
que se lê como integração quebrada sem ser. Com 200 (86 de raciocínio, 96 no
total) a palavra sai inteira. `content` vazio junto de `finish_reason: length`
significa orçamento estourado no raciocínio; aumente o `max_tokens`, o transporte
nunca foi o problema.

**5.7 Em clone limpo, todo `up` falha até a rede existir.** Como ela é
`external: true`, isso agora vale para as três stacks, inclusive os dois gateways
que antes subiam sozinhos: `network ... declared as external, but could not be
found`. Rode `make rede-de-inferencia` antes. A mensagem nomeia a rede que falta,
mas não manda criá-la. Desde a seção 0 esse é o último laço entre esta stack e a
rede compartilhada: o `litellmrtk-router` ainda a declara para alcançar o
OmniRoute. Quem abrir mão da perna do OmniRoute pode abrir mão da declaração — e
desta falha junto.

**5.8 Modelo combo dá teste ruim.** O `auto/best-fast` deixa o OmniRoute escolher
conta e provedor — ótimo em produção, péssimo em teste: o combo caiu num modelo de
**extração**, que devolveu `{"answer":...}`, uma data sem relação e um texto
divagante antes de ecoar a palavra pedida. Um teste que falha assim não mede a
integração, mede o sorteio. Cadastre um id concreto para testar e deixe o combo
para o tráfego real.
