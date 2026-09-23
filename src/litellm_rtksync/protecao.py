"""Freio contra força bruta e varredura automatizada, sem depender de ninguém.

Um painel preso ao loopback não precisa disso. Um painel atrás de um túnel
precisa, e o túnel é um botão que o operador aperta quando quiser — então o
freio tem de já estar aqui quando ele apertar.

Três camadas, da mais barata para a mais cara:

1. **Teto por janela.** Mais de `TENTATIVAS_POR_JANELA` tentativas de login no
   mesmo endereço dentro de `JANELA_EM_SEGUNDOS` devolve **429** com
   `Retry-After`. É o que para o script que tenta mil senhas por minuto.

2. **Espera que cresce.** Cada falha seguida atrasa a resposta seguinte, dobrando
   até um teto. Um humano que errou a senha espera meio segundo; um robô que erra
   sempre passa a esperar mais. O atraso é do lado do servidor: não há nada no
   cliente para desligar.

3. **Desafio interativo direto.** Depois de `FALHAS_ATE_DESAFIO` falhas, o formulário
   só é aceito com a seleção do item solicitado entre opções visuais. Instantâneo
   para humanos (1 clique, zero travamento de CPU ou spinner), e barra scripts e
   robôs que tentam ataques automatizados em massa.

O estado vive em memória, por processo. Reiniciar zera os contadores, o que é
aceitável: reiniciar é justamente o que um atacante não consegue fazer.
"""

import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

JANELA_EM_SEGUNDOS = 300
TENTATIVAS_POR_JANELA = 10

FALHAS_ATE_DESAFIO = 3
ESPERA_INICIAL_EM_SEGUNDOS = 0.5
ESPERA_MAXIMA_EM_SEGUNDOS = 5.0

# Quantidade padrão e máxima de opções exibidas no desafio interativo.
DIFICULDADE = 4
DIFICULDADE_MAXIMA = 6

# Catálogo de itens do desafio interativo (identificador, ícone do Bootstrap Icons)
ITENS_DESAFIO: Tuple[Tuple[str, str], ...] = (
    ("key", "bi-key-fill"),
    ("shield", "bi-shield-fill"),
    ("lock", "bi-lock-fill"),
    ("star", "bi-star-fill"),
    ("heart", "bi-heart-fill"),
    ("bell", "bi-bell-fill"),
    ("lightning", "bi-lightning-fill"),
    ("gear", "bi-gear-fill"),
    ("sun", "bi-sun-fill"),
    ("moon", "bi-moon-stars-fill"),
    ("cloud", "bi-cloud-fill"),
    ("fire", "bi-fire"),
    ("droplet", "bi-droplet-fill"),
    ("tree", "bi-tree-fill"),
    ("flower", "bi-flower1"),
    ("gem", "bi-gem"),
    ("trophy", "bi-trophy-fill"),
    ("award", "bi-award-fill"),
    ("gift", "bi-gift-fill"),
    ("compass", "bi-compass-fill"),
    ("flag", "bi-flag-fill"),
    ("globe", "bi-globe"),
    ("camera", "bi-camera-fill"),
    ("cup", "bi-cup-hot-fill"),
    ("palette", "bi-palette-fill"),
    ("umbrella", "bi-umbrella-fill"),
    ("rocket", "bi-rocket-takeoff-fill"),
    ("airplane", "bi-airplane-fill"),
    ("bicycle", "bi-bicycle"),
    ("truck", "bi-truck"),
    ("cpu", "bi-cpu-fill"),
    ("terminal", "bi-terminal-fill"),
    ("laptop", "bi-laptop"),
    ("phone", "bi-phone-fill"),
    ("watch", "bi-watch"),
    ("wifi", "bi-wifi"),
)
MAPA_ICONES: Dict[str, str] = dict(ITENS_DESAFIO)


def icone_do_item(item: str) -> str:
    """Ícone Bootstrap correspondente ao item do desafio."""
    return MAPA_ICONES.get(item, "bi-question-circle")


