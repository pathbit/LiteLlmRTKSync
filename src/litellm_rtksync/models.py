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


@dataclass
class UpstreamConnection:
    """Um destino real atrás dos modelos cadastrados.

    Os painéis irmãos listam "conexões monitoradas": as identidades que o
    gateway usa para falar com o provedor. O LiteLLM não guarda essa lista em
    lugar nenhum — ele guarda MODELOS, e cada modelo declara para onde vai
    (`api_base`) e com que credencial (`litellm_credential_name`, `api_key`).

    A conexão, aqui, é o que sobra quando se agrupa os modelos por destino:
    cada par (provedor, api_base) é um endpoint de verdade, com uma credencial
    e um conjunto de modelos servidos por ela. Foi a leitura escolhida em vez
    de "o próprio proxy é a única conexão" porque essa segunda diz sempre a
    mesma coisa — uma linha, sempre saudável — e não ajuda ninguém a descobrir
    qual provedor parou de responder.

    Pares de rota do LiteLLM que sustentam isso: `/model/info` devolve
    `litellm_params` com `api_base` e `litellm_credential_name`; `/credentials`
    lista as credenciais nomeadas com o valor já mascarado pelo gateway. Nada
    aqui carrega segredo: só o NOME da credencial e o endereço do destino.
    """

    provider: str
    api_base: Optional[str]
    credential_name: Optional[str]
    models: List[ModelEntry]

    @property
    def identity(self) -> str:
        """Chave de agrupamento, e também o id do modal quando normalizada."""
        return f"{self.provider}|{self.api_base or ''}"

    @property
    def name(self) -> str:
        """Como a conexão se chama na tela.

        O nome da credencial é o rótulo que o operador escolheu e o que ele
        reconhece; sem credencial nomeada, o endereço do destino é a única
        identificação honesta; sem endereço, sobra o provedor.
        """
        if self.credential_name:
            return self.credential_name
        if self.api_base:
            return self.api_base
        return self.provider or "(sem destino)"

    @property
    def model_names(self) -> List[str]:
        return [m.name for m in self.models]

    def to_dict(self) -> Dict[str, Any]:
        """Projeção explícita. Nenhuma chave de API entra aqui — só o nome dela."""
        return {
            "provider": self.provider,
            "apiBase": self.api_base,
            "credentialName": self.credential_name,
            "models": self.model_names,
        }


def group_connections(models: List[ModelEntry]) -> List[UpstreamConnection]:
    """Agrupa os modelos cadastrados nos destinos que eles realmente usam.

    A ordem de saída é a da primeira aparição de cada destino, para que a tabela
    não mude de ordem entre dois carregamentos sem nada ter mudado no gateway.
    """
    agrupadas: Dict[str, UpstreamConnection] = {}
    for modelo in models:
        chave = f"{modelo.provider}|{modelo.api_base or ''}"
        conexao = agrupadas.get(chave)
        if conexao is None:
            conexao = UpstreamConnection(
                provider=modelo.provider,
                api_base=modelo.api_base,
                credential_name=(str(modelo.params.get("litellm_credential_name"))
                                 if modelo.params.get("litellm_credential_name") else None),
                models=[],
            )
            agrupadas[chave] = conexao
        # Modelos do mesmo destino podem declarar credenciais diferentes; o
        # primeiro nome encontrado vale como rótulo, e o modal mostra os modelos
        # para quem precisar conferir caso a caso.
        if conexao.credential_name is None and modelo.params.get("litellm_credential_name"):
            conexao.credential_name = str(modelo.params["litellm_credential_name"])
        conexao.models.append(modelo)
    return list(agrupadas.values())


# Os três tipos de fallback do roteador do LiteLLM, na ordem em que a tela os
# mostra. O valor é a CHAVE de tradução do rótulo; `general` não tem rótulo
# porque é o caso comum e nomear o óbvio só ocupa a linha.
TIPOS_DE_FALLBACK = (
    ("fallbacks", "general", ""),
    ("context_window_fallbacks", "context_window", "combos.kind_context_window"),
    ("content_policy_fallbacks", "content_policy", "combos.kind_content_policy"),
)


def fallback_combos(router_settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Combos de resiliência do LiteLLM, que aqui se chamam FALLBACKS.

    O conceito existe e é exatamente o mesmo dos irmãos: um modelo principal e
    a cascata que assume quando ele falha. O que muda é o nome e o lugar — no
    LiteLLM isso mora em `router_settings`, e chega por `GET /router/settings`
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
