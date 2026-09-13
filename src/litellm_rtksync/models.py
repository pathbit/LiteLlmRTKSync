"""Registros do LiteLLM, já com as perguntas que o painel precisa responder."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Estados de saúde, os mesmos vocabulários dos projetos irmãos.
SAUDE_ATIVA = "active"
SAUDE_EXPIRANDO = "expiring_soon"
SAUDE_EXPIRADA = "expired"
SAUDE_BLOQUEADA = "blocked"
SAUDE_ESTOURADA = "over_budget"
SAUDE_DESCONHECIDA = "unknown"


def parse_instante(valor: Any) -> Optional[datetime]:
    """Lê um instante em ISO-8601 ou epoch, tolerando as duas formas.

    O mesmo cuidado dos irmãos: um epoch numérico gravado como texto é a origem
    da classe de bug que motivou estes projetos. Aqui a API devolve ISO, mas o
    campo passa por exportação e importação, e uma dessas etapas pode numerar.
    """
    if valor in (None, ""):
        return None
    if isinstance(valor, (int, float)):
        segundos = float(valor)
        # Heurística de segundos vs milissegundos, igual à dos irmãos.
        if segundos > 1e11:
            segundos /= 1000.0
        return datetime.fromtimestamp(segundos, tz=timezone.utc)
    texto = str(valor).strip()
    if texto.isdigit():
        return parse_instante(int(texto))
    try:
        momento = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None
    return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)


@dataclass
class VirtualKey:
    """Uma chave virtual do LiteLLM (`LiteLLM_VerificationToken`)."""

    raw: Dict[str, Any]

    @property
    def alias(self) -> str:
        for campo in ("key_alias", "key_name"):
            if self.raw.get(campo):
                return str(self.raw[campo])
        token = str(self.raw.get("token") or "")
        # Nunca o token inteiro: o painel mostra o suficiente para identificar.
        return f"…{token[-6:]}" if token else "(sem apelido)"

    @property
    def team_id(self) -> Optional[str]:
        valor = self.raw.get("team_id")
        return str(valor) if valor else None

    @property
    def expires_at(self) -> Optional[datetime]:
        return parse_instante(self.raw.get("expires"))

    @property
    def remaining_seconds(self) -> Optional[int]:
        instante = self.expires_at
        if instante is None:
            return None
        return int((instante - datetime.now(timezone.utc)).total_seconds())

    @property
    def created_at(self) -> Optional[str]:
        """Quando a chave foi emitida.

        Chave virtual nao se renova -- nasce com prazo e vence -- entao este e o
        unico carimbo de tempo que ela tem. E o que a coluna "ultima renovacao"
        mostra aqui, para a tabela ter as mesmas sete colunas dos irmaos.
        """
        return self.raw.get("created_at") or self.raw.get("created_by_at")

    @property
    def blocked(self) -> bool:
        return bool(self.raw.get("blocked"))

    @property
    def spend(self) -> float:
        try:
            return float(self.raw.get("spend") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def max_budget(self) -> Optional[float]:
        valor = self.raw.get("max_budget")
        try:
            numero = float(valor)
        except (TypeError, ValueError):
            return None
        return numero if numero > 0 else None

    @property
    def budget_exhausted(self) -> bool:
        teto = self.max_budget
        return teto is not None and self.spend >= teto

    def health_status(self, margin_seconds: int = 900) -> str:
        """Estado que o painel exibe.

        A ordem importa: bloqueio e expiração são fatos; orçamento estourado
        vem depois, porque uma chave expirada e estourada é, antes de tudo,
        expirada.
        """
        if self.blocked:
            return SAUDE_BLOQUEADA
        restante = self.remaining_seconds
        if restante is not None:
            if restante <= 0:
                return SAUDE_EXPIRADA
            if restante <= margin_seconds:
                return SAUDE_EXPIRANDO
        if self.budget_exhausted:
            return SAUDE_ESTOURADA
        # Sem validade declarada não é "ilimitada": é validade não declarada.
        return SAUDE_ATIVA

    def to_dict(self, margin_seconds: int = 900) -> Dict[str, Any]:
        """Projeção explícita. O token nunca sai daqui."""
        return {
            "alias": self.alias,
            "teamId": self.team_id,
            "expiresAt": self.expires_at.isoformat() if self.expires_at else None,
            "remainingSeconds": self.remaining_seconds,
            "blocked": self.blocked,
            "spend": self.spend,
            "maxBudget": self.max_budget,
            "healthStatus": self.health_status(margin_seconds),
            "models": [str(m) for m in (self.raw.get("models") or [])],
            "tpmLimit": self.raw.get("tpm_limit"),
            "rpmLimit": self.raw.get("rpm_limit"),
        }


@dataclass
class ModelEntry:
    """Um modelo cadastrado no proxy (`LiteLLM_ProxyModelTable`)."""

    raw: Dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.raw.get("model_name") or "(sem nome)")

    @property
    def params(self) -> Dict[str, Any]:
        valor = self.raw.get("litellm_params")
        return valor if isinstance(valor, dict) else {}

    @property
    def model_id(self) -> str:
        """Identificador do deployment (`model_info.id`).

        É a única coisa que amarra este cadastro ao veredito do `/health`: lá o
        modelo é identificado por `model_id`, e não por `model_name` — dois
        deployments podem compartilhar o mesmo nome de modelo, e o LiteLLM trata
        isso como recurso, não como erro.
        """
        info = self.raw.get("model_info")
        if isinstance(info, dict) and info.get("id"):
            return str(info["id"])
        return ""

    @property
    def provider(self) -> str:
        modelo = str(self.params.get("model") or "")
        return modelo.split("/", 1)[0] if "/" in modelo else modelo

    @property
    def api_base(self) -> Optional[str]:
        valor = self.params.get("api_base")
        return str(valor) if valor else None

    @property
    def api_key(self) -> str:
        """Chave declarada no modelo.

        Pode vir como referência de ambiente (`os.environ/NOME`); nesse caso não
        há segredo aqui e não há o que validar a partir do cadastro.
        """
        valor = self.params.get("api_key")
        return str(valor) if valor else ""

    @property
    def key_is_env_reference(self) -> bool:
        return self.api_key.startswith("os.environ/")

    @property
    def uses_named_credential(self) -> bool:
        return bool(self.params.get("litellm_credential_name"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "apiBase": self.api_base,
            # Booleanos, nunca o valor. Mesma regra dos irmãos.
            "hasApiKey": bool(self.api_key),
            "keyIsEnvReference": self.key_is_env_reference,
            "usesNamedCredential": self.uses_named_credential,
        }


def summarize(keys: List[VirtualKey], margin_seconds: int = 900) -> Dict[str, int]:
    """Contagem por estado, para o cabeçalho do painel."""
    resumo = {
        SAUDE_ATIVA: 0,
        SAUDE_EXPIRANDO: 0,
        SAUDE_EXPIRADA: 0,
        SAUDE_BLOQUEADA: 0,
        SAUDE_ESTOURADA: 0,
    }
    for chave in keys:
        estado = chave.health_status(margin_seconds)
        resumo[estado] = resumo.get(estado, 0) + 1
    return resumo
