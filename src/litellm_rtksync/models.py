"""Registros que o painel mostra: conexões, chaves virtuais e modelos.

Cada gateway devolve o seu JSON com uma forma própria -- um aninha o endereço do
provedor em ``providerSpecificData``, outro devolve colunas relacionais, um
terceiro não guarda conexão nenhuma e só devolve MODELOS, cada um declarando
para onde vai. Este arquivo é a lente comum: as propriedades DERIVADAS
(``is_oauth``, ``remaining_seconds``, ``health_status``, ``provider``) respondem
à mesma pergunta com a mesma regra nos três produtos, para que a tela não mude
de opinião conforme o gateway por trás dela.

Ler formato é tolerância: quando dois gateways gravam a mesma coisa com nomes
diferentes, os dois nomes são aceitos e o campo ausente simplesmente não
responde. Decidir o que aquilo SIGNIFICA é regra de negócio, e regra de negócio
é uma só.

Nenhuma projeção carrega credencial: ``to_dict`` diz se existe chave, nunca qual
é, e a chave virtual se identifica pelo apelido, pelo nome ou pelo id -- nunca
pelo prefixo do token, que é um pedaço do segredo.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Margem em que uma credencial já conta como "expirando" na tela. Vale para a
# conexão e para a chave virtual, para que o mesmo prazo pinte o mesmo amarelo
# nos dois cartões.
EXPIRING_SOON_SECONDS = 900

# Vocabulário de saúde. É o mesmo texto que o render usa como chave do badge e
# da tradução: um estado que não esteja no mapa dele aparece como desconhecido,
# então inventar nome aqui apaga a informação na tela.
HEALTH_ACTIVE = "active"
HEALTH_EXPIRING_SOON = "expiring_soon"
HEALTH_EXPIRED = "expired"
HEALTH_NO_EXPIRATION = "no_expiration"
HEALTH_BLOCKED = "blocked"
HEALTH_OVER_BUDGET = "over_budget"
HEALTH_RATE_LIMITED = "rate_limited"
HEALTH_INVALID = "invalid"
HEALTH_UNREACHABLE = "unreachable"
HEALTH_NOT_CHECKED = "not_checked"
HEALTH_UNKNOWN = "unknown"

# Nomes de provedor que sugerem uma instância local ou compatível com a API da
# OpenAI. O marcador sozinho não prova nada: "ollama" também é o nome do serviço
# hospedado, que jamais pode ser sondado em /api/tags.
LOCAL_PROVIDER_MARKERS = ("ollama", "vllm", "lmstudio", "llamacpp", "localai", "openai-compatible")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal", ".local")


def parse_instant(value: Any) -> Optional[datetime]:
    """Lê um instante em ISO-8601 ou em epoch, tolerando as duas formas.

    Um epoch numérico gravado como TEXTO é a origem da classe de defeito que deu
    origem a esta família de projetos: quem só entende ISO devolve nada, e a tela
    passa a dizer "sem validade" para uma credencial que tem prazo e está
    vencendo. Valor que não dá para ler vira ``None`` -- nunca uma exceção, que
    derrubaria a página inteira por causa de um campo torto.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds <= 0:
            return None
        # Heurística de segundos contra milissegundos: 1e11 segundos é o ano
        # 5138, então qualquer coisa acima disso só pode estar em milissegundos.
        if seconds >= 1e11:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return parse_instant(float(text))
    except ValueError:
        pass
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Carimbo sem fuso é lido como UTC, que é o fuso em que os gateways gravam.
    # Assumir o fuso da máquina faria o mesmo dado significar horas diferentes
    # em dois servidores.
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def to_epoch_ms(value: Any) -> Optional[int]:
    """O mesmo instante em epoch de milissegundos, que é como o painel compara."""
    moment = parse_instant(value)
    return int(moment.timestamp() * 1000) if moment is not None else None


