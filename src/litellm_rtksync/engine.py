"""Motor de inspeção do LiteLLM.

Diferença estrutural para os irmãos: 9RTKSync e OminiRTkSync **renovam**
credenciais OAuth, porque o gateway deles guarda tokens que expiram e que só
alguém de fora percebe quando morrem. O LiteLLM não tem OAuth de consumidor —
as credenciais são chaves de provedor no cadastro de modelos e chaves virtuais
emitidas pelo próprio proxy.

Então aqui não há o que renovar; há o que **verificar**, e três coisas que
ninguém verifica sozinho:

1. uma chave virtual expira em silêncio, e a requisição só falha depois;
2. uma chave de provedor cadastrada num modelo pode ter sido revogada, e o
   proxy só descobre na hora de usar;
3. um limite de chave maior que o do time existe no cadastro e não vale na
   prática — o LiteLLM aceita a configuração sem reclamar.

Somente leitura: nada aqui altera chave, limite ou modelo.
"""

import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List

from .client import LiteLLMClient, LiteLLMError
from .config import Settings
from .credential_check import (
    STATE_INVALID,
    STATE_RATE_LIMITED,
    STATE_UNREACHABLE,
    STATE_VALID,
    check_api_key,
)
from .limits import SEVERIDADE_INCOERENTE, avaliar
from .logs import get_logger
from .models import ModelEntry, VirtualKey, summarize

PREFIXOS_DE_ERRO = {"FALHA", "ERRO", "ERROR", "FAILURE"}
PREFIXOS_DE_AVISO = {"AVISO", "WARN", "WARNING"}

# Quanto do erro do gateway cabe na tela. O `/health` devolve o traceback
# inteiro do proxy junto com a mensagem do provedor; a primeira linha é a que
# diz o que aconteceu, o resto é ruído para quem opera.
LIMITE_DO_DETALHE = 160


def _classificar_erro_do_gateway(erro: Any) -> Dict[str, str]:
    """Traduz o erro que o `/health` do LiteLLM devolve para um estado da tela.

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


class LiteLLMSyncEngine:
    def __init__(self, settings: Settings, client: LiteLLMClient = None):
        self.settings = settings
        self.client = client or LiteLLMClient(
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

        chaves = [VirtualKey(k) for k in chaves_brutas]
        modelos = [ModelEntry(m) for m in modelos_brutos]
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
        except LiteLLMError as e:
            log_msg("FALHA", f"Não foi possível ler {rotulo}: {e}")
            resumo["errors"].append(f"{rotulo}: {e}")
            return None

    def _veredito_do_gateway(self, modelos: List[ModelEntry]) -> Dict[str, Dict[str, str]]:
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
        except LiteLLMError as e:
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

    def _verificar_modelos(self, modelos: List[ModelEntry], resumo: Dict[str, Any]) -> None:
        # Só vale a pena perguntar ao gateway se houver modelo cujo segredo este
        # lado não enxerga — que, com o LiteLLM atual, é todo modelo.
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
