"""Domínio: tudo o que este sincronizador sabe sobre o gateway a que se liga.

Este é o ÚNICO módulo do pacote onde a divergência entre os três irmãos é
legítima e fica. Painel, render, sessão, SSO e i18n são o mesmo texto nos três;
o que muda é o que existe atrás deste arquivo — aqui um proxy com API HTTP e
Postgres, nos irmãos um SQLite no disco do gateway.

Ele reúne o que antes morava em três módulos separados (`client.py`, `limits.py`
e `engine.py`). Separados, eles eram três nomes de arquivo que os irmãos não
tinham, e enquanto os nomes fossem diferentes nenhum teste conseguia afirmar que
o resto era igual.

Por que API e não banco: o gateway guarda o estado em Postgres via Prisma, e o
schema muda entre versões — as tabelas de token, time, credencial e modelo já
mudaram de forma mais de uma vez. Escrever direto na tabela ignoraria os
invariantes que o proxy aplica e quebraria a cada atualização. A API
administrativa é o contrato público, e é ela que este sincronizador usa.

Somente leitura: nada aqui altera chave, limite ou modelo.
"""

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import Settings
from .credential_check import (
    STATE_INVALID,
    STATE_RATE_LIMITED,
    STATE_UNREACHABLE,
    STATE_VALID,
    check_api_key,
)
from .identidade import NOME_DO_PRODUTO
from .logs import get_logger
from .models import RegisteredModelRecord, VirtualKeyRecord, summarize


# ---------------------------------------------------------------------------
# Cliente da API administrativa
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT_SECONDS = 10.0
USER_AGENT = f"{NOME_DO_PRODUTO}/1.0"