@dataclass
class ConnectionRecord:
    """Uma conexão com o provedor, vista pela lente do painel.

    A origem muda conforme o gateway: uma linha de ``providerConnections`` com o
    JSON inteiro numa coluna, uma linha relacional já resolvida em dicionário, ou
    o agrupamento dos modelos por destino quando o gateway não guarda conexão
    nenhuma (veja ``group_connections``). O que não muda é o que a tela pergunta.
    """

    id: str = ""
    provider: str = ""
    name: str = ""
    created_at: str = ""
    updated_at: str = ""
    data_raw: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Gateway que guarda o payload como texto entrega aqui o JSON cru. JSON
        # torto não pode derrubar a leitura de todas as outras conexões.
        if self.data_raw and not self.data:
            try:
                self.data = json.loads(self.data_raw)
            except Exception:
                self.data = {}

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "ConnectionRecord":
        """Monta o registro a partir do dicionário que a leitura do gateway devolve."""
        return cls(
            id=str(row.get("id", "")),
            provider=str(row.get("provider", "")),
            name=str(row.get("name") or row.get("provider") or ""),
            data=dict(row),
        )

    @property
    def is_oauth(self) -> bool:
        """Se a conexão se autentica por fluxo de token OAuth."""
        return bool(self.data.get("refreshToken") or self.data.get("accessToken"))

    @property
    def has_api_key(self) -> bool:
        """Se a conexão se autentica por chave de API estática."""
        return bool(self.data.get("apiKey"))

    @property
    def api_key(self) -> Optional[str]:
        return self.data.get("apiKey")

    @property
    def access_token(self) -> Optional[str]:
        return self.data.get("accessToken")

    @property
    def refresh_token(self) -> Optional[str]:
        return self.data.get("refreshToken")

    @property
    def base_url(self) -> Optional[str]:
        """Endereço do provedor, onde quer que o gateway o tenha guardado.

        Um dos gateways aninha em ``providerSpecificData``; lendo só a raiz,
        instância local nenhuma exibia os seus modelos.
        """
        specific = self.data.get("providerSpecificData")
        if isinstance(specific, dict):
            nested = specific.get("baseUrl") or specific.get("baseURL")
            if nested:
                return nested
        return (
            self.data.get("baseUrl")
            or self.data.get("baseURL")
            or self.data.get("base_url")
            or None
        )

    @property
    def api_base(self) -> Optional[str]:
        """O mesmo endereço com o nome que o cadastro de modelos usa."""
        return self.base_url

    @property
    def is_local(self) -> bool:
        """Se a conexão realmente aponta para uma instância nesta máquina.

        Quem classifica é o endereço, não o nome do provedor. Só quando nenhum
        endereço foi declarado é que um marcador como "openai-compatible" -- que
        não tem contraparte hospedada -- vale sozinho.
        """
        base_url = str(self.base_url or "").lower()
        if base_url:
            return any(host in base_url for host in LOCAL_HOSTS)
        # Sem endereço: "openai-compatible" só existe auto-hospedado, enquanto
        # "ollama" sem baseUrl é a conta na nuvem.
        return "openai-compatible" in self.provider.lower()

    @property
    def local_models(self) -> List[str]:
        """Modelos descobertos na instância local na última varredura."""
        models = self.data.get("discoveredModels") or self.data.get("models") or []
        if isinstance(models, str):
            return [models]
        return [str(m) for m in models if m]

    @property
    def credential_name(self) -> Optional[str]:
        """Nome que o operador deu à credencial no gateway -- nunca o valor dela."""
        name = self.data.get("credentialName")
        return str(name) if name else None

    @property
    def models(self) -> List["RegisteredModelRecord"]:
        """Modelos servidos por este destino, quando a conexão veio do agrupamento."""
        return list(self.data.get("registeredModels") or [])

    @property
    def model_names(self) -> List[str]:
        return [model.name for model in self.models]

    @property
    def identity(self) -> str:
        """Chave de agrupamento do destino: provedor mais endereço."""
        return f"{self.provider}|{self.api_base or ''}"

    @property
    def egress_status(self) -> str:
        """Como esta conta sai para a internet: ``bound``, ``shared`` ou ``unknown``.

        Somente leitura: quem manda no vínculo é o gateway. Um guarda os
        interruptores em colunas da própria conexão (``proxyEnabled``,
        ``perKeyProxyEnabled``) com o vínculo já resolvido em ``egressProxy``;
        outro guarda tudo em ``providerSpecificData``. É exibido porque uma conta
        que compartilha o mesmo endereço de saída com todas as outras é
        justamente o estado que o operador precisa perceber antes do provedor.
        """
        if self.data.get("proxyEnabled") is True or self.data.get("perKeyProxyEnabled") is True:
            return "bound" if self.data.get("egressProxy") else "shared"
        specific = self.data.get("providerSpecificData")
        if isinstance(specific, dict):
            if specific.get("connectionProxyEnabled") is True and specific.get("proxyPoolId"):
                return "bound"
            return "shared"
        return "shared" if "proxyEnabled" in self.data else "unknown"

    @property
    def egress_binding(self) -> Optional[str]:
        """Nome ou identificador da saída vinculada, quando existe uma."""
        binding = self.data.get("egressProxy")
        if binding:
            return str(binding)
        specific = self.data.get("providerSpecificData")
        if not isinstance(specific, dict):
            return None
        if specific.get("connectionProxyEnabled") is not True:
            return None
        pool = specific.get("proxyPoolId")
        return str(pool) if pool else None

    @property
    def expires_at_ms(self) -> Optional[int]:
        """Expiração normalizada em epoch de milissegundos, venha ela como for."""
        return to_epoch_ms(self.data.get("expiresAt"))

    @property
    def remaining_seconds(self) -> Optional[int]:
        """Segundos que faltam para a credencial expirar."""
        expires = self.expires_at_ms
        if expires is None:
            return None
        return int((expires - int(time.time() * 1000)) / 1000)

    @property
    def is_expired(self) -> bool:
        """Se a credencial já venceu."""
        remaining = self.remaining_seconds
        return remaining is not None and remaining <= 0

    @property
    def last_refresh_at(self) -> Optional[str]:
        """Quando a credencial foi renovada ou verificada pela última vez.

        ``lastRefreshAt`` é gravado na renovação de OAuth; ``lastTested``, na
        validação da credencial. Sem expor isto, o painel diz "0 renovadas" e não
        há como saber se a última renovação foi há um minuto ou há uma semana.
        """
        return (
            self.data.get("lastRefreshAt")
            or self.data.get("credentialCheckedAt")
            or self.data.get("lastTested")
            or None
        )

    @property
    def credential_state(self) -> Optional[str]:
        """Resultado da última validação viva da credencial, quando houve uma.

        Quem escreve este campo é a sondagem deste painel, nunca o gateway.
        """
        state = self.data.get("credentialState")
        return str(state) if state else None

    @property
    def rate_limit_active(self) -> bool:
        """Se a trava de rate limit ainda vale neste instante.

        ``rateLimitedUntil`` é um PRAZO, não uma bandeira: guarda o momento em
        que a janela do provedor se reabre. Tratar a simples presença do campo
        como "limitada" deixava a conexão amarela para sempre depois do primeiro
        429, porque nada apaga a marca quando o prazo vence.
        """
        until = to_epoch_ms(self.data.get("rateLimitedUntil"))
        if until is None:
            return False
        return until > int(time.time() * 1000)

    @property
    def health_status(self) -> str:
        """Classificação semântica do estado da conexão.

        Uma validação viva vence tudo: chave que o provedor recusa está quebrada,
        não importa o que o gateway tenha carimbado por último. As instâncias
        locais são classificadas antes do ramo de chave de API porque carregam
        uma chave de fachada e nunca chegariam ao teste que é delas.
        """
        probed = self.credential_state
        if probed in (HEALTH_INVALID, HEALTH_RATE_LIMITED, HEALTH_UNREACHABLE):
            return probed

        if self.is_local:
            # "unreachable" é escrito quando o catálogo de modelos não responde.
            stamped = self.data.get("testStatus")
            if stamped == "unreachable":
                return HEALTH_UNKNOWN
            # Conexão recém-criada nunca foi sondada, e com o agendador desligado
            # pode nunca ser: dizer "ativa" é alegar uma saúde que ninguém viu.
            return HEALTH_ACTIVE if stamped in ("active", "ok", "success") else HEALTH_NOT_CHECKED

        if self.is_oauth:
            # O gateway já pode ter carimbado a conexão como recusada. **Sem uma
            # sonda viva que diga o contrário**, o carimbo dele é a melhor
            # informação que existe -- ignorá-lo mostrava como saudável uma
            # credencial que o próprio gateway sabe estar quebrada. Mas a
            # validação viva vence: o carimbo é do último erro e não caduca
            # sozinho, então honrá-lo depois de a sonda aprovar a credencial
            # repetiria, ao contrário, a contradição entre tela e banco que este
            # arquivo existe para evitar.
            if probed != "valid" and self.data.get("testStatus") in ("invalid", "error", "failed"):
                return HEALTH_INVALID
            remaining = self.remaining_seconds
            if remaining is None:
                return HEALTH_NO_EXPIRATION
            if remaining <= 0:
                return HEALTH_EXPIRED
            if remaining < EXPIRING_SOON_SECONDS:
                return HEALTH_EXPIRING_SOON
            return HEALTH_ACTIVE

        if self.has_api_key:
            if self.rate_limit_active:
                return HEALTH_RATE_LIMITED
            # Nunca sondada: dizer isso, em vez de alegar saúde que ninguém viu.
            return HEALTH_ACTIVE if probed == "valid" else HEALTH_NOT_CHECKED

        # Um gateway escreve "ok", outro escreve "active"; os dois querem dizer
        # a mesma coisa.
        stamped = self.data.get("testStatus")
        return HEALTH_ACTIVE if stamped in ("active", "ok", "success") else HEALTH_UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        """Projeção explícita do destino. Nenhuma credencial entra aqui -- só o nome dela."""
        return {
            "provider": self.provider,
            "apiBase": self.api_base,
            "credentialName": self.credential_name,
            "models": self.model_names,
        }


