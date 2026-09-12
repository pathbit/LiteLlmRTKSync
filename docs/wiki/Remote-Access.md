# Remote access: tunnel, Tailscale and what must come first

*(Versão em português ao final.)*

Reaching the gateway from another machine — your laptop away from home, a
teammate, a phone — has three usual answers. They differ in who can reach you,
and the order in which you turn things on decides whether that is safe.

> **This page is about getting IN.** Sending traffic OUT through a chosen
> address, so each account has its own source IP, is a different problem with a
> different answer — see [Egress and Multi-Session](Egress-And-Multi-Session).
> Tailscale appears in both pages doing two unrelated jobs.

---

## Read this before enabling anything

LiteLLM has no dashboard-login flag: **every** admin route already demands the
master key, and the inference routes demand a virtual key. What it does have is
a single master key that opens everything.

That is **safe while the port is bound to `127.0.0.1`**, which is how every
compose here publishes it: only your own machine can reach it, and demanding a
password from yourself on localhost adds friction without adding safety.

The moment you expose the gateway, that reasoning inverts. 9Router's dashboard says it in a yellow banner,
and the warning applies here just the same:

> *Enable "Require login" and set a custom password before activating the tunnel.*

Two things make this sharper than it looks:

1. **`/v1` is a public prefix.** In `src/dashboardGuard.js`, the gateway lists its
   own inference endpoints — `/v1`, `/v1beta`, `/api/v1`, `/codex`, `/responses` —
   as public, because they are meant to be called by your coding tools without a
   dashboard session. With `REQUIRE_API_KEY=false` and the gateway on a public URL,
   **anyone who learns the URL can spend your accounts**.
2. **The gateway's admin routes fall open too.** Its `/api/settings`, `/api/keys`,
   `/api/providers` and the rest of the gateway's admin surface are protected
   *only when `requireLogin` is on*.
   With it off, an exposed gateway hands over its own configuration.

So the order is not a preference:

```
1. a LITELLM_MASTER_KEY that is long and random — not a word you chose
2. a virtual key per tool, so the master key never leaves the host
3. only then, the tunnel or Tailscale
```

---

## Option 1 — Cloudflare tunnel (no built-in button here)

OmniRoute has no tunnel button of its own — that is a 9Router feature. To get
the same result, run `cloudflared` yourself against the published port:

```bash
cloudflared tunnel --url http://127.0.0.1:8083
```

It prints a public `https://…trycloudflare.com` address that reaches your
gateway without opening any port on your router.

**When it fits:** you need a URL reachable from anywhere, including devices you
do not control, and you accept that the address is public to whoever has it.

**What to know:**

- The URL is **public**. There is no allow-list — the only thing between the
  internet and your accounts is the master key and the virtual keys.
- The address changes every time the tunnel is re-enabled, unless you bring your
  own named Cloudflare tunnel.
- The dashboard blocks the button while login is off — but that gate lives in the
  screen. The gateway's own `POST /api/tunnel/enable` does not re-check it, so a script or an
  extension can enable the tunnel while login is still off. Set the two flags
  first and the question does not arise.

---

## Option 2 — Tailscale (the one to prefer)

Your machine joins your private tailnet and the gateway becomes reachable at a
`100.x.y.z` address, or at a MagicDNS name like `http://your-host:8083`.

**When it fits:** almost always. Only devices you enrolled in your tailnet can
reach the gateway — the address is not public, and there is nothing for a
stranger to find.

**Setting it up by hand**, which is also how you do it for OmniRoute and LiteLLM:

```bash
# 1. On the host that runs the gateway
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# 2. Find the address it received
tailscale ip -4        # e.g. 100.101.102.103

# 3. Publish the gateway on the tailnet interface instead of loopback
#    (in the compose, replace 127.0.0.1 with the tailnet address)
ports:
  - "100.101.102.103:8083:4000"

# 4. From another device already in the tailnet
curl http://100.101.102.103:8083/v1/models
```

Binding to the tailnet address rather than `0.0.0.0` matters: `0.0.0.0` also
exposes the gateway to the local network — the café Wi-Fi, the office VLAN —
which is exactly what you were avoiding.

**MagicDNS** makes this readable: with it on, `http://your-host:8083` works from
any device in the tailnet, and the address survives a change of IP.

---

## Option 3 — your own reverse proxy

A VPS with Caddy or nginx in front, TLS terminated there, Basic Auth or mTLS on
top. More work, and the only option that lets you put your own authentication
layer in front of the gateway instead of relying on its flags.

Worth it when several people share one gateway and you want access logs and
revocation per person — neither of which the gateway's own login gives you.

---

## Which one

| | Tunnel | Tailscale | Reverse proxy |
| :--- | :--- | :--- | :--- |
| Who can reach it | anyone with the URL | only your tailnet | whoever you let through |
| Setup | one command | 3 commands | real work |
| Stable address | no (unless named) | yes (MagicDNS) | yes |
| Built-in button in the dashboard | 9Router only | 9Router only | — |
| Sensible default | for a one-off demo | **for everyday use** | shared or audited setups |

---

## After exposing it, check what you exposed

```bash
# From another device, WITHOUT credentials — both should refuse
curl -si https://<your-address>/v1/models | head -1     # expect 401
curl -si https://<your-address>/            | head -1     # expect 401 or a login page

# The synchronizer panel should not be exposed at all
curl -si https://<your-address>:9093/       | head -1     # expect connection refused
```