class GatewayError(RuntimeError):
    """Falha ao falar com o proxy. Carrega o status para o chamador decidir."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class GatewayClient:
    """Leitura do estado administrativo do proxy deste gateway.

    Só faz GET. Este sincronizador relata e valida; quem altera limite, chave ou
    modelo é o operador, pelas telas do próprio gateway.
    """

    def __init__(
        self,
        base_url: str,
        master_key: str = "",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        opener: Optional[Any] = None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.master_key = master_key or ""
        self.timeout = timeout
        self.opener = opener

    # -- transporte ---------------------------------------------------------

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, method="GET")
        request.add_header("User-Agent", USER_AGENT)
        if self.master_key:
            request.add_header("Authorization", f"Bearer {self.master_key}")

        send = self.opener or urllib.request.urlopen
        try:
            with send(request, timeout=self.timeout) as resposta:
                corpo = resposta.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            # 401 aqui quase sempre significa master key errada, e dizer isso
            # poupa o operador de procurar o erro no lugar errado.
            detalhe = "master key recusada" if e.code in (401, 403) else str(e)
            raise GatewayError(f"{path}: {detalhe}", status=e.code) from e
        except (urllib.error.URLError, OSError) as e:
            raise GatewayError(f"{path}: proxy inacessível ({e})") from e

        if not corpo.strip():
            return {}
        try:
            return json.loads(corpo)
        except ValueError as e:
            raise GatewayError(f"{path}: resposta não é JSON ({e})") from e

    # -- leitura ------------------------------------------------------------

    def health(self) -> bool:
        """Se o proxy responde. Não exige credencial."""
        try:
            self._get("/health/liveliness")
            return True
        except GatewayError:
            return False

    def list_keys(self, page_size: int = 100) -> List[Dict[str, Any]]:
        """Chaves virtuais. Pagina até o fim, porque o padrão da API é parcial."""
        chaves: List[Dict[str, Any]] = []
        pagina = 1
        while True:
            dados = self._get(
                "/key/list",
                {"page": pagina, "size": page_size, "return_full_object": "true"},
            )
            lote = dados.get("keys") if isinstance(dados, dict) else None
            if not lote:
                break
            # Com return_full_object a API devolve objetos; sem ele, strings.
            chaves.extend(k for k in lote if isinstance(k, dict))
            total_paginas = (dados or {}).get("total_pages") or 1
            if pagina >= int(total_paginas):
                break
            pagina += 1
        return chaves

    def list_teams(self) -> List[Dict[str, Any]]:
        dados = self._get("/team/list")
        if isinstance(dados, list):
            return [t for t in dados if isinstance(t, dict)]
        return [t for t in (dados or {}).get("teams", []) if isinstance(t, dict)]

    def list_models(self) -> List[Dict[str, Any]]:
        """Modelos cadastrados, com `litellm_params` (onde mora a chave real).

        Uma instalação sem modelo nenhum é estado normal — e o gateway responde
        **500** a esta rota nesse caso, não 200 com lista vazia. Tratar isso
        como falha derrubaria o ciclo inteiro de uma instalação recém-criada,
        então a ausência de modelos vira exatamente o que é: nenhum modelo.
        """
        try:
            dados = self._get("/model/info")
        except GatewayError as e:
            if e.status in (404, 500):
                return []
            raise
        return [m for m in (dados or {}).get("data", []) if isinstance(m, dict)]

    def router_settings(self) -> Dict[str, Any]:
        """Configuração corrente do roteador — é de onde saem os fallbacks.

        Devolve só `current_values`: o resto da resposta é metadado de formulário
        (tipo, descrição, opções de cada campo) que serve à tela de administração
        do próprio gateway e não a este painel.

        Proxy antigo não tem a rota, e proxy sem roteador iniciado responde 500.
        Nos dois casos a resposta honesta é "nenhum fallback declarado", e não
        derrubar a página: quem some é o conteúdo do cartão, nunca o cartão.
        """
        try:
            dados = self._get("/router/settings")
        except GatewayError as e:
            if e.status in (404, 405, 500):
                return {}
            raise
        if not isinstance(dados, dict):
            return {}
        valores = dados.get("current_values")
        return valores if isinstance(valores, dict) else {}

    def health_check(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Veredito do PRÓPRIO proxy sobre cada modelo cadastrado.

        Por que esta rota existe no cliente: o `/model/info` **remove** o campo
        `api_key` da resposta — `remove_sensitive_info_from_deployment` faz
        `deployment_dict["litellm_params"].pop("api_key", None)` antes de
        qualquer mascaramento. Não é mascarado, é removido; nem o segredo nem
        uma referência `os.environ/NOME` chegam aqui. Logo, validar a chave de
        um modelo a partir do cadastro é impossível por construção.

        Quem tem o segredo é o proxy. O `/health` faz ele mesmo uma chamada real
        a cada provedor e devolve o resultado por `model_id`. O veredito passa a
        vir de quem pode emiti-lo, em vez de a tela dizer "não verificada" para
        sempre.

        Custo: uma chamada real por modelo a cada execução. Fica sob
        `CREDENTIAL_CHECK_ENABLED`, como o resto da validação viva.

        O timeout é próprio porque esta rota fala com N provedores numa
        requisição só; o padrão de 10 s do cliente serve para leitura de
        cadastro, não para isso.
        """
        anterior = self.timeout
        if timeout:
            self.timeout = timeout
        try:
            dados = self._get("/health")
        except GatewayError as e:
            # Versão sem a rota, ou proxy ocupado: ausência de veredito é um
            # estado legítimo e não pode derrubar o ciclo inteiro.
            if e.status in (404, 405):
                return {}
            raise
        finally:
            self.timeout = anterior
        return dados if isinstance(dados, dict) else {}

    def list_credentials(self) -> List[Dict[str, Any]]:
        """Credenciais nomeadas e reutilizáveis (a tabela de credenciais)."""
        try:
            dados = self._get("/credentials")
        except GatewayError as e:
            # Versões anteriores à tabela de credenciais não expõem a rota.
            if e.status == 404:
                return []
            raise
        return [c for c in (dados or {}).get("credentials", []) if isinstance(c, dict)]


