#!/usr/bin/env python3
"""Popula a bancada de testes do LiteLlmRTKSync no proxy LiteLLM.

Por que existe: o painel só consegue mostrar o que o proxy tem. Uma instalação
recém-criada não tem chave com validade, não tem credencial nomeada e não tem
time acima do teto — e a tela, honestamente, responde "validade não declarada",
"nenhuma declarada", "não verificada". Isso não é defeito do painel: é ausência
de dado do outro lado. Este script cria o outro lado.

O que ele cria, e por quê cada coisa:

  times      — dois, um dentro do teto da plataforma e um acima dele, para que
               o detector de incoerência de limites (`limits.py`) tenha o que
               achar nos dois sentidos: chave×time e time×plataforma;
  chaves     — COM `duration`, porque sem `duration` o LiteLLM grava
               `expires = null` e não existe validade para o painel exibir.
               Uma ativa, uma dentro da margem de renovação, uma já vencida,
               uma incoerente com o teto do time e uma bloqueada;
  credencial — nomeada (`/credentials`), com o valor lido do AMBIENTE. O
               `/model/info` do LiteLLM REMOVE `api_key` da resposta, mas
               preserva `litellm_credential_name`: a credencial nomeada é a
               única procedência que o painel consegue enxergar;
  modelos    — apontando para provedores reais, amarrados à credencial nomeada.

SEGREDO: nenhum. As chaves de provedor são lidas do ambiente (ou do `.env`, que
é gitignored) e enviadas ao proxy como referência `os.environ/NOME` quando o
proxy tem a variável, ou como valor lido do ambiente quando não tem. Nada é
escrito neste arquivo, e nada de valor de credencial vai para a saída padrão.

Uso:

    python3 tools/popula_bancada.py             # cria o que faltar
    python3 tools/popula_bancada.py --limpar    # remove só o que este script cria
    python3 tools/popula_bancada.py --conferir  # só mostra o estado, não escreve

Idempotente: cada objeto é procurado pelo nome antes de ser criado. Rodar duas
vezes não duplica. Todo objeto da bancada leva o prefixo `bancada-`, e `--limpar`
só toca no que tem esse prefixo — o que já existia no proxy não é assunto dele.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

# Prefixo de tudo que este script cria. É o contrato com `--limpar`: nada sem
# este prefixo é apagado, e nada com ele é considerado de outra pessoa.
BENCH_PREFIX = "bancada-"

DEFAULT_URL = "http://127.0.0.1:8083"
DEFAULT_TIMEOUT = 30.0

# Mesma margem padrão do sincronizador (`REFRESH_MARGIN`, config.py). É o que
# define "dentro da janela de renovação" para a chave efêmera da bancada.
DEFAULT_REFRESH_MARGIN = 900


def refresh_margin() -> int:
    """Margem de renovação em segundos, lida NA HORA — nunca no import.

    O `.env` do repositório só entra no ambiente quando `main()` o carrega, e
    isso acontece depois do import. Fixar a margem numa constante de módulo
    faria o script enxergar 900 enquanto o container do sync usa outro valor do
    mesmo `.env` — os dois discordando em silêncio, que é o tipo de desencontro
    que esta bancada existe para não ter.
    """
    try:
        valor = int(os.environ.get("REFRESH_MARGIN") or DEFAULT_REFRESH_MARGIN)
    except ValueError:
        return DEFAULT_REFRESH_MARGIN
    return valor if valor > 0 else DEFAULT_REFRESH_MARGIN


def ephemeral_duration(margem: Optional[int] = None) -> str:
    """Validade da chave efêmera, sempre DENTRO da margem vigente.

    Derivada e não fixa: uma duração escrita à mão ("14m") vira maior que a
    margem assim que alguém baixa `REFRESH_MARGIN`, e aí a chave nasce fora da
    janela, é rearmada em toda execução e o painel nunca mostra "Expiring".
    Um minuto de folga abaixo do teto dá tempo de a chave ser lida ainda dentro
    da janela.
    """
    margem = refresh_margin() if margem is None else margem
    return f"{max(60, margem - 60)}s"

# -- desenho da bancada -------------------------------------------------------
# Declarativo de propósito: o que a tela vai mostrar está escrito aqui, e não
# espalhado no meio das chamadas HTTP.

TEAMS: List[Dict[str, Any]] = [
    {
        # Dentro de qualquer teto de plataforma razoável: é o time "bem
        # configurado", e serve de contraste para o outro.
        "team_alias": f"{BENCH_PREFIX}time-estrito",
        "rpm_limit": 60,
        "tpm_limit": 20000,
        "max_budget": 5.0,
    },
    {
        # Acima do teto da plataforma nos três campos. O LiteLLM aceita sem
        # reclamar — é exatamente a incoerência que o painel precisa denunciar.
        "team_alias": f"{BENCH_PREFIX}time-folgado",
        "rpm_limit": 300,
        "tpm_limit": 400000,
        "max_budget": 250.0,
    },
]

KEYS: List[Dict[str, Any]] = [
    {
        "key_alias": f"{BENCH_PREFIX}chave-ativa",
        "team_alias": f"{BENCH_PREFIX}time-estrito",
        "duration": "720h",
        "rpm_limit": 30,
        "max_budget": 1.0,
        # Estado esperado no painel, para o relatório final conferir.
        "estado": "Active",
    },
    {
        # Nasce um minuto abaixo da margem vigente, então entra em "Expiring".
        # É a única célula da bancada que decai sozinha — passada a margem ela
        # vira "Expired" e a tela perde o estado intermediário. Por isso é
        # `ephemeral`: cada execução confere a validade viva e recria a chave
        # quando ela já saiu da margem, em vez de pular por já existir o alias.
        # A `duration` não é fixa aqui: sai de `ephemeral_duration()`, para não
        # desmentir a margem no dia em que alguém mudar `REFRESH_MARGIN`.
        "key_alias": f"{BENCH_PREFIX}chave-renovando",
        "team_alias": f"{BENCH_PREFIX}time-estrito",
        "duration": None,
        "rpm_limit": 20,
        "ephemeral": True,
        "estado": "Expiring",
    },
    {
        # `duration` precisa ser positiva; 1 s vence sozinha enquanto o script
        # ainda está rodando. O proxy continua devolvendo a chave vencida — é
        # justamente o caso que o painel existe para mostrar.
        "key_alias": f"{BENCH_PREFIX}chave-vencida",
        "team_alias": f"{BENCH_PREFIX}time-estrito",
        "duration": "1s",
        "estado": "Expired",
    },
    {
        # 600 rpm dentro de um time que teto é 60. Vale 60; o cadastro diz 600.
        "key_alias": f"{BENCH_PREFIX}chave-incoerente",
        "team_alias": f"{BENCH_PREFIX}time-estrito",
        "duration": "720h",
        "rpm_limit": 600,
        "tpm_limit": 90000,
        "estado": "Active (com achado de limite)",
    },
    {
        "key_alias": f"{BENCH_PREFIX}chave-bloqueada",
        "team_alias": f"{BENCH_PREFIX}time-folgado",
        "duration": "720h",
        "blocked": True,
        "estado": "Blocked",
    },
]

# Provedores reais. A `env_var` é o nome da variável, NUNCA o valor: quem tiver
# a variável definida entra na bancada, quem não tiver é pulado com aviso.
PROVIDERS: List[Dict[str, Any]] = [
    {
        "nome": "groq",
        "env_var": "GROQ_API_KEY",
        "credential_name": f"{BENCH_PREFIX}cred-groq",
        "model_name": f"{BENCH_PREFIX}groq-gpt-oss-120b",
        "model": "groq/openai/gpt-oss-120b",
        "api_base": "https://api.groq.com/openai/v1",
    },
    {
        "nome": "mistral",
        "env_var": "MISTRAL_API_KEY",
        "credential_name": f"{BENCH_PREFIX}cred-mistral",
        "model_name": f"{BENCH_PREFIX}mistral-small",
        "model": "mistral/mistral-small-latest",
        "api_base": "https://api.mistral.ai/v1",
    },
    {
        "nome": "gemini",
        "env_var": "GEMINI_API_KEY",
        "credential_name": f"{BENCH_PREFIX}cred-gemini",
        "model_name": f"{BENCH_PREFIX}gemini-flash",
        "model": "gemini/gemini-3.6-flash",
        # Gemini não é OpenAI-compatível: o endereço padrão do provedor é o
        # certo, e declarar um errado aqui quebraria a checagem de saúde.
        "api_base": None,
    },
]


class BenchError(RuntimeError):
    """Falha que o operador precisa ler inteira, sem traceback."""


# -- transporte ---------------------------------------------------------------


class AdminAPI:
    """As chamadas administrativas do LiteLLM que a bancada usa.

    O `opener` é injetável pelo mesmo motivo do `LiteLLMClient`: o teste desta
    ferramenta não pode depender de um container de pé.
    """

    def __init__(self, base_url: str, master_key: str, timeout: float = DEFAULT_TIMEOUT,
                 opener: Optional[Any] = None):
        self.base_url = (base_url or "").rstrip("/")
        self.master_key = master_key or ""
        self.timeout = timeout
        self.opener = opener

    def request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        dados = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=dados, method=method)
        req.add_header("User-Agent", "LiteLlmRTKSync-bancada/1.0")
        req.add_header("Authorization", f"Bearer {self.master_key}")
        if dados is not None:
            req.add_header("Content-Type", "application/json")

        send = self.opener or urllib.request.urlopen
        try:
            with send(req, timeout=self.timeout) as resposta:
                corpo = resposta.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            detalhe = e.read().decode("utf-8", errors="replace")[:400] if hasattr(e, "read") else str(e)
            if e.code in (401, 403):
                raise BenchError(
                    f"{method} {path}: master key recusada pelo proxy (HTTP {e.code}). "
                    "Confira LITELLM_MASTER_KEY."
                ) from e
            raise BenchError(f"{method} {path}: HTTP {e.code} — {detalhe}") from e
        except (urllib.error.URLError, OSError) as e:
            raise BenchError(f"{method} {path}: proxy inacessível em {self.base_url} ({e})") from e

        if not corpo.strip():
            return {}
        try:
            return json.loads(corpo)
        except ValueError as e:
            raise BenchError(f"{method} {path}: resposta não é JSON ({e})") from e

    # -- leituras --------------------------------------------------------

    def list_teams(self) -> List[Dict[str, Any]]:
        dados = self.request("GET", "/team/list")
        if isinstance(dados, list):
            return [t for t in dados if isinstance(t, dict)]
        return [t for t in (dados or {}).get("teams", []) if isinstance(t, dict)]

    def list_keys(self) -> List[Dict[str, Any]]:
        chaves: List[Dict[str, Any]] = []
        pagina = 1
        while True:
            dados = self.request(
                "GET", f"/key/list?{urllib.parse.urlencode({'page': pagina, 'size': 100, 'return_full_object': 'true'})}"
            )
            lote = [k for k in (dados or {}).get("keys", []) if isinstance(k, dict)]
            if not lote:
                break
            chaves.extend(lote)
            if pagina >= int((dados or {}).get("total_pages") or 1):
                break
            pagina += 1
        return chaves

    def list_models(self) -> List[Dict[str, Any]]:
        try:
            dados = self.request("GET", "/model/info")
        except BenchError as e:
            # Instalação sem modelo nenhum responde 500 nesta rota; é estado
            # normal, e tratar como falha impediria a primeira execução.
            if "HTTP 500" in str(e) or "HTTP 404" in str(e):
                return []
            raise
        return [m for m in (dados or {}).get("data", []) if isinstance(m, dict)]

    def list_credentials(self) -> List[Dict[str, Any]]:
        try:
            dados = self.request("GET", "/credentials")
        except BenchError as e:
            if "HTTP 404" in str(e):
                return []
            raise
        return [c for c in (dados or {}).get("credentials", []) if isinstance(c, dict)]


# -- utilidades ---------------------------------------------------------------


def log(mensagem: str) -> None:
    print(mensagem, flush=True)


def carregar_env_do_arquivo(caminho: str) -> None:
    """Completa o ambiente com o `.env` do repositório, sem sobrescrever nada.

    O `.env` é gitignored e é onde as chaves reais moram. Ler dele evita exigir
    que o operador exporte seis variáveis à mão antes de rodar a bancada.
    """
    if not os.path.isfile(caminho):
        return
    with open(caminho, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            nome, _, valor = linha.partition("=")
            nome = nome.strip()
            # O ambiente real tem precedência: quem exportou a variável quis
            # aquele valor, e o arquivo não pode contrariá-lo.
            if nome and nome not in os.environ:
                os.environ[nome] = valor.strip().strip('"').strip("'")


def valor_da_credencial(env_var: str, usar_referencia: bool) -> str:
    """O que vai para o proxy como chave do provedor. Sempre vindo do AMBIENTE.

    Duas formas, e a escolha entre elas não é preferência — é uma limitação
    medida do LiteLLM 1.100.1:

      1. valor lido do ambiente deste script (PADRÃO). O proxy o guarda cifrado
         com a `LITELLM_SALT_KEY`. Funciona, e é o que faz o painel mostrar
         "Named credential" em vez de "None declared";
      2. `os.environ/NOME` (`--referencia-ambiente`). Dentro de uma credencial
         NOMEADA o LiteLLM **não resolve** essa referência: o
         `CredentialAccessor.get_credential_values`
         (`litellm/litellm_core_utils/credential_accessor.py:11-19`) devolve o
         valor gravado como está, sem passar por `get_secret`. O provedor recebe
         a string literal `os.environ/GROQ_API_KEY` e responde "Invalid API Key".
         A referência só é resolvida quando está em `litellm_params.api_key`
         direto no modelo — e aí o `/model/info` REMOVE o campo da resposta, e o
         painel volta a não ter procedência para mostrar.

    Em nenhuma das duas o segredo é escrito em arquivo do repositório.
    """
    if usar_referencia:
        return f"os.environ/{env_var}"
    return os.environ.get(env_var, "")


# -- criação ------------------------------------------------------------------


def garantir_times(api: AdminAPI, escrever: bool = True) -> Dict[str, str]:
    """Cria os times que faltam e devolve o mapa alias → team_id.

    O LiteLLM NÃO impõe unicidade de `team_alias`: criar sem conferir antes
    produziria um time novo a cada execução. A conferência é aqui.
    """
    existentes = {str(t.get("team_alias")): str(t.get("team_id"))
                  for t in api.list_teams() if t.get("team_alias")}
    mapa: Dict[str, str] = {}
    for desenho in TEAMS:
        alias = desenho["team_alias"]
        if alias in existentes:
            mapa[alias] = existentes[alias]
            log(f"  = time '{alias}' já existe")
            continue
        if not escrever:
            log(f"  + time '{alias}' seria criado")
            continue
        resposta = api.request("POST", "/team/new", {
            "team_alias": alias,
            "rpm_limit": desenho["rpm_limit"],
            "tpm_limit": desenho["tpm_limit"],
            "max_budget": desenho["max_budget"],
        })
        mapa[alias] = str(resposta.get("team_id") or "")
        log(f"  + time '{alias}' criado (rpm={desenho['rpm_limit']} "
            f"tpm={desenho['tpm_limit']} orçamento={desenho['max_budget']})")
    return mapa


def remaining_seconds(registro: Dict[str, Any]) -> Optional[float]:
    """Segundos até o `expires` do registro, ou None se a validade não existe.

    Tolera ISO-8601 e epoch (texto ou número) pelo mesmo motivo de
    `models.parse_instante`: um epoch gravado como texto é a classe de bug que
    originou estes projetos, e não é aqui que ela vai passar em silêncio.
    """
    valor = registro.get("expires")
    if valor in (None, ""):
        return None
    try:
        if isinstance(valor, (int, float)) or str(valor).strip().isdigit():
            segundos = float(str(valor).strip())
            if segundos > 1e11:  # veio em milissegundos
                segundos /= 1000.0
            instante = datetime.fromtimestamp(segundos, tz=timezone.utc)
        else:
            instante = datetime.fromisoformat(str(valor).strip().replace("Z", "+00:00"))
    except (ValueError, OSError, OverflowError):
        return None
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)
    return (instante - datetime.now(timezone.utc)).total_seconds()


def needs_rearm(registro: Dict[str, Any], margem: Optional[int] = None) -> bool:
    """A chave efêmera saiu da janela de renovação e não serve mais de exemplo.

    Fora da janela é tanto o que já venceu (restante <= 0) quanto o que ainda
    está longe demais (restante > margem) — este segundo caso só acontece se
    alguém mexer na margem, mas custa uma comparação e evita uma bancada muda.
    """
    margem = refresh_margin() if margem is None else margem
    restante = remaining_seconds(registro)
    return restante is None or restante <= 0 or restante > margem


def garantir_chaves(api: AdminAPI, times: Dict[str, str], escrever: bool = True) -> int:
    """Cria as chaves virtuais que faltam. O token gerado NUNCA é impresso."""
    existentes = {str(k.get("key_alias")): k for k in api.list_keys() if k.get("key_alias")}
    criadas = 0
    for desenho in KEYS:
        alias = desenho["key_alias"]
        # A chave efêmera não declara validade fixa: ela sai da margem vigente.
        duracao = ephemeral_duration() if desenho.get("ephemeral") else desenho["duration"]
        vivo = existentes.get(alias)
        if vivo is not None:
            # Só a chave efêmera é reavaliada; as demais valem enquanto existirem.
            if not desenho.get("ephemeral") or not needs_rearm(vivo):
                log(f"  = chave '{alias}' já existe")
                continue
            if not escrever:
                log(f"  ~ chave '{alias}' seria rearmada (saiu da janela de renovação)")
                continue
            api.request("POST", "/key/delete", {"key_aliases": [alias]})
            log(f"  ~ chave '{alias}' saiu da janela de renovação: removida para rearmar")
        elif not escrever:
            log(f"  + chave '{alias}' seria criada (duration={duracao})")
            continue
        payload: Dict[str, Any] = {
            "key_alias": alias,
            # Sem `duration` o LiteLLM grava `expires = null`, e o painel passa
            # a dizer "validade não declarada" — que é a verdade, e é o que esta
            # bancada existe para deixar de ser o caso.
            "duration": duracao,
        }
        time_id = times.get(desenho.get("team_alias", ""))
        if time_id:
            payload["team_id"] = time_id
        for campo in ("rpm_limit", "tpm_limit", "max_budget", "blocked"):
            if campo in desenho:
                payload[campo] = desenho[campo]
        api.request("POST", "/key/generate", payload)
        criadas += 1
        log(f"  + chave '{alias}' criada (validade={duracao}, "
            f"estado esperado: {desenho['estado']})")
    return criadas


def garantir_credenciais_e_modelos(api: AdminAPI, referencia_de_ambiente: bool,
                                   escrever: bool = True) -> int:
    """Cria credencial nomeada + modelo para cada provedor com chave no ambiente.

    A credencial nomeada não é capricho: o `/model/info` do LiteLLM remove
    `api_key` da resposta (`remove_sensitive_info_from_deployment`), mas mantém
    `litellm_credential_name` na lista de exceções. Uma chave declarada direto
    no modelo fica INVISÍVEL para quem lê a API — e o painel, corretamente, não
    tem o que mostrar. A credencial nomeada é a única procedência legível.
    """
    creds = {str(c.get("credential_name")) for c in api.list_credentials()
             if c.get("credential_name")}
    modelos = {str(m.get("model_name")) for m in api.list_models() if m.get("model_name")}
    criados = 0

    for provedor in PROVIDERS:
        env_var = provedor["env_var"]
        if not os.environ.get(env_var):
            log(f"  ! provedor '{provedor['nome']}' pulado: {env_var} não está no ambiente")
            continue

        nome_cred = provedor["credential_name"]
        if nome_cred in creds:
            log(f"  = credencial '{nome_cred}' já existe")
        elif not escrever:
            log(f"  + credencial '{nome_cred}' seria criada a partir de {env_var}")
        else:
            api.request("POST", "/credentials", {
                "credential_name": nome_cred,
                "credential_info": {"custom_llm_provider": provedor["nome"]},
                # O valor sai do ambiente e entra cifrado no proxy. Ele não
                # aparece em log, nem na saída deste script, nem no repositório.
                "credential_values": {
                    "api_key": valor_da_credencial(env_var, referencia_de_ambiente)
                },
            })
            log(f"  + credencial '{nome_cred}' criada a partir de {env_var}"
                + (" (por referência os.environ)" if referencia_de_ambiente else ""))

        nome_modelo = provedor["model_name"]
        if nome_modelo in modelos:
            log(f"  = modelo '{nome_modelo}' já existe")
            continue
        if not escrever:
            log(f"  + modelo '{nome_modelo}' seria criado apontando para {provedor['model']}")
            continue
        params: Dict[str, Any] = {
            "model": provedor["model"],
            "litellm_credential_name": nome_cred,
        }
        if provedor.get("api_base"):
            params["api_base"] = provedor["api_base"]
        api.request("POST", "/model/new", {
            "model_name": nome_modelo,
            "litellm_params": params,
            "model_info": {"id": f"{BENCH_PREFIX}{provedor['nome']}"},
        })
        criados += 1
        log(f"  + modelo '{nome_modelo}' criado → {provedor['model']}")
    return criados


# -- aposentadoria do demo feito à mão ----------------------------------------

# O que existia no proxy antes desta bancada: objetos criados à mão numa sessão
# anterior, sem validade e sem credencial nomeada. São EXATAMENTE o que faz o
# painel dizer "Expiry unknown" e "Not exposed by the gateway", porque o dado
# não existe do outro lado. A bancada os substitui — mas só quando pedido, e
# nomeando cada um: apagar por adivinhação o que outra pessoa criou não é
# trabalho de script.
DEMO_LEGADO = {
    "key_aliases": ["chave-incoerente-rpm600", "chave-coerente-rpm30"],
    "team_aliases": ["time-demo-teto60"],
    "model_names": ["mistral-small", "groq-gpt-oss-120b", "gemini-3.6-flash"],
}


def aposentar_demo(api: AdminAPI) -> None:
    """Remove o demo feito à mão, nomeadamente. Nada por prefixo, nada por regra."""
    modelos = {str(m.get("model_name")): (m.get("model_info") or {}).get("id")
               for m in api.list_models()}
    for nome in DEMO_LEGADO["model_names"]:
        ident = modelos.get(nome)
        if ident:
            api.request("POST", "/model/delete", {"id": str(ident)})
            log(f"  - modelo legado '{nome}' removido")

    aliases = {str(k.get("key_alias")) for k in api.list_keys()}
    alvos = [a for a in DEMO_LEGADO["key_aliases"] if a in aliases]
    if alvos:
        api.request("POST", "/key/delete", {"key_aliases": alvos})
        for a in alvos:
            log(f"  - chave legada '{a}' removida")

    times = {str(t.get("team_alias")): str(t.get("team_id")) for t in api.list_teams()}
    ids = [times[a] for a in DEMO_LEGADO["team_aliases"] if a in times]
    if ids:
        api.request("POST", "/team/delete", {"team_ids": ids})
        for a in DEMO_LEGADO["team_aliases"]:
            if a in times:
                log(f"  - time legado '{a}' removido")


# -- limpeza ------------------------------------------------------------------


def limpar(api: AdminAPI) -> None:
    """Remove SÓ o que tem o prefixo da bancada, na ordem que o proxy aceita.

    Modelos antes das credenciais (um modelo amarrado a credencial apagada fica
    órfão), chaves antes dos times (o LiteLLM recusa apagar time com chave viva).
    """
    modelos = [m for m in api.list_models()
               if str(m.get("model_name", "")).startswith(BENCH_PREFIX)]
    for modelo in modelos:
        ident = (modelo.get("model_info") or {}).get("id")
        if not ident:
            continue
        api.request("POST", "/model/delete", {"id": str(ident)})
        log(f"  - modelo '{modelo.get('model_name')}' removido")

    for cred in api.list_credentials():
        nome = str(cred.get("credential_name") or "")
        if nome.startswith(BENCH_PREFIX):
            api.request("DELETE", f"/credentials/{urllib.parse.quote(nome)}")
            log(f"  - credencial '{nome}' removida")

    aliases = [str(k.get("key_alias")) for k in api.list_keys()
               if str(k.get("key_alias") or "").startswith(BENCH_PREFIX)]
    if aliases:
        # Por alias, e não por token: o token só existe no instante da criação,
        # e guardá-lo em algum lugar para poder apagar depois seria criar um
        # arquivo de segredo que não precisa existir.
        api.request("POST", "/key/delete", {"key_aliases": aliases})
        for alias in aliases:
            log(f"  - chave '{alias}' removida")

    ids = [str(t.get("team_id")) for t in api.list_teams()
           if str(t.get("team_alias") or "").startswith(BENCH_PREFIX)]
    if ids:
        api.request("POST", "/team/delete", {"team_ids": ids})
        for t in ids:
            log(f"  - time {t} removido")


# -- conferência --------------------------------------------------------------


def conferir(api: AdminAPI) -> Dict[str, int]:
    """Estado da bancada como o painel vai lê-lo. Nenhum segredo sai daqui."""
    chaves = [k for k in api.list_keys()
              if str(k.get("key_alias") or "").startswith(BENCH_PREFIX)]
    modelos = [m for m in api.list_models()
               if str(m.get("model_name") or "").startswith(BENCH_PREFIX)]
    times = [t for t in api.list_teams()
             if str(t.get("team_alias") or "").startswith(BENCH_PREFIX)]

    log("")
    log("Estado da bancada, pela API administrativa:")
    log(f"  times   : {len(times)}")
    for t in times:
        log(f"            {t.get('team_alias')} — rpm={t.get('rpm_limit')} "
            f"tpm={t.get('tpm_limit')} orçamento={t.get('max_budget')}")
    log(f"  chaves  : {len(chaves)}")
    com_validade = 0
    for k in chaves:
        expira = k.get("expires")
        if expira:
            com_validade += 1
        log(f"            {k.get('key_alias')} — expires={expira or 'null'} "
            f"rpm={k.get('rpm_limit')} bloqueada={bool(k.get('blocked'))}")
    log(f"  modelos : {len(modelos)}")
    sem_credencial = 0
    for m in modelos:
        params = m.get("litellm_params") or {}
        cred = params.get("litellm_credential_name")
        if not cred:
            sem_credencial += 1
        log(f"            {m.get('model_name')} — credencial nomeada="
            f"{cred or '(nenhuma)'} api_base={params.get('api_base') or '(padrão)'}")

    return {
        "teams": len(times),
        "keys": len(chaves),
        "keys_com_validade": com_validade,
        "models": len(modelos),
        "models_sem_credencial": sem_credencial,
    }


# -- entrada ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Popula (ou limpa) a bancada de testes do LiteLlmRTKSync.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--url", default=os.environ.get("BENCH_LITELLM_URL", DEFAULT_URL),
                   help=f"URL do proxy LiteLLM (padrão: {DEFAULT_URL})")
    p.add_argument("--limpar", action="store_true",
                   help=f"remove tudo que começa com '{BENCH_PREFIX}' e sai")
    p.add_argument("--conferir", action="store_true",
                   help="só mostra o estado atual, sem escrever nada")
    p.add_argument("--aposentar-demo", action="store_true",
                   help="remove também o demo feito à mão que a bancada substitui "
                        f"({', '.join(DEMO_LEGADO['key_aliases'] + DEMO_LEGADO['model_names'])}). "
                        "Opt-in de propósito: são objetos que este script não criou.")
    p.add_argument("--referencia-ambiente", action="store_true",
                   help="grava a credencial como 'os.environ/NOME' em vez do valor. "
                        "O LiteLLM 1.100.1 NÃO resolve essa referência dentro de uma "
                        "credencial nomeada — o modelo sobe com chave inválida. "
                        "Existe para o dia em que o upstream resolver.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    carregar_env_do_arquivo(os.path.join(raiz, ".env"))

    master_key = os.environ.get("LITELLM_MASTER_KEY", "")
    if not master_key:
        log("ERRO: LITELLM_MASTER_KEY não está no ambiente nem no .env do repositório.")
        return 2

    api = AdminAPI(args.url, master_key)

    try:
        if args.limpar:
            log(f"Limpando a bancada em {args.url} (só o prefixo '{BENCH_PREFIX}')…")
            limpar(api)
            conferir(api)
            return 0

        escrever = not args.conferir
        if args.aposentar_demo and escrever:
            log("Aposentando o demo feito à mão que esta bancada substitui:")
            aposentar_demo(api)
        if args.conferir:
            log(f"Conferindo a bancada em {args.url} (nada será escrito)…")
        else:
            log(f"Populando a bancada em {args.url}…")

        # O valor vindo do ambiente é o padrão porque é a única forma que o
        # LiteLLM 1.100.1 realmente honra em credencial nomeada; veja o
        # comentário de `valor_da_credencial`.
        referencia = args.referencia_ambiente

        log("Times:")
        times = garantir_times(api, escrever)
        if not times:
            # Em modo conferência o mapa vem vazio; releitura resolve.
            times = {str(t.get("team_alias")): str(t.get("team_id"))
                     for t in api.list_teams() if t.get("team_alias")}
        log("Chaves virtuais:")
        garantir_chaves(api, times, escrever)
        log("Credenciais e modelos:")
        garantir_credenciais_e_modelos(api, referencia, escrever)

        if escrever:
            # A chave de 1 s precisa vencer antes da conferência, senão ela
            # aparece como ativa e o relatório mente sobre o próprio estado.
            time.sleep(2)
        resumo = conferir(api)

        log("")
        if resumo["keys_com_validade"] < len(KEYS):
            log("AVISO: nem toda chave da bancada tem validade — o painel vai "
                "continuar mostrando 'Expiry unknown' para as que faltam.")
        if resumo["models_sem_credencial"]:
            log("AVISO: há modelo da bancada sem credencial nomeada — a coluna "
                "Credential vai continuar sem procedência legível.")
        log("Pronto. O painel do sincronizador lê as chaves ao vivo; a tabela de "
            "modelos só muda depois de um ciclo (reinicie o container do sync ou "
            "espere SYNC_INTERVAL).")
        return 0
    except BenchError as e:
        log(f"ERRO: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
