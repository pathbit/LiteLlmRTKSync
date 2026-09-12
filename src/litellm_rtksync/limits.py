"""Coerência da hierarquia de limites do LiteLLM.

O LiteLLM aceita limite em três níveis — chave virtual, time e o padrão da
plataforma — e **não recusa** um limite de chave maior que o do time que a
contém. O resultado é um limite que existe no cadastro e não vale na prática:
quem impõe é sempre o teto mais restritivo encontrado no caminho da requisição.
Um time com `rpm_limit=60` e uma chave com `rpm_limit=600` não entrega 600; o
operador acha que configurou 600 e recebe 60, sem nada dizer o contrário.

Este módulo compara os níveis e nomeia cada incoerência. Ele não corrige nada:
mexer no limite de alguém é decisão do operador, não de um processo de fundo.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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
    # O LiteLLM aceita 0 como "sem limite" em alguns campos; tratar 0 como teto
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