# ---------------------------------------------------------------------------
# Coerência da hierarquia de limites
#
# O gateway aceita limite em três níveis — chave virtual, time e o padrão da
# plataforma — e **não recusa** um limite de chave maior que o do time que a
# contém. O resultado é um limite que existe no cadastro e não vale na prática:
# quem impõe é sempre o teto mais restritivo encontrado no caminho da
# requisição. Um time com `rpm_limit=60` e uma chave com `rpm_limit=600` não
# entrega 600; o operador acha que configurou 600 e recebe 60, sem nada dizer o
# contrário. Aqui os níveis são comparados e cada incoerência é nomeada; nada é
# corrigido, porque mexer no limite de alguém é decisão do operador.
# ---------------------------------------------------------------------------

# Campos que existem nos três níveis e para os quais "menor é mais restritivo".
CAMPOS_DE_TETO = ("tpm_limit", "rpm_limit", "max_parallel_requests", "max_budget")

SEVERIDADE_INCOERENTE = "incoerente"
SEVERIDADE_SEM_TETO = "sem_teto"
SEVERIDADE_OK = "ok"


@dataclass
class Achado:
    """Uma incoerência entre dois níveis, já em linguagem de operador."""

    key_alias: str
    team_alias: str
    campo: str
    valor_da_chave: Optional[float]
    valor_do_teto: Optional[float]
    nivel_do_teto: str
    severidade: str

    @property
    def mensagem(self) -> str:
        if self.severidade == SEVERIDADE_SEM_TETO:
            return (
                f"{self.campo}: a chave '{self.key_alias}' declara "
                f"{_formatar(self.valor_da_chave)} e nada acima dela limita esse valor"
            )
        return (
            f"{self.campo}: a chave '{self.key_alias}' declara "
            f"{_formatar(self.valor_da_chave)}, acima do teto de "
            f"{_formatar(self.valor_do_teto)} do {self.nivel_do_teto} "
            f"'{self.team_alias}' — na prática vale o teto, não o que está na chave"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "keyAlias": self.key_alias,
            "teamAlias": self.team_alias,
            "field": self.campo,
            "keyValue": self.valor_da_chave,
            "capValue": self.valor_do_teto,
            "capLevel": self.nivel_do_teto,
            "severity": self.severidade,
            "message": self.mensagem,
        }


@dataclass
class Relatorio:
    achados: List[Achado] = field(default_factory=list)
    chaves_avaliadas: int = 0

    @property
    def coerente(self) -> bool:
        return not any(a.severidade == SEVERIDADE_INCOERENTE for a in self.achados)

    def por_severidade(self, severidade: str) -> List[Achado]:
        return [a for a in self.achados if a.severidade == severidade]


def _formatar(valor: Optional[float]) -> str:
    if valor is None:
        return "sem limite"
    if float(valor).is_integer():
        return str(int(valor))
    return f"{valor:g}"


def _numero(valor: Any) -> Optional[float]:
    """Converte para número, tratando ausência e texto vazio como "sem limite"."""
    if valor is None or valor == "":
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    # O gateway aceita 0 como "sem limite" em alguns campos; tratar 0 como teto
    # zerado acusaria incoerência em toda chave da instalação.
    return None if numero <= 0 else numero


def _identificar(registro: Dict[str, Any], *chaves: str) -> str:
    """Nome legível de um registro, sem jamais devolver a credencial inteira."""
    for chave in chaves:
        valor = registro.get(chave)
        if not valor:
            continue
        if chave == "token":
            # Sem apelido, o unico identificador e o proprio token. Ele serve
            # para o operador reconhecer a linha, nao para ser copiado: vai
            # mascarado, como no painel.
            texto = str(valor)
            return f"…{texto[-6:]}" if len(texto) > 6 else "(sem nome)"
        return str(valor)
    return "(sem nome)"


