#!/usr/bin/env python3
"""Cadastra os gateways encadeados — 9Router e OmniRoute — como provedores do LiteLLM.

O 9Router é o `litellmrtk-9router`, que sobe nesta mesma stack; o OmniRoute
continua sendo o gateway de uma stack vizinha.

O QUE ISTO RESOLVE

Uma requisição que chega no LiteLLM sai por um dos dois gateways, e é o gateway
que escolhe a conta e o provedor final. Para isso o proxy precisa de duas coisas
que não vêm de graça:

  1. ALCANCE. O 9Router é de casa: `litellmrtk-9router` sobe no mesmo compose
     desta stack e é alcançado pela rede de gestão própria, sem depender de
     mais nada. Foi a mudança que tornou a stack autossuficiente — antes este
     script apontava para `9rtk-router`, que pertence à stack do 9RTKSync, e o
     encadeamento só funcionava com o vizinho no ar.

     O OmniRoute continua sendo de OUTRA stack, e para ele o encontro acontece
     numa segunda rede, `rtk-inference-net`, declarada como externa nos composes
     e que só os gateways e este proxy compartilham. Sem ela, `ominirtk-router`
     sequer resolve de dentro do container do LiteLLM.

         docker network create rtk-inference-net

  2. CREDENCIAL. Os dois gateways exigem uma chave própria em `/v1/*`, emitida
     pelo painel de cada um (`POST /api/keys`). A do 9Router e a do OmniRoute
     são validadas contra o SQLite local de cada gateway: não são
     intercambiáveis, e é por isso que são duas variáveis distintas.

DE ONDE VÊM AS CHAVES

Do ambiente, nunca de argv — argumento de linha de comando aparece em `ps` e no
histórico do shell. `NINEROUTER_API_KEY` e `OMNIROUTE_API_KEY` moram no `.env`
do repositório, que é gitignored. Este script as lê, as envia ao proxy uma vez
(que as guarda cifradas no Postgres, com `LITELLM_SALT_KEY`) e nunca imprime o
valor — só prefixo e tamanho.

POR QUE CREDENCIAL NOMEADA, E NÃO `api_key` SOLTA NO MODELO

`/model/info` apaga o campo `api_key` da resposta. Quem for depurar depois
"qual credencial este modelo usa?" fica sem resposta. Já o
`litellm_credential_name` a API preserva — a procedência continua legível sem
que o segredo vaze. E a referência `os.environ/NOME` NÃO serve aqui: dentro de
uma credencial nomeada o LiteLLM grava a string literal, e o gateway responde
"Invalid API Key" (medido, e documentado em `popula_bancada.py`).

IDEMPOTENTE

Roda quantas vezes quiser: consulta `/credentials` e `/model/info` por nome
antes de criar, e informa o que já existia.

USO

Este repositório não tem `.venv` (ao contrário dos irmãos): o script só usa a
biblioteca padrão, então o `python3` do sistema basta.

    python3 tools/registra_gateways.py             # cadastra (idempotente)
    python3 tools/registra_gateways.py --dry-run   # só mostra o que faria
    python3 tools/registra_gateways.py --conferir  # o que já está cadastrado
    python3 tools/registra_gateways.py --testar    # chamada real em cada gateway
"""

import argparse
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

# As peças de HTTP administrativo já existem na bancada; reaproveitá-las evita
# um segundo cliente com outro tratamento de 401 e outra mensagem de erro.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from popula_bancada import (  # noqa: E402
    AdminAPI,
    BenchError,
    carregar_env_do_arquivo,
    log,
)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_URL = "http://127.0.0.1:8083"

# Prefixo próprio, para distinguir do que a bancada (`bancada-`) cria e para
# que a limpeza de um nunca leve o outro junto.
PREFIXO = "gateway-"