The panel of this synchronizer has no reason to leave the machine: it reads the
gateway's database and shows credentials' health. Keep its port on `127.0.0.1`
and reach it through the same tunnel or tailnet you use for everything else.

---

# Em português

Alcançar o gateway de outra máquina tem três respostas usuais. Elas diferem em
**quem consegue chegar até você**, e a ordem em que você liga as coisas decide se
isso é seguro.

> **Esta página é sobre entrar.** Fazer o tráfego **sair** por um endereço
> escolhido, para que cada conta tenha o seu IP, é outro problema — veja
> [Egress and Multi-Session](Egress-And-Multi-Session). O Tailscale aparece nas
> duas páginas fazendo trabalhos diferentes.

## Leia antes de ligar qualquer coisa

O LiteLLM não tem flag de login de painel: toda rota administrativa já exige a
master key, e as rotas de inferência exigem uma chave virtual. O que ele tem é
uma master key única que abre tudo. Expor a porta é **seguro enquanto ela está
presa em `127.0.0.1`** — só a sua máquina alcança, e exigir senha de si mesmo no
localhost acrescenta atrito sem acrescentar segurança.

No instante em que o gateway é exposto, o raciocínio se inverte. O painel do 9Router avisa, e o aviso
vale igual aqui:

> *Enable "Require login" and set a custom password before activating the tunnel.*

Dois detalhes tornam isso mais sério do que parece:

1. **`/v1` é prefixo público.** Em `src/dashboardGuard.js`, os endpoints de
   inferência do gateway (`/v1`, `/v1beta`, `/api/v1`, `/codex`, `/responses`)
   são públicos por projeto — é assim que as ferramentas de código chamam o gateway
   sem sessão. Com `REQUIRE_API_KEY=false` e o gateway numa URL pública,
   **qualquer um que descubra o endereço gasta as suas contas**.
2. **As rotas administrativas do gateway também caem.** As dele — `/api/settings`,
   `/api/keys`, `/api/providers` e afins do gateway — só são protegidas **quando `requireLogin` está
   ligado**. Com ele desligado, um gateway exposto entrega a própria
   configuração.

A ordem, portanto, não é preferência:

```
1. uma LITELLM_MASTER_KEY longa e aleatória — não uma palavra escolhida
2. uma chave virtual por ferramenta, para a master key nunca sair do host
3. só então, o túnel ou o Tailscale
```

## Opção 1 — túnel Cloudflare (nativo do 9Router)

A tela **API Endpoint** tem o botão `Tunnel`. Ele registra um quick tunnel da
Cloudflare e devolve um endereço público `https://…trycloudflare.com` que
alcança o gateway sem abrir porta nenhuma no seu roteador.

**Quando serve:** você precisa de uma URL alcançável de qualquer lugar,
inclusive de dispositivos que você não controla, e aceita que o endereço seja
público para quem o tiver.

**O que saber:** a URL é pública e não há lista de permissão — entre a internet
e as suas contas existem apenas a master key e as chaves virtuais. O endereço
muda a cada reativação, a menos que você use um túnel nomeado seu. E o bloqueio
do botão enquanto o login está desligado vive **na tela**: o `POST
/api/tunnel/enable` do gateway não reavalia a condição. Ligue as duas variáveis antes e a
questão não se coloca.

## Opção 2 — Tailscale (nativo do 9Router, e o que preferir)

A mesma tela tem o botão `Tailscale`, que instala e conecta o daemon. A sua
máquina entra na sua tailnet e o gateway passa a ser alcançável num endereço
`100.x.y.z`, ou num nome MagicDNS como `http://seu-host:20128`.

**Quando serve:** quase sempre. Só os dispositivos que você cadastrou alcançam o
gateway — o endereço não é público e não há o que um estranho descubra.

**Configurando à mão**, que é também como se faz no OmniRoute e no LiteLLM:

```bash
# 1. No host que roda o gateway
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# 2. Descubra o endereço recebido
tailscale ip -4        # ex.: 100.101.102.103

# 3. Publique o gateway na interface da tailnet, em vez do loopback
ports:
  - "100.101.102.103:8083:4000"

# 4. De outro dispositivo já na tailnet
curl http://100.101.102.103:8083/v1/models
```

Prender no endereço da tailnet em vez de `0.0.0.0` importa: `0.0.0.0` também
expõe o gateway à rede local — o Wi-Fi do café, a VLAN do escritório — que é
justamente o que se queria evitar.

## Opção 3 — proxy reverso próprio

Um VPS com Caddy ou nginx na frente, TLS terminado ali, Basic Auth ou mTLS por
cima. Dá mais trabalho, e é a única opção que permite colocar a **sua** camada
de autenticação na frente do gateway em vez de depender das flags dele.

Compensa quando várias pessoas dividem um gateway e você quer log de acesso e
revogação por pessoa — coisas que o login do gateway não oferece.

## Depois de expor, confira o que você expôs

```bash
# De outro dispositivo, SEM credencial — os dois têm de recusar
curl -si https://<seu-endereco>/v1/models | head -1     # espera-se 401
curl -si https://<seu-endereco>/            | head -1     # espera-se 401 ou tela de login
```

O painel deste sincronizador não tem motivo para sair da máquina: ele lê o banco
do gateway e mostra a saúde das credenciais. Mantenha a porta dele em
`127.0.0.1` e alcance-o pelo mesmo túnel ou tailnet que você já usa.