def avaliar(
    chaves: List[Dict[str, Any]],
    times: List[Dict[str, Any]],
    padrao_da_plataforma: Optional[Dict[str, Any]] = None,
) -> Relatorio:
    """Compara cada chave com o time dela e com o padrão da plataforma.

    A regra é uma só, aplicada campo a campo: **o valor de um nível nunca pode
    ser maior que o do nível acima**. Quando a chave não pertence a time algum,
    o teto é o padrão da plataforma; quando também não há padrão, o caso é
    relatado como "sem teto" — não é erro, é uma escolha que merece ser vista.
    """
    padrao = padrao_da_plataforma or {}
    por_time = {str(t.get("team_id")): t for t in times if t.get("team_id")}
    relatorio = Relatorio()

    for chave in chaves:
        relatorio.chaves_avaliadas += 1
        apelido_chave = _identificar(chave, "key_alias", "key_name", "token")
        time = por_time.get(str(chave.get("team_id") or ""))
        if time:
            teto = time
            nivel = "time"
            apelido_teto = _identificar(time, "team_alias", "team_id")
        else:
            teto = padrao
            nivel = "padrão da plataforma"
            apelido_teto = "plataforma"

        for campo in CAMPOS_DE_TETO:
            valor_chave = _numero(chave.get(campo))
            if valor_chave is None:
                # Chave sem limite herda o de cima; nada a comparar.
                continue
            valor_teto = _numero(teto.get(campo))
            if valor_teto is None:
                relatorio.achados.append(
                    Achado(apelido_chave, apelido_teto, campo, valor_chave, None, nivel,
                           SEVERIDADE_SEM_TETO)
                )
                continue
            if valor_chave > valor_teto:
                relatorio.achados.append(
                    Achado(apelido_chave, apelido_teto, campo, valor_chave, valor_teto,
                           nivel, SEVERIDADE_INCOERENTE)
                )

    # Um time também não pode exceder o padrão da plataforma.
    for time in times:
        apelido_time = _identificar(time, "team_alias", "team_id")
        for campo in CAMPOS_DE_TETO:
            valor_time = _numero(time.get(campo))
            valor_padrao = _numero(padrao.get(campo))
            if valor_time is None or valor_padrao is None:
                continue
            if valor_time > valor_padrao:
                relatorio.achados.append(
                    Achado(apelido_time, "plataforma", campo, valor_time, valor_padrao,
                           "padrão da plataforma", SEVERIDADE_INCOERENTE)
                )

    return relatorio


# ---------------------------------------------------------------------------
# Motor de inspeção
#
# Os irmãos **renovam** credenciais OAuth, porque o gateway deles guarda tokens
# que expiram e que só alguém de fora percebe quando morrem. Este gateway não
# tem OAuth de consumidor — as credenciais são chaves de provedor no cadastro de
# modelos e chaves virtuais emitidas pelo próprio proxy.
#
# Então aqui não há o que renovar; há o que **verificar**, e três coisas que
# ninguém verifica sozinho:
#
# 1. uma chave virtual expira em silêncio, e a requisição só falha depois;
# 2. uma chave de provedor cadastrada num modelo pode ter sido revogada, e o
#    proxy só descobre na hora de usar;
# 3. um limite de chave maior que o do time existe no cadastro e não vale na
#    prática — o gateway aceita a configuração sem reclamar.
# ---------------------------------------------------------------------------

PREFIXOS_DE_ERRO = {"FALHA", "ERRO", "ERROR", "FAILURE"}
PREFIXOS_DE_AVISO = {"AVISO", "WARN", "WARNING"}

# Quanto do erro do gateway cabe na tela. O `/health` devolve o traceback
# inteiro do proxy junto com a mensagem do provedor; a primeira linha é a que
# diz o que aconteceu, o resto é ruído para quem opera.
LIMITE_DO_DETALHE = 160