@dataclass
class VirtualKeyRecord:
    """A chave virtual que o cliente apresenta ao gateway, vista pelo painel.

    Ela NÃO se renova: nasce com prazo (ou sem nenhum) e vence, ou vale até
    alguém desativá-la. Por isso a coluna "última renovação" da tabela carrega
    aqui a data de EMISSÃO -- é o único carimbo de tempo que a chave tem, e a
    coluna existe para casar com a dos irmãos.

    O material do token não chega a este objeto: a leitura do gateway sequer
    carrega a coluna onde ele mora.
    """

    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "VirtualKeyRecord":
        return cls(data=dict(row))

    @property
    def id(self) -> str:
        return str(self.data.get("id") or "")

    @property
    def name(self) -> str:
        """Como a chave se identifica na tela: o nome dado a ela, senão o id.

        O id é um UUID -- identifica sem revelar nada. O prefixo do token seria
        mais reconhecível e está fora de questão: é um pedaço do segredo.
        """
        return str(self.data.get("name") or self.id)

    @property
    def alias(self) -> str:
        """O apelido que o gateway guarda, quando o cadastro tem um.

        Sem apelido e sem nome, o que sobra é o FIM do token -- os últimos
        caracteres identificam a linha para quem a emitiu e não reconstroem o
        segredo. O começo, que é o que serve para autenticar, nunca aparece.
        """
        for campo in ("key_alias", "key_name"):
            if self.data.get(campo):
                return str(self.data[campo])
        token = str(self.data.get("token") or "")
        return f"…{token[-6:]}" if token else "(sem apelido)"

    @property
    def team_id(self) -> Optional[str]:
        value = self.data.get("team_id")
        return str(value) if value else None

    @property
    def created_at(self) -> Optional[str]:
        """Quando a chave foi emitida -- o único carimbo de tempo que ela tem."""
        return (
            self.data.get("createdAt")
            or self.data.get("created_at")
            or self.data.get("created_by_at")
            or None
        )

    @property
    def issued_at(self) -> Optional[str]:
        """O mesmo instante de emissão, com o nome que a tabela usa."""
        return self.created_at

    @property
    def last_used_at(self) -> Optional[str]:
        return self.data.get("lastUsedAt")

    @property
    def machine_id(self) -> str:
        """A máquina a que o gateway amarrou esta chave.

        Não é credencial: é uma impressão digital da instalação, que o próprio
        gateway devolve ao emitir a chave. Aparece no modal porque é ela que
        explica por que uma chave copiada para outra máquina deixa de funcionar.
        """
        return str(self.data.get("machineId") or "")

    @property
    def revoked(self) -> bool:
        """Se o gateway já recusa esta chave, por qualquer um dos caminhos.

        Revogada, banida e desativada são caminhos diferentes para o mesmo fato
        observável: a chave não é mais aceita. Há gateway que tem os três campos
        e há gateway que só tem a bandeira de ativa; o que não existe simplesmente
        não responde.
        """
        return bool(
            self.data.get("revokedAt")
            or self.data.get("isBanned")
            or self.data.get("isActive") is False
        )

    @property
    def blocked(self) -> bool:
        """Se a chave está bloqueada pelo gateway sem ter sido revogada."""
        return bool(self.data.get("blocked"))

    @property
    def spend(self) -> float:
        """Quanto já foi gasto por esta chave, quando o gateway contabiliza."""
        try:
            return float(self.data.get("spend") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def max_budget(self) -> Optional[float]:
        """Teto de gasto declarado. Zero é ausência de teto, não teto zerado."""
        try:
            limit = float(self.data.get("max_budget"))
        except (TypeError, ValueError):
            return None
        return limit if limit > 0 else None

    @property
    def budget_exhausted(self) -> bool:
        limit = self.max_budget
        return limit is not None and self.spend >= limit

    @property
    def scopes(self) -> List[str]:
        return [str(s) for s in (self.data.get("scopes") or [])]

    @property
    def allowed_models(self) -> List[str]:
        return [str(m) for m in (self.data.get("allowedModels") or [])]

    @property
    def model_access_mode(self) -> str:
        return str(self.data.get("modelAccessMode") or "all")

    @property
    def expires_at_ms(self) -> Optional[int]:
        """Prazo da chave em epoch de milissegundos, quando o gateway declara um."""
        return to_epoch_ms(self.data.get("expiresAt") or self.data.get("expires"))

    @property
    def remaining_seconds(self) -> Optional[int]:
        """Segundos até o vencimento, ou ``None`` quando não há prazo declarado."""
        expires = self.expires_at_ms
        if expires is None:
            return None
        return int((expires - int(time.time() * 1000)) / 1000)

    def health(self, margin_seconds: int = EXPIRING_SOON_SECONDS) -> str:
        """Estado da chave, no mesmo vocabulário que as conexões usam.

        A ordem importa: recusa e bloqueio são fatos consumados; prazo vem
        depois; orçamento estourado vem por último, porque uma chave vencida e
        estourada é, antes de tudo, uma chave vencida.

        O último caso -- chave sem prazo nenhum declarado -- é o que os irmãos
        ainda contam de formas diferentes, e é a última divergência deste
        arquivo: um diz "sem expiração" e o outro diz "ativa". Validade não
        declarada não é validade infinita, e enquanto a tela de um deles não
        souber desenhar o estado do outro, trocar a palavra aqui apagaria a
        informação em vez de unificá-la.
        """
        if self.revoked:
            return HEALTH_INVALID
        if self.blocked:
            return HEALTH_BLOCKED
        remaining = self.remaining_seconds
        if remaining is not None:
            if remaining <= 0:
                return HEALTH_EXPIRED
            if remaining < margin_seconds:
                return HEALTH_EXPIRING_SOON
        if self.budget_exhausted:
            return HEALTH_OVER_BUDGET
        return HEALTH_ACTIVE

    def health_status(self, margin_seconds: int = EXPIRING_SOON_SECONDS) -> str:
        """O estado da chave, na margem que o painel usa para avisar."""
        return self.health(margin_seconds)

    def to_dict(self, margin_seconds: int = EXPIRING_SOON_SECONDS) -> Dict[str, Any]:
        """Projeção explícita da chave. O token nunca sai daqui."""
        expires = self.expires_at_ms
        return {
            "alias": self.alias,
            "teamId": self.team_id,
            "expiresAt": (
                datetime.fromtimestamp(expires / 1000, tz=timezone.utc).isoformat()
                if expires is not None
                else None
            ),
            "remainingSeconds": self.remaining_seconds,
            "blocked": self.blocked,
            "spend": self.spend,
            "maxBudget": self.max_budget,
            "healthStatus": self.health(margin_seconds),
            "models": [str(m) for m in (self.data.get("models") or [])],
            "tpmLimit": self.data.get("tpm_limit"),
            "rpmLimit": self.data.get("rpm_limit"),
        }


@dataclass
class RegisteredModelRecord:
    """Um modelo do catálogo do gateway, com a conexão que o serve.

    Modelo não tem saúde própria nem validade própria: ele responde enquanto a
    credencial da conexão que o publica for aceita. Por isso status, validade
    restante e última renovação são HERDADOS da conexão dona -- e o modal diz de
    qual conexão vieram, para que ninguém leia a linha como um veredito sobre o
    modelo em si.
    """

    data: Dict[str, Any] = field(default_factory=dict)
    connection: Optional[ConnectionRecord] = None

    @classmethod
    def from_row(
        cls, row: Dict[str, Any], connection: Optional[ConnectionRecord] = None
    ) -> "RegisteredModelRecord":
        return cls(data=dict(row), connection=connection)

    @classmethod
    def from_entry(
        cls, entry: Dict[str, Any], connection: Optional[ConnectionRecord] = None
    ) -> "RegisteredModelRecord":
        return cls(data=dict(entry), connection=connection)

    @property
    def id(self) -> str:
        """Identificador do cadastro.

        Onde há deployment, é o id DELE que identifica a linha: dois cadastros
        podem publicar o mesmo nome de modelo, e o gateway trata isso como
        recurso, não como erro.
        """
        info = self.data.get("model_info")
        if isinstance(info, dict) and info.get("id"):
            return str(info["id"])
        return str(self.data.get("id") or "")

    @property
    def model_id(self) -> str:
        """O mesmo id, com o nome pelo qual o veredito de saúde o procura."""
        return self.id

    @property
    def name(self) -> str:
        """Como o cadastro se identifica na tela.

        O travessão no fim não é decoração: `id` cai para string vazia quando o
        gateway não devolve nem `model_info.id` nem `id`, e sem ele a célula do
        grid ficaria em branco -- o operador leria uma linha sem saber a que
        cadastro ela se refere. Travessão é o mesmo sinal que o resto do painel
        usa para "não declarado".
        """
        return str(self.data.get("name") or self.data.get("model_name")
                   or self.id or "—")

    @property
    def params(self) -> Dict[str, Any]:
        """Parâmetros do cadastro, quando o gateway os devolve em bloco."""
        value = self.data.get("litellm_params")
        return value if isinstance(value, dict) else {}

    @property
    def provider(self) -> str:
        """Quem serve o modelo: o campo declarado, ou o prefixo do nome técnico."""
        declared = self.data.get("provider")
        if declared:
            return str(declared)
        model = str(self.params.get("model") or "")
        return model.split("/", 1)[0] if "/" in model else model

    @property
    def source(self) -> str:
        """De onde o gateway tirou esta entrada do catálogo."""
        return str(self.data.get("source") or "")

    @property
    def description(self) -> str:
        return str(self.data.get("description") or "")

    @property
    def input_token_limit(self) -> Optional[int]:
        return self.data.get("inputTokenLimit")

    @property
    def output_token_limit(self) -> Optional[int]:
        return self.data.get("outputTokenLimit")

    @property
    def supported_endpoints(self) -> List[str]:
        return [str(e) for e in (self.data.get("supportedEndpoints") or [])]

    @property
    def api_base(self) -> Optional[str]:
        """Endereço para onde este cadastro manda a requisição."""
        value = self.params.get("api_base")
        return str(value) if value else None

    @property
    def api_key(self) -> str:
        """Chave declarada no cadastro do modelo.

        Pode vir como referência de ambiente; nesse caso não há segredo aqui e
        não há o que validar a partir do cadastro. O valor nunca é projetado.
        """
        value = self.params.get("api_key")
        return str(value) if value else ""

    @property
    def key_is_env_reference(self) -> bool:
        return self.api_key.startswith("os.environ/")

    @property
    def uses_named_credential(self) -> bool:
        return bool(self.params.get("litellm_credential_name"))

    @property
    def connection_name(self) -> Optional[str]:
        return self.connection.name if self.connection else None

    @property
    def health_status(self) -> str:
        # Sem conexão dona identificada (catálogo estático do gateway, ou órfão
        # de uma conexão removida) não há o que afirmar: dizer "ativo" seria
        # inventar uma sondagem que nunca houve.
        return self.connection.health_status if self.connection else HEALTH_NOT_CHECKED

    @property
    def remaining_seconds(self) -> Optional[int]:
        return self.connection.remaining_seconds if self.connection else None

    @property
    def last_refresh_at(self) -> Optional[str]:
        return self.connection.last_refresh_at if self.connection else None

    def to_dict(self) -> Dict[str, Any]:
        """Projeção explícita do modelo. Booleanos sobre a chave, nunca o valor."""
        return {
            "name": self.name,
            "provider": self.provider,
            "apiBase": self.api_base,
            "hasApiKey": bool(self.api_key),
            "keyIsEnvReference": self.key_is_env_reference,
            "usesNamedCredential": self.uses_named_credential,
        }


def group_connections(models: List[RegisteredModelRecord]) -> List[ConnectionRecord]:
    """Agrupa os modelos cadastrados nos destinos que eles realmente usam.

    Há gateway que guarda a lista de conexões e há gateway que não guarda: ele
    guarda MODELOS, e cada modelo declara para onde vai e com que credencial. Sem
    a lista, a conexão é o que sobra ao agrupar os modelos por destino -- cada par
    (provedor, endereço) é um endpoint de verdade, com uma credencial e um
    conjunto de modelos servidos por ela. Foi esta leitura, e não "o próprio
    gateway é a única conexão", porque a segunda diz sempre a mesma coisa (uma
    linha, sempre saudável) e não ajuda ninguém a descobrir qual provedor parou.

    A ordem de saída é a da primeira aparição de cada destino, para que a tabela
    não mude de ordem entre dois carregamentos sem nada ter mudado no gateway.
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    for model in models:
        key = f"{model.provider}|{model.api_base or ''}"
        bucket = grouped.get(key)
        if bucket is None:
            bucket = {"provider": model.provider, "api_base": model.api_base,
                      "credential_name": None, "models": []}
            grouped[key] = bucket
        # Modelos do mesmo destino podem declarar credenciais diferentes; o
        # primeiro nome encontrado vale como rótulo, e o modal mostra os modelos
        # para quem precisar conferir caso a caso.
        named = model.params.get("litellm_credential_name")
        if bucket["credential_name"] is None and named:
            bucket["credential_name"] = str(named)
        bucket["models"].append(model)

    # O nome da conexão é o rótulo que o operador reconhece: o nome da credencial
    # quando há um; senão o endereço do destino, que é a única identificação
    # honesta; senão o provedor.
    return [
        ConnectionRecord(
            id=key,
            provider=bucket["provider"],
            name=(bucket["credential_name"] or bucket["api_base"]
                  or bucket["provider"] or "(sem destino)"),
            data={
                "baseUrl": bucket["api_base"],
                "credentialName": bucket["credential_name"],
                "registeredModels": bucket["models"],
            },
        )
        for key, bucket in grouped.items()
    ]


def summarize(keys: List[VirtualKeyRecord],
              margin_seconds: int = EXPIRING_SOON_SECONDS) -> Dict[str, int]:
    """Contagem por estado, para o cabeçalho do painel."""
    summary = {
        HEALTH_ACTIVE: 0,
        HEALTH_EXPIRING_SOON: 0,
        HEALTH_EXPIRED: 0,
        HEALTH_BLOCKED: 0,
        HEALTH_OVER_BUDGET: 0,
    }
    for key in keys:
        state = key.health(margin_seconds)
        summary[state] = summary.get(state, 0) + 1
    return summary