def dificuldade_para(endereco: str) -> int:
    """Quantas opções exigir deste endereço, dado o histórico dele."""
    with _trava:
        falhas = _falhas.get(endereco, 0)
    extra = max(0, (falhas - FALHAS_ATE_DESAFIO) // 3)
    return min(DIFICULDADE + extra, DIFICULDADE_MAXIMA)


_trava = threading.Lock()
_tentativas: Dict[str, List[float]] = {}
_falhas: Dict[str, int] = {}
_desafios: Dict[str, Any] = {}


def _limpa(agora: float) -> None:
    """Descarta o que saiu da janela, para a memória não crescer sem limite."""
    for endereco in list(_tentativas):
        recentes = [t for t in _tentativas[endereco] if agora - t < JANELA_EM_SEGUNDOS]
        if recentes:
            _tentativas[endereco] = recentes
        else:
            _tentativas.pop(endereco, None)
            _falhas.pop(endereco, None)
    for desafio, info in list(_desafios.items()):
        criado = info.get("criado_em", 0.0) if isinstance(info, dict) else info
        if agora - criado > JANELA_EM_SEGUNDOS:
            _desafios.pop(desafio, None)


def registra_tentativa(endereco: str, agora: Optional[float] = None) -> Tuple[bool, int]:
    """Anota uma tentativa. Devolve (pode_seguir, segundos_para_tentar_de_novo)."""
    agora = agora if agora is not None else time.time()
    with _trava:
        _limpa(agora)
        marcas = _tentativas.setdefault(endereco, [])
        marcas.append(agora)
        if len(marcas) > TENTATIVAS_POR_JANELA:
            espera = int(JANELA_EM_SEGUNDOS - (agora - marcas[0])) + 1
            return False, max(espera, 1)
        return True, 0


def espera_por_falhas(endereco: str) -> float:
    """Quanto o servidor segura a resposta, dado o histórico de falhas."""
    with _trava:
        falhas = _falhas.get(endereco, 0)
    if falhas <= 0:
        return 0.0
    return min(ESPERA_INICIAL_EM_SEGUNDOS * (2 ** (falhas - 1)), ESPERA_MAXIMA_EM_SEGUNDOS)


def anota_falha(endereco: str) -> int:
    with _trava:
        _falhas[endereco] = _falhas.get(endereco, 0) + 1
        return _falhas[endereco]


def limpa_apos_sucesso(endereco: str) -> None:
    """Quem acertou a senha deixa de ser suspeito."""
    with _trava:
        _falhas.pop(endereco, None)
        _tentativas.pop(endereco, None)


def precisa_de_desafio(endereco: str) -> bool:
    with _trava:
        return _falhas.get(endereco, 0) >= FALHAS_ATE_DESAFIO


def novo_desafio(quantidade: Optional[int] = None) -> str:
    """Cria um desafio interativo de uso único, válido pela mesma janela do teto."""
    qtd = DIFICULDADE if quantidade is None else max(3, min(quantidade, len(ITENS_DESAFIO)))
    desafio_id = secrets.token_hex(16)
    escolhidos = secrets.SystemRandom().sample(ITENS_DESAFIO, qtd)
    alvo = secrets.choice(escolhidos)[0]
    with _trava:
        _desafios[desafio_id] = {
            "criado_em": time.time(),
            "alvo": alvo,
            "opcoes": [item[0] for item in escolhidos],
        }
    return desafio_id


def detalhes_do_desafio(desafio_id: str) -> Optional[Dict[str, Any]]:
    """Devolve as opções e o item alvo do desafio, sem consumi-lo."""
    with _trava:
        info = _desafios.get(desafio_id)
        if not info or not isinstance(info, dict):
            return None
        return {
            "id": desafio_id,
            "alvo": info["alvo"],
            "opcoes": list(info["opcoes"]),
        }


def resposta_confere(desafio: str, resposta: str, dificuldade: Optional[int] = None) -> bool:
    """Confere a resposta do desafio e o consome (uso único)."""
    if not desafio or not resposta:
        return False
    with _trava:
        info = _desafios.pop(desafio, None)
        if not info or not isinstance(info, dict):
            return False
        return str(resposta).strip().lower() == str(info.get("alvo", "")).strip().lower()


def endereco_do_cliente(client_address) -> str:
    """Só o endereço, sem a porta de origem, que muda a cada conexão."""
    try:
        return str(client_address[0])
    except (TypeError, IndexError):
        return "desconhecido"