def _classificar_erro_do_gateway(erro: Any) -> Dict[str, str]:
    """Traduz o erro que o `/health` do gateway devolve para um estado da tela.

    Conservador de propósito: só chama de RECUSADA o que o provedor disse ser
    problema de autenticação. Qualquer outra falha vira "desconhecido", porque
    marcar de inválida uma chave boa manda o operador trocar a credencial errada.
    """
    texto = str(erro or "").strip()
    primeira_linha = texto.split("\n", 1)[0][:LIMITE_DO_DETALHE] or "sem detalhe"

    # SÓ o cabeçalho entra na classificação. O `/health` cola o traceback do
    # proxy depois da mensagem, e um traceback tem números de linha: uma falha
    # qualquer cujo rastro passe por `File "...", line 401` viraria "chave
    # recusada" na tela, e o operador iria trocar uma credencial que estava boa.
    cabecalho = re.split(r"stack trace", texto, maxsplit=1, flags=re.IGNORECASE)[0]
    minusculo = cabecalho.lower()

    if ("authenticationerror" in minusculo
            or "invalid api key" in minusculo
            or "invalid_api_key" in minusculo
            or "api key not valid" in minusculo
            or "unauthorized" in minusculo
            or "401" in minusculo):
        return {"state": STATE_INVALID, "detail": primeira_linha}
    if "ratelimiterror" in minusculo or "rate limit" in minusculo or "429" in minusculo:
        return {"state": STATE_RATE_LIMITED,
                "detail": f"o provedor limitou a checagem do gateway: {primeira_linha}"}
    if ("apiconnectionerror" in minusculo or "connection" in minusculo
            or "timeout" in minusculo or "timed out" in minusculo):
        return {"state": STATE_UNREACHABLE,
                "detail": f"o gateway não alcançou o provedor: {primeira_linha}"}
    return {"state": "unknown",
            "detail": f"o gateway reprovou o modelo sem dizer que é credencial: {primeira_linha}"}


def log_msg(prefixo: str, texto: str):
    """Nível segue o prefixo: com LOG_LEVEL=WARNING a falha continua aparecendo."""
    logger = get_logger()
    mensagem = f"[{prefixo}] {texto}"
    alvo = str(prefixo).upper()
    if alvo in PREFIXOS_DE_ERRO:
        logger.error(mensagem)
    elif alvo in PREFIXOS_DE_AVISO:
        logger.warning(mensagem)
    else:
        logger.info(mensagem)


