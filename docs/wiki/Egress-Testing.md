# Testing where the traffic actually leaves from

*(Versão em português ao final.)*

Binding an account to a proxy is easy to configure and hard to verify. The
dashboard shows the binding, the pool test says OK, and none of that answers the
question that matters: **when the proxy goes down, does the gateway stop — or
does it quietly go direct, through the machine's own address, carrying the
account's token?**

The difference between those two answers is the difference between isolation and
the appearance of isolation. This page is a bench that answers it empirically.

---

## The bench

```bash
docker compose -f docker-compose.egress-test.yml up -d
tools/testa_saida_de_rede.sh
docker compose -f docker-compose.egress-test.yml down -v
```

Three containers, none of which touch the internet:

| Container | Address | Role |
| :--- | :--- | :--- |
| `litellmrtk-proxy-a` | `172.33.0.11` | an HTTP proxy |
| `litellmrtk-proxy-b` | `172.33.0.12` | a second one, so "went through *a* proxy" and "went through *this* proxy" can be told apart |
| `litellmrtk-echo` | `172.33.0.20` | the referee: answers with the source address it saw |

The referee is what makes this verifiable. It returns JSON:

```json
{"seen_from": "172.33.0.11", "via": "1.1 squid/6.13", "forwarded_for": "172.33.0.2"}
```

`seen_from` is the whole point — no guessing, no third-party IP service, no
dependency on network luck.

## What the script checks

1. **Baseline** — with no proxy, the referee sees the machine's address.
2. **Through a proxy** — the referee sees the *proxy's* address instead.
3. **Two proxies, two addresses** — what makes one-account-per-address possible.
4. **Proxy down** — the request must **fail**. If it still succeeds, it went
   direct, and that is the silent leak.
5. **Proxy back** — the egress returns with it.

Step 4 is the only one that separates isolation from its appearance.

## Pointing it at a real gateway

The script as shipped drives `curl`, which verifies the bench itself. To measure
the **gateway's** decision, configure the proxy in the gateway and let it make
the request:

**LiteLLM has no proxy-pool API.** Nothing in the administrative surface this
project reads — see [Architecture](Architecture) — registers an egress address,
and there is no per-account binding to configure. What LiteLLM honours is the
process-wide proxy environment of the container it runs in, which means one
instance has one egress address:

```bash
# 1. put the gateway on the bench network
docker network connect litellmrtksync-egress-net litellmrtk-router

# 2. declare the proxy in the compose, under the litellmrtk-router service.
#    It has to go in the file and not into a running container: a container's
#    environment cannot be changed after it starts.
environment:
  - HTTPS_PROXY=http://172.33.0.11:3128
  - HTTP_PROXY=http://172.33.0.11:3128

# 3. recreate it, then watch the proxy log while traffic flows
docker compose -f docker-compose.example.yml up -d --force-recreate litellmrtk-router
docker logs -f litellmrtk-proxy-a
```

A line like `172.33.0.2 TCP_TUNNEL/200 CONNECT api.provider.com:443` is the
gateway's container address going through the proxy — the setting took effect.

Then stop the proxy and send traffic again. **If the request still succeeds, the
gateway fell back to direct.**

One address per instance is the ceiling of what this setting gives you, and
nothing in the administrative surface this project reads binds an address per
account. So two accounts leaving by two different addresses is not something you
configure inside one instance here — and whichever arrangement you end up with,
this page is how you check that it really leaves where you think it does.

## What was measured — against 9Router, not against LiteLLM

The measurement below was taken on the **9Router** bench, and it is labelled that
way on purpose: the proxy pool and the `strictProxy` flag it exercises do not
exist in LiteLLM. It is kept here because the bench is the same one and the
failure mode it found is the reason this page exists at all.

Against a running 9Router (read on 2026-09-12):

- the pool binding works: `docker logs` on the bench proxy showed
  `172.33.0.2 TCP_TUNNEL/200 CONNECT google.com:443`, where `172.33.0.2` is the
  gateway's container;
- the **pool test path** detects a dead proxy correctly:
  `{"ok":false,"error":"Proxy test timed out"}`.

