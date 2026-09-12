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
| `egress-proxy-a` | `172.31.0.11` | an HTTP proxy |
| `egress-proxy-b` | `172.31.0.12` | a second one, so "went through *a* proxy" and "went through *this* proxy" can be told apart |
| `egress-echo` | `172.31.0.20` | the referee: answers with the source address it saw |

The referee is what makes this verifiable. It returns JSON:

```json
{"seen_from": "172.31.0.11", "via": "1.1 squid/6.13", "forwarded_for": "172.31.0.2"}
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

```bash
# 1. put the gateway on the bench network
docker network connect egress-test_egress <gateway-container>

# 2. register the pool through the gateway's own API
curl -s -X POST http://127.0.0.1:8083 \
  -H 'Content-Type: application/json' \
  -d '{"name":"bench","proxyUrl":"http://172.31.0.11:3128","isActive":true,"strictProxy":true}'

# 3. bind it to a connection, then watch the proxy log while traffic flows
docker logs -f egress-proxy-a
```

A line like `172.31.0.2 TCP_TUNNEL/200 CONNECT api.provider.com:443` is the
gateway's container address going through the proxy — the binding works.

Then stop the proxy and send traffic again. **If the request still succeeds, the
gateway fell back to direct.**

## What was measured here

Against a running LiteLLM (read on 2026-09-12):

- the pool binding works: `docker logs egress-proxy-a` showed
  `172.31.0.2 TCP_TUNNEL/200 CONNECT google.com:443`, where `172.31.0.2` is the
  gateway's container;
- the **pool test path** detects a dead proxy correctly:
  `{"ok":false,"error":"Proxy test timed out"}`.

That second result is worth pausing on, because it is reassuring in a misleading
way. The pool test says the right thing, so the operator concludes they are
covered — while the **chat path** drops the `strictProxy` flag before it reaches
the fetch layer and falls back to direct on failure. Reported upstream as
[decolua/9router#4007](https://github.com/decolua/9router/issues/4007).

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
decisão **do gateway**, configure o proxy nele e deixe-o fazer a requisição:
conecte o container do gateway à rede da bancada, cadastre o pool pela API dele,
vincule a uma conexão e acompanhe `docker logs -f egress-proxy-a`.

Uma linha como `172.31.0.2 TCP_TUNNEL/200 CONNECT api.provider.com:443` é o
endereço do container do gateway passando pelo proxy — o vínculo funciona.

Depois derrube o proxy e gere tráfego de novo. **Se a requisição ainda for
atendida, o gateway caiu para saída direta.**

## O que foi medido aqui

Contra um LiteLLM em execução (lido em 12/09/2026):

- o vínculo do pool funciona: o log do proxy registrou
  `172.31.0.2 TCP_TUNNEL/200 CONNECT google.com:443`;
- o **caminho de teste do pool** detecta um proxy morto corretamente:
  `{"ok":false,"error":"Proxy test timed out"}`.

O segundo resultado merece atenção justamente por ser tranquilizador do jeito
errado. O teste do pool responde certo, então o operador conclui que está
protegido — enquanto o **caminho de chat** descarta a flag `strictProxy` antes de
chegar à camada de fetch e cai para saída direta quando o proxy falha. Reportado
em [decolua/9router#4007](https://github.com/decolua/9router/issues/4007).

Ou seja: **teste o caminho que carrega o seu tráfego, não o que o painel
oferece.** Não são o mesmo código.