class SyncEngine:
    def __init__(self, settings: Settings, client: GatewayClient = None):
        self.settings = settings
        self.client = client or GatewayClient(
            settings.litellm_url, settings.master_key, timeout=settings.validation_timeout
        )
        # O botao da tela e o cron chamam esta mesma instancia, e o servidor web
        # atende em threads. Um ciclo por vez.
        self._lock = threading.Lock()

    def sync_all(self) -> Dict[str, Any]:
        with self._lock:
            return self._sync_all_locked()

    def _sync_all_locked(self) -> Dict[str, Any]:
        agora = datetime.now(timezone.utc).isoformat()
        resumo: Dict[str, Any] = {
            "timestamp": agora,
            "success": True,
            "keys": 0,
            "teams": 0,
            "models": 0,
            "expiring": 0,
            "invalid_credentials": 0,
            "limit_findings": 0,
            "details": [],
            "errors": [],
        }

        # Cada leitura degrada por conta própria. Uma rota indisponível numa
        # versão do proxy não pode apagar o que as outras já responderam — o
        # ciclo diz o que conseguiu ver e o que não conseguiu.
        chaves_brutas = self._ler("chaves", self.client.list_keys, resumo)
        times = self._ler("times", self.client.list_teams, resumo)
        modelos_brutos = self._ler("modelos", self.client.list_models, resumo)

        if chaves_brutas is None and times is None and modelos_brutos is None:
            # Nada respondeu: o proxy está fora, e não há o que relatar.
            resumo["success"] = False
            return resumo

        chaves_brutas = chaves_brutas or []
        times = times or []
        modelos_brutos = modelos_brutos or []

        chaves = [VirtualKeyRecord(k) for k in chaves_brutas]
        modelos = [RegisteredModelRecord(m) for m in modelos_brutos]
        resumo["keys"] = len(chaves)
        resumo["teams"] = len(times)
        resumo["models"] = len(modelos)
        resumo["summary"] = summarize(chaves, self.settings.refresh_margin)

        log_msg("INFO", f"Inspecionando {len(chaves)} chave(s), {len(times)} time(s) e "
                        f"{len(modelos)} modelo(s) em {self.settings.litellm_url}")

        # 1. Chaves virtuais perto do fim ou já vencidas.
        for chave in chaves:
            estado = chave.health_status(self.settings.refresh_margin)
            detalhe = {"kind": "key", "name": chave.alias, "status": estado, "actions": []}
            if estado in ("expiring_soon", "expired", "blocked", "over_budget"):
                resumo["expiring"] += 1
                nota = {
                    "expiring_soon": "vence dentro da margem configurada",
                    "expired": "já venceu e continua cadastrada",
                    "blocked": "bloqueada no proxy",
                    "over_budget": "orçamento esgotado",
                }[estado]
                detalhe["actions"].append(nota)
                log_msg("AVISO" if estado != "expired" else "FALHA",
                        f"[chave · {chave.alias}] {nota}")
            resumo["details"].append(detalhe)

        # 2. Chaves de provedor declaradas nos modelos.
        if self.settings.validate_credentials:
            self._verificar_modelos(modelos, resumo)

        # 3. Coerencia dos limites.
        relatorio = avaliar(chaves_brutas, times, self.settings.platform_caps())
        for achado in relatorio.por_severidade(SEVERIDADE_INCOERENTE):
            resumo["limit_findings"] += 1
            log_msg("AVISO", f"[limite] {achado.mensagem}")
            resumo["details"].append(
                {"kind": "limit", "name": achado.key_alias, "status": achado.severidade,
                 "actions": [achado.mensagem]}
            )

        if resumo["errors"]:
            resumo["success"] = False
        return resumo

    def _ler(self, rotulo: str, funcao, resumo: Dict[str, Any]):
        """Executa uma leitura, registrando a falha em vez de propagá-la."""
        try:
            return funcao()
        except GatewayError as e:
            log_msg("FALHA", f"Não foi possível ler {rotulo}: {e}")
            resumo["errors"].append(f"{rotulo}: {e}")
            return None

    def _veredito_do_gateway(self, modelos: List[RegisteredModelRecord]) -> Dict[str, Dict[str, str]]:
        """Pergunta ao proxy o que ELE acha de cada modelo, por `model_id`.

        Existe porque a pergunta não tem resposta deste lado: o `/model/info`
        remove `api_key` da resposta, então o cadastro nunca traz o segredo nem
        a referência de ambiente. Quem consegue testar a chave é quem a tem — o
        proxy. Esta função traduz o `/health` dele para o mesmo vocabulário de
        estados que a sonda local usa, para que a tela não precise saber de onde
        veio o veredito.
        """
        if not modelos:
            return {}
        # Uma requisição só, mas ela fala com N provedores: o timeout da leitura
        # de cadastro não serve aqui, e estourar transformaria um ciclo inteiro
        # em erro por causa de um provedor lento.
        limite = max(30.0, self.settings.validation_timeout * len(modelos))
        try:
            saude = self.client.health_check(timeout=limite)
        except GatewayError as e:
            log_msg("AVISO", f"Não foi possível obter o veredito do gateway: {e}")
            return {}

        vereditos: Dict[str, Dict[str, str]] = {}
        for item in saude.get("healthy_endpoints") or []:
            if isinstance(item, dict) and item.get("model_id"):
                vereditos[str(item["model_id"])] = {
                    "state": STATE_VALID,
                    "detail": "o gateway chamou o provedor e a chave foi aceita",
                }
        for item in saude.get("unhealthy_endpoints") or []:
            if not isinstance(item, dict) or not item.get("model_id"):
                continue
            vereditos[str(item["model_id"])] = _classificar_erro_do_gateway(item.get("error"))
        return vereditos

    def _verificar_modelos(self, modelos: List[RegisteredModelRecord], resumo: Dict[str, Any]) -> None:
        # Só vale a pena perguntar ao gateway se houver modelo cujo segredo este
        # lado não enxerga — que, com o gateway atual, é todo modelo.
        invisiveis = [m for m in modelos if m.key_is_env_reference or not m.api_key]
        vereditos = self._veredito_do_gateway(invisiveis) if invisiveis else {}

        for modelo in modelos:
            detalhe = {"kind": "model", "name": modelo.name, "status": "unknown", "actions": []}
            if modelo.key_is_env_reference or not modelo.api_key:
                veredito = vereditos.get(modelo.model_id)
                if not veredito:
                    # Sem veredito de ninguém: a chave não está no cadastro e o
                    # gateway não respondeu por este modelo. Dizer "inválida"
                    # seria inventar; "não verificada" é o que de fato houve.
                    detalhe["status"] = "not_checked"
                    detalhe["actions"].append(
                        "chave não exposta pelo cadastro e sem veredito do gateway"
                    )
                    resumo["details"].append(detalhe)
                    continue
                detalhe["status"] = veredito["state"]
                if veredito["state"] == STATE_INVALID:
                    resumo["invalid_credentials"] += 1
                    nota = f"chave RECUSADA pelo provedor ({veredito['detail']})"
                    detalhe["actions"].append(nota)
                    log_msg("FALHA", f"[modelo · {modelo.name}] {nota}")
                else:
                    detalhe["actions"].append(veredito["detail"])
                resumo["details"].append(detalhe)
                continue

            resultado = check_api_key(
                modelo.provider,
                modelo.api_key,
                base_url=modelo.api_base,
                timeout=self.settings.validation_timeout,
            )
            detalhe["status"] = resultado.state
            if resultado.state == STATE_INVALID:
                resumo["invalid_credentials"] += 1
                nota = f"chave RECUSADA pelo provedor ({resultado.detail})"
                detalhe["actions"].append(nota)
                log_msg("FALHA", f"[modelo · {modelo.name}] {nota}")
            elif resultado.state == STATE_RATE_LIMITED:
                detalhe["actions"].append(f"provedor limitou a validação ({resultado.detail})")
            elif resultado.state == STATE_UNREACHABLE:
                detalhe["actions"].append(f"provedor inacessível ({resultado.detail})")
            else:
                detalhe["actions"].append(f"chave aceita pelo provedor ({resultado.detail})")
            resumo["details"].append(detalhe)