That second result is worth pausing on, because it is reassuring in a misleading
way. The pool test says the right thing, so the operator concludes they are
covered — while the **chat path** drops the `strictProxy` flag before it reaches
the fetch layer and falls back to direct on failure. Reported upstream as
[decolua/9router#4007](https://github.com/decolua/9router/issues/4007).

**Nothing equivalent has been measured against LiteLLM yet.** When it is, the
result goes here, with its date. Until then, run the five steps above against
your own instance — the answer is worth more measured than assumed.

So: **test the path that carries your traffic, not the one the dashboard offers
you.** They are not the same code.

---

# Em português

Vincular uma conta a um proxy é fácil de configurar e difícil de verificar. O
painel mostra o vínculo, o teste do pool diz OK, e nada disso responde à pergunta
que importa: **quando o proxy cai, o gateway para — ou sai em silêncio pelo
endereço da própria máquina, carregando o token da conta?**

A diferença entre essas duas respostas é a diferença entre isolamento e aparência
de isolamento. Esta página é uma bancada que responde isso empiricamente.

## A bancada

```bash
docker compose -f docker-compose.egress-test.yml up -d
tools/testa_saida_de_rede.sh
docker compose -f docker-compose.egress-test.yml down -v
```

Três containers, nenhum deles tocando a internet: dois proxies (para distinguir
"saiu por *um* proxy" de "saiu por *este* proxy") e um **árbitro**, que devolve
em JSON o endereço de origem que enxergou. É o `seen_from` que torna o resultado
verificável, sem palpite e sem depender de serviço de terceiro.

## O que o script verifica

1. **Linha de base** — sem proxy, o árbitro vê o endereço da máquina.
2. **Com proxy** — o árbitro vê o endereço *do proxy*.
3. **Dois proxies, dois endereços** — o que sustenta uma conta por endereço.
4. **Proxy fora do ar** — a requisição tem de **falhar**. Se ainda for atendida,
   ela saiu direto, e esse é o vazamento silencioso.
5. **Proxy de volta** — a saída volta com ele.

O passo 4 é o único que separa isolamento de aparência de isolamento.

## Apontando para um gateway real

O script, como vem, dirige o `curl` — isso verifica a bancada. Para medir a
decisão **do gateway**, configure o proxy nele e deixe-o fazer a requisição.

**O LiteLLM não tem API de proxy pool.** Nada na superfície administrativa que
este projeto lê — veja [Arquitetura](Architecture) — cadastra endereço de saída,
e não há vínculo por conta para configurar. O que o LiteLLM respeita é o proxy
declarado no ambiente do container, que vale para o processo inteiro: uma
instância, um endereço de saída. Conecte `litellmrtk-router` à rede
`litellmrtksync-egress-net`, declare `HTTPS_PROXY` e `HTTP_PROXY` no serviço
dentro do compose (não dá para mudar o ambiente de um container já em pé),
recrie-o e acompanhe `docker logs -f litellmrtk-proxy-a`.

Uma linha como `172.33.0.2 TCP_TUNNEL/200 CONNECT api.provider.com:443` é o
endereço do container do gateway passando pelo proxy — o ajuste pegou.

Depois derrube o proxy e gere tráfego de novo. **Se a requisição ainda for
atendida, o gateway caiu para saída direta.**

Um endereço por instância é o teto do que esse ajuste oferece, e nada na
superfície administrativa que este projeto lê vincula endereço por conta. Ou
seja, duas contas saindo por endereços diferentes não é coisa que se configure
dentro de uma instância aqui.

## O que foi medido — contra o 9Router, não contra o LiteLLM

A medição abaixo foi feita na bancada do **9Router**, e está rotulada assim de
propósito: o proxy pool e a flag `strictProxy` que ela exercita não existem no
LiteLLM. Ela fica aqui porque a bancada é a mesma e a falha que ela encontrou é
o motivo desta página existir.

Contra um 9Router em execução (lido em 12/09/2026):

- o vínculo do pool funciona: o log do proxy registrou
  `172.33.0.2 TCP_TUNNEL/200 CONNECT google.com:443`;
- o **caminho de teste do pool** detecta um proxy morto corretamente:
  `{"ok":false,"error":"Proxy test timed out"}`.

O segundo resultado merece atenção justamente por ser tranquilizador do jeito
errado. O teste do pool responde certo, então o operador conclui que está
protegido — enquanto o **caminho de chat** descarta a flag `strictProxy` antes de
chegar à camada de fetch e cai para saída direta quando o proxy falha. Reportado
em [decolua/9router#4007](https://github.com/decolua/9router/issues/4007).

**Nada equivalente foi medido contra o LiteLLM ainda.** Quando for, o resultado
entra aqui, com a data. Até lá, rode os cinco passos acima contra a sua própria
instância — a resposta vale mais medida do que suposta.

Ou seja: **teste o caminho que carrega o seu tráfego, não o que o painel
oferece.** Não são o mesmo código.