# Os dois gateways irmãos.
#
# `api_base` COM `/v1`: o LiteLLM concatena "chat/completions" ao que receber.
# Sem o `/v1` a requisição iria para
# `http://litellmrtk-9router:20128/chat/completions` e o gateway devolveria 404
# — que o proxy relata como erro do provedor, não como erro de configuração, e a
# pista se perde.
#
# `api_base` pelo NOME DO SERVIÇO, nunca `127.0.0.1:8383`: dentro do container
# do LiteLLM o loopback é o próprio LiteLLM.
#
# O prefixo duplo em `model` não é engano. O LiteLLM consome só o primeiro
# segmento (`openai/`, que escolhe o dialeto) e manda o resto intacto no corpo.
# Então `openai/ag/gemini-3.8-flash` chega ao 9Router como `ag/gemini-3.8-flash`,
# que é como ele nomeia um modelo: `provedor/modelo`.
#
# Os dois modelos abaixo são os que responderam 200 com texto quando medidos
# direto no gateway, e ambos usam provedor por CHAVE DE API de propósito.
#
# `ag/...` (Antigravity) foi a primeira escolha e é a errada aqui: o Antigravity
# entra no 9Router por OAuth, e uma conta OAuth vive no banco do gateway, que
# mora num volume. Quem segue o caminho documentado de recomeçar do zero
# (`docker compose down -v`) apaga a conta junto, e a chamada passa a falhar com
# `No active credentials for provider: antigravity` -- que parece defeito da
# integração e é só uma conta que precisa ser reconectada à mão, pela tela.
# Provedor por chave de API se recadastra por script, e por isso serve de teste.
#
# E "medido direto no gateway" é literal: estar no `/v1/models` NÃO basta. O
# catálogo do OmniRoute anuncia 1749 ids, e `groq/llama-3.3-70b-versatile` é um
# deles — mas o caminho de inferência recusa esse mesmo id com 400 "not
# available in the active live catalog for provider 'groq'". Anúncio e serviço
# são listas diferentes; só a chamada real decide. Veja
# docs/wiki/Chaining-Gateways.md.
#
# Por que NÃO um combo aqui: `auto/best-fast` deixa o próprio OmniRoute escolher
# conta e provedor, o que é ótimo em produção e péssimo para um teste. Medido em
# quatro chamadas seguidas, o combo caiu sempre num modelo de EXTRAÇÃO, que
# devolveu `{"answer":"..."}`, `{"response":"..."}`, uma data sem relação e um
# texto divagante antes de acertar o eco pedido. Um teste que falha assim não
# está medindo a integração -- está medindo qual modelo o combo sorteou, e
# produz tanto falso negativo (transporte certo, texto diferente) quanto falso
# positivo (texto por acaso parecido). O modelo concreto isola o que se quer
# provar: que a requisição atravessou a rede, foi autenticada e voltou.
# O combo continua disponível para uso real -- veja docs/wiki/Chaining-Gateways.md.
GATEWAYS: List[Dict[str, Any]] = [
    {
        "nome": "9router",
        "model_name": f"{PREFIXO}9router-gemini",
        "model": "openai/gemini/gemini-3.8-flash",
        # O 9Router DESTA stack, alcançado pela rede de gestão própria. Já
        # apontou para `http://9rtk-router:20128/v1`, que pertence à stack do
        # 9RTKSync: funcionava e era frágil -- derrubar a stack do vizinho
        # quebrava a inferência daqui, e subir só esta stack dava um proxy sem
        # para onde encadear. Ver docs/wiki/Chaining-Gateways.md.
        "api_base": "http://litellmrtk-9router:20128/v1",
        # Endereco publicado no host: a conferencia da chave roda daqui, de
        # fora das redes Docker, onde o nome interno nao resolve.
        "api_base_host": "http://127.0.0.1:8383/v1",
        "env_var": "NINEROUTER_API_KEY",
        "credential_name": f"{PREFIXO}cred-9router",
        # Só para dizer ao operador onde conferir o rastro da chamada.
        "container": "litellmrtk-9router",
    },
    {
        "nome": "omniroute",
        "model_name": f"{PREFIXO}omniroute-granite",
        "model": "openai/openrouter/ibm-granite/granite-4.2-8b",
        "api_base": "http://ominirtk-router:20128/v1",
        "api_base_host": "http://127.0.0.1:8082/v1",
        "env_var": "OMNIROUTE_API_KEY",
        "credential_name": f"{PREFIXO}cred-omniroute",
        "container": "ominirtk-router",
    },
]


def mascara(valor: str) -> str:
    """Prefixo e tamanho. O valor de uma credencial não vai para a saída."""
    if not valor:
        return "(vazia)"
    return f"{valor[:6]}*** (len={len(valor)})"


def credenciais_existentes(api: AdminAPI) -> set:
    return {
        str(c.get("credential_name"))
        for c in api.list_credentials()
        if c.get("credential_name")
    }


def modelos_existentes(api: AdminAPI) -> Dict[str, Dict[str, Any]]:
    """Nome do modelo -> o que o proxy tem cadastrado nele.

    Devolve um mapa, e não um conjunto de nomes, porque "já existe" não é a
    pergunta toda: o modelo pode existir apontando para o gateway ERRADO. Foi o
    que aconteceu ao mudar o encadeamento de `9rtk-router` (stack vizinha) para
    `litellmrtk-9router` (esta stack) -- o nome não mudou, então a verificação
    por nome dizia "já existe" e o `api_base` antigo sobrevivia no Postgres.
    """
    mapa: Dict[str, Dict[str, Any]] = {}
    for modelo in api.list_models():
        nome = modelo.get("model_name")
        if not nome:
            continue
        params = modelo.get("litellm_params") or {}
        mapa[str(nome)] = {
            "api_base": params.get("api_base"),
            "model": params.get("model"),
            "credential_name": params.get("litellm_credential_name"),
        }
    return mapa