# ---------------------------------------------------------------------------
# Combos de resiliência, na forma deste gateway
# ---------------------------------------------------------------------------

# Os três tipos de fallback do roteador do gateway, na ordem em que a tela os
# mostra. O valor é a CHAVE de tradução do rótulo; `general` não tem rótulo
# porque é o caso comum e nomear o óbvio só ocupa a linha.
TIPOS_DE_FALLBACK = (
    ("fallbacks", "general", ""),
    ("context_window_fallbacks", "context_window", "combos.kind_context_window"),
    ("content_policy_fallbacks", "content_policy", "combos.kind_content_policy"),
)


def fallback_combos(router_settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Combos de resiliência do gateway, que aqui se chamam FALLBACKS.

    O conceito existe e é exatamente o mesmo dos irmãos: um modelo principal e
    a cascata que assume quando ele falha. O que muda é o nome e o lugar — no
    gateway daqui isso mora em `router_settings`, e chega por `GET /router/settings`
    na forma `[{"modelo-principal": ["reserva-1", "reserva-2"]}]`.

    São três listas distintas, e juntá-las numa só sem dizer qual é qual seria
    mentira: `context_window_fallbacks` só dispara quando a janela estoura e
    `content_policy_fallbacks` só quando a política recusa. O tipo viaja no
    registro para a tela poder marcá-lo.
    """
    combos: List[Dict[str, Any]] = []
    if not isinstance(router_settings, dict):
        return combos
    for campo, tipo, rotulo in TIPOS_DE_FALLBACK:
        entradas = router_settings.get(campo)
        if isinstance(entradas, dict):
            entradas = [entradas]
        if not isinstance(entradas, list):
            continue
        for entrada in entradas:
            if not isinstance(entrada, dict):
                continue
            for principal, reservas in entrada.items():
                if isinstance(reservas, str):
                    reservas = [reservas]
                if not isinstance(reservas, list):
                    continue
                combos.append({
                    "name": str(principal),
                    "models": [str(m) for m in reservas],
                    "kind": tipo,
                    "kindLabelKey": rotulo,
                })
    return combos
