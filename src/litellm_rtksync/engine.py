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

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List

from .client import LiteLLMClient, LiteLLMError
from .config import Settings
from .credential_check import STATE_INVALID, STATE_RATE_LIMITED, STATE_UNREACHABLE, check_api_key
from .limits import SEVERIDADE_INCOERENTE, avaliar
from .logs import get_logger
from .models import ModelEntry, VirtualKey, summarize

PREFIXOS_DE_ERRO = {"FALHA", "ERRO", "ERROR", "FAILURE"}
PREFIXOS_DE_AVISO = {"AVISO", "WARN", "WARNING"}


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

    def _verificar_modelos(self, modelos: List[ModelEntry], resumo: Dict[str, Any]) -> None:
        for modelo in modelos:
            detalhe = {"kind": "model", "name": modelo.name, "status": "unknown", "actions": []}
            if modelo.key_is_env_reference or not modelo.api_key:
                # A chave vive no ambiente do proxy, nao no cadastro: nao ha o
                # que verificar daqui, e dizer "invalida" seria falso.
                detalhe["status"] = "not_checked"
                detalhe["actions"].append(
                    "chave mantida no ambiente do proxy; não verificável a partir do cadastro"
                )
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