def registrar(api: AdminAPI, escrever: bool = True) -> int:
    """Garante credencial e modelo de cada gateway. Devolve quantos criou."""
    creds = credenciais_existentes(api)
    modelos = modelos_existentes(api)
    criados = 0

    for gw in GATEWAYS:
        log(f"\n[{gw['nome']}] → {gw['api_base']}")
        chave = os.environ.get(gw["env_var"], "")
        if not chave:
            log(f"  ! {gw['env_var']} não está no ambiente — este gateway fica de fora.")
            log(f"    A chave sai do painel do gateway (POST /api/keys) e mora no .env.")
            continue
        log(f"  chave {gw['env_var']} = {mascara(chave)}")

        # A chave do .env pode estar morta. Ela vive no banco do gateway, e esse
        # banco mora num volume: apagar o volume -- que é o caminho documentado
        # para recomeçar do zero -- leva a chave junto, enquanto o .env continua
        # guardando o valor antigo. Sem esta conferência o script cadastraria
        # tudo com uma credencial que já não vale, e o erro só apareceria mais
        # tarde, na inferência, dizendo a coisa errada.
        recusa = conferir_credencial_do_gateway(gw)
        if recusa:
            log(f"  ! {recusa}")
            log(f"    Crie uma chave nova no painel do {gw['nome']} e grave em {gw['env_var']}.")
            continue

        nome_cred = gw["credential_name"]
        if nome_cred in creds:
            # "Já existe" não basta: ela pode guardar a chave ANTERIOR. O proxy
            # cifra o valor e nunca o devolve, então não há como comparar --
            # dá para recriar, e só. Foi exatamente o que aconteceu ao recriar
            # as stacks: a credencial sobreviveu no Postgres do LiteLLM com a
            # chave que morreu junto com o volume do gateway, e a inferência
            # respondia 401 dizendo que o problema era a master key do proxy.
            if not escrever:
                log(f"  ~ credencial '{nome_cred}' seria recriada com a chave atual")
            else:
                try:
                    api.request("DELETE", f"/credentials/{nome_cred}", None)
                except BenchError:
                    # Versões diferentes expõem rotas diferentes para apagar;
                    # se não deu, o POST abaixo ainda pode sobrescrever.
                    pass
                api.request("POST", "/credentials", {
                    "credential_name": nome_cred,
                    "credential_info": {"custom_llm_provider": "openai"},
                    "credential_values": {"api_key": chave},
                })
                log(f"  ~ credencial '{nome_cred}' atualizada com a chave atual")
        elif not escrever:
            log(f"  + credencial '{nome_cred}' seria criada a partir de {gw['env_var']}")
        else:
            api.request("POST", "/credentials", {
                "credential_name": nome_cred,
                "credential_info": {"custom_llm_provider": "openai"},
                # Valor literal: dentro de uma credencial nomeada a referência
                # `os.environ/NOME` seria gravada como string e recusada pelo
                # gateway. O proxy cifra isto no Postgres.
                "credential_values": {"api_key": chave},
            })
            log(f"  + credencial '{nome_cred}' criada a partir de {gw['env_var']}")

        nome_modelo = gw["model_name"]
        cadastrado = modelos.get(nome_modelo)
        if cadastrado is not None:
            # Confere o DESTINO, não só o nome. Um `api_base` obsoleto é
            # silencioso: o modelo aparece na lista, o `--testar` até responde
            # 200, e a chamada sai pelo gateway antigo -- a linha de prova
            # aparece no log do vizinho, e não no do gateway desta stack.
            atual = cadastrado.get("api_base")
            if atual == gw["api_base"]:
                log(f"  = modelo '{nome_modelo}' já existe → {atual}")
                continue
            if not escrever:
                log(f"  ~ modelo '{nome_modelo}' seria reapontado: {atual} → {gw['api_base']}")
                continue
            # Recriar em vez de atualizar: `/model/update` não existe em toda
            # versão do proxy, e apagar-e-criar dá o mesmo resultado com uma
            # rota só. O id é derivado do nome, então ele volta igual.
            try:
                api.request("POST", "/model/delete", {"id": gw["model_name"]})
            except BenchError:
                # Versões diferentes expõem rotas diferentes para apagar; se
                # não deu, o POST abaixo ainda tenta sobrescrever.
                pass
        elif not escrever:
            log(f"  + modelo '{nome_modelo}' seria criado → {gw['model']}")
            continue
        api.request("POST", "/model/new", {
            "model_name": nome_modelo,
            "litellm_params": {
                "model": gw["model"],
                "api_base": gw["api_base"],
                "litellm_credential_name": nome_cred,
            },
            # O id deriva do NOME do modelo, não do gateway. Enquanto era
            # fixo por gateway, trocar o nome do modelo colidia com o id que já
            # estava no banco e o proxy devolvia
            # `HTTP 500 Failed to add model to db` -- sem nomear o id nem o
            # modelo, o que manda quem lê procurar defeito em qualquer outro
            # lugar. Id estável para o mesmo nome (a idempotência continua),
            # diferente para nome diferente.
            "model_info": {"id": gw["model_name"]},
        })
        # O sucesso é anunciado DEPOIS do POST, nunca antes. No reapontamento o
        # modelo antigo já foi apagado quando esta linha roda: anunciar antes
        # faria a falha do POST imprimir "reapontado" seguido de "falhou", com
        # o modelo sumido do proxy e a saída dizendo o contrário.
        if cadastrado is None:
            criados += 1
            log(f"  + modelo '{nome_modelo}' criado → {gw['model']}")
        else:
            log(f"  ~ modelo '{nome_modelo}' reapontado: "
                f"{cadastrado.get('api_base')} → {gw['api_base']}")

    return criados


def conferir(api: AdminAPI) -> None:
    """Mostra como cada modelo de gateway ficou cadastrado."""
    log("\nmodelos de gateway registrados neste proxy:")
    achou = False
    for m in api.list_models():
        nome = str(m.get("model_name") or "")
        if not nome.startswith(PREFIXO):
            continue
        achou = True
        params = m.get("litellm_params") or {}
        log(f"  {nome}")
        log(f"    model      = {params.get('model')}")
        log(f"    api_base   = {params.get('api_base')}")
        log(f"    credencial = {params.get('litellm_credential_name') or '(nenhuma)'}")
        # `api_key` sai da resposta do /model/info de propósito; se aparecesse
        # aqui, seria segredo vazando num comando de conferência.
        log(f"    api_key na resposta = {'sim' if params.get('api_key') else 'não (esperado)'}")
    if not achou:
        log("  (nenhum — rode sem --conferir para cadastrar)")


def conferir_credencial_do_gateway(gw: Dict[str, Any]) -> str:
    """Pergunta ao PRÓPRIO gateway se a credencial ainda vale.

    Devolve "" quando a chave é aceita, ou a razão da recusa. Vai direto ao
    gateway, sem passar pelo LiteLLM: um 401 vindo do proxy é ambíguo -- pode
    ser a master key do LiteLLM ou a chave do gateway, e a mensagem que o proxy
    devolve culpa a primeira mesmo quando o problema é a segunda.
    """
    chave = os.environ.get(gw["env_var"], "")
    if not chave:
        return f"{gw['env_var']} vazia no ambiente"

    # O endereço interno só resolve de dentro da rede de inferência; daqui, do
    # host, usa-se a porta publicada.
    base = gw.get("api_base_host") or gw["api_base"]
    url = base.rstrip("/") + "/models"
    pedido = urllib.request.Request(url, headers={"Authorization": f"Bearer {chave}"})
    try:
        with urllib.request.urlopen(pedido, timeout=20) as resposta:
            if resposta.status == 200:
                return ""
            return f"{gw['env_var']} devolveu HTTP {resposta.status} em {url}"
    except urllib.error.HTTPError as erro:
        if erro.code == 401:
            return (f"{gw['env_var']} foi RECUSADA pelo {gw['nome']} (HTTP 401). "
                    f"A chave provavelmente morreu com o volume do gateway: "
                    f"rode este script sem --testar para criar uma nova.")
        return f"{gw['env_var']} devolveu HTTP {erro.code} em {url}"
    except Exception as erro:  # rede, DNS, gateway fora do ar
        return f"não consegui falar com {gw['nome']} em {url}: {type(erro).__name__}"


def testar(api: AdminAPI) -> int:
    """Chama de verdade cada modelo de gateway e diz como conferir no gateway.

    Uma resposta bonita não prova nada sozinha: ela poderia ter vindo de outro
    lugar. Por isso cada chamada leva uma palavra-marca e o comando que mostra a
    linha correspondente NO LOG DO GATEWAY é impresso junto — é o casamento das
    duas coisas que prova que a requisição realmente passou por lá.
    """
    falhas = 0
    for gw in GATEWAYS:
        marca = f"PROVA-{gw['nome'].upper()}"
        log(f"\n[{gw['nome']}] chamando '{gw['model_name']}' …")

        # PRIMEIRO a chave, DEPOIS a inferência. A ordem não é estética.
        #
        # A inferência do OmniRoute responde 200 sem credencial nenhuma
        # (fall-through em clientApi.ts:89-95), então um chat bem-sucedido ali
        # NÃO prova que a chave vale -- prova só que o caminho existe. Foi
        # exatamente o que aconteceu depois de recriar as stacks: as chaves
        # tinham morrido com os volumes, o 9Router recusou e o OmniRoute
        # respondeu "PROVA-OMNIROUTE" alegremente com uma credencial inválida.
        #
        # `/v1/models` é a rota que os DOIS protegem, e por isso é ela que
        # separa "a chave vale" de "o caminho existe".
        estado_da_chave = conferir_credencial_do_gateway(gw)
        if estado_da_chave:
            log(f"  ✗ CREDENCIAL: {estado_da_chave}")
            falhas += 1
            continue
        log(f"  ✓ credencial aceita pelo gateway (/v1/models)")
        try:
            r = api.request("POST", "/v1/chat/completions", {
                "model": gw["model_name"],
                "messages": [{"role": "user",
                              "content": f"Responda exatamente com a palavra: {marca}"}],
                # 200, e não 40: `max_tokens` é orçamento TOTAL de saída, e num
                # modelo de raciocínio o pensamento come esse orçamento antes de
                # sobrar texto. Com 40 o granite gastou 41 tokens de raciocínio e
                # devolveu `content` VAZIO com `finish_reason: length` -- que se
                # lê como falha de integração sem ser uma. Com 200 (86 de
                # raciocínio) a palavra sai inteira.
                "max_tokens": 200,
            })
        except BenchError as e:
            log(f"  ✗ FALHOU: {e}")
            falhas += 1
            continue
        escolhas = r.get("choices") or [{}]
        conteudo = ((escolhas[0].get("message") or {}).get("content") or "").strip()
        uso = r.get("usage") or {}
        log(f"  ✓ HTTP 200 · conteúdo: {conteudo!r}")
        log(f"    tokens: entrada={uso.get('prompt_tokens')} saída={uso.get('completion_tokens')}")
        # Comparação exata, não `in`: um modelo que ecoa o prompt inteiro traz a
        # marca dentro da resposta e passaria por acerto sem ter respondido nada.
        if conteudo != marca:
            # Não é erro de conexão: o caminho fechou e o gateway respondeu. É o
            # modelo que o gateway escolheu que não seguiu a instrução — num
            # combo, quem escolhe é ele, e a escolha muda entre chamadas.
            log(f"    ! o texto não traz '{marca}' — o caminho funcionou, mas o modelo "
                f"que o gateway escolheu respondeu outra coisa.")
        log(f"    confira no gateway:  docker logs {gw['container']} --since 2m | tail -20")
    return falhas


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Cadastra 9Router e OmniRoute como provedores do LiteLLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--url", default=os.environ.get("BENCH_LITELLM_URL", DEFAULT_URL),
                   help=f"endereço do proxy LiteLLM (padrão: {DEFAULT_URL})")
    p.add_argument("--dry-run", action="store_true",
                   help="mostra o que faria, sem escrever nada no proxy")
    p.add_argument("--conferir", action="store_true",
                   help="só lista os modelos de gateway já cadastrados")
    p.add_argument("--testar", action="store_true",
                   help="faz uma chamada real de chat em cada gateway e mostra "
                        "o comando que exibe o rastro no log do gateway")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    # O `.env` do repositório completa o ambiente sem sobrescrever o que já
    # estiver exportado. É de lá que saem a master key e as chaves dos gateways.
    carregar_env_do_arquivo(os.path.join(RAIZ, ".env"))

    master_key = os.environ.get("LITELLM_MASTER_KEY", "")
    if not master_key:
        log("LITELLM_MASTER_KEY não está no ambiente nem no .env do repositório.")
        return 2

    api = AdminAPI(args.url, master_key)
    try:
        if args.conferir:
            conferir(api)
            return 0
        if args.testar:
            return 1 if testar(api) else 0
        criados = registrar(api, escrever=not args.dry_run)
        if args.dry_run:
            log("\n(--dry-run: nada foi escrito no proxy)")
        else:
            log(f"\n{criados} modelo(s) criado(s) nesta execução.")
            conferir(api)
    except BenchError as e:
        log(f"falhou: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
