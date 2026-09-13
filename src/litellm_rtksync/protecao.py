"""Freio contra força bruta e varredura automatizada, sem depender de ninguém.

Um painel preso ao loopback não precisa disso. Um painel atrás de um túnel
precisa, e o túnel é um botão que o operador aperta quando quiser — então o
freio tem de já estar aqui quando ele apertar.

Três camadas, da mais barata para a mais cara:

1. **Teto por janela.** Mais de `TENTATIVAS_POR_JANELA` tentativas de login no
   mesmo endereço dentro de `JANELA_EM_SEGUNDOS` devolve **429** com
   `Retry-After`. É o que para o script que tenta mil senhas por minuto.

2. **Espera que cresce.** Cada falha seguida atrasa a resposta seguinte, dobrando
   até um teto. Um humano que errou a senha espera um segundo; um robô que erra
   sempre passa a esperar dezenas. O atraso é do lado do servidor: não há nada no
   cliente para desligar.

3. **Prova de trabalho.** Depois de `FALHAS_ATE_DESAFIO` falhas, o formulário só
   é aceito com a resposta de um desafio que custa CPU para resolver e é
   instantâneo de conferir. Sem conta, sem serviço externo, sem cookie de
   rastreio e sem imagem para decifrar — o custo cai sobre quem tenta em massa,
   e não sobre quem esqueceu a senha.

O estado vive em memória, por processo. Reiniciar zera os contadores, o que é
aceitável: reiniciar é justamente o que um atacante não consegue fazer.
"""

import hashlib
import secrets
import threading
import time
from typing import Dict, List, Optional, Tuple

JANELA_EM_SEGUNDOS = 300
TENTATIVAS_POR_JANELA = 10

FALHAS_ATE_DESAFIO = 3
ESPERA_INICIAL_EM_SEGUNDOS = 1.0
ESPERA_MAXIMA_EM_SEGUNDOS = 30.0

# Quantos zeros hexadecimais o resumo precisa ter. Cada zero a mais multiplica
# o custo por dezesseis, e medido nesta máquina:
#
#     3 zeros ->     1 ms  (~1.500 tentativas)
#     4 zeros ->    12 ms  (~29.000 tentativas)
#     5 zeros ->   532 ms  (~1.275.000 tentativas)
#
# Conferir custa 0,3 microssegundo em qualquer um deles -- é essa assimetria,
# de mais de quarenta mil vezes, que faz a prova de trabalho servir.
DIFICULDADE = 4

# A dificuldade CRESCE com a insistência. Doze milissegundos não incomodam quem
# errou a senha, e também não incomodam um bot que só quer testar uma senha por
# endereço -- é o ataque distribuído, com muitos IPs, que o teto por janela não
# alcança. Subir um zero a cada bloco de falhas põe o preço onde ele precisa
# estar sem cobrar nada de quem acerta na segunda tentativa.
DIFICULDADE_MAXIMA = 6


def dificuldade_para(endereco: str) -> int:
    """Quantos zeros exigir deste endereço, dado o histórico dele."""
    with _trava:
        falhas = _falhas.get(endereco, 0)
    extra = max(0, (falhas - FALHAS_ATE_DESAFIO) // 3)
    return min(DIFICULDADE + extra, DIFICULDADE_MAXIMA)

_trava = threading.Lock()
_tentativas: Dict[str, List[float]] = {}
_falhas: Dict[str, int] = {}
_desafios: Dict[str, float] = {}


def _limpa(agora: float) -> None:
    """Descarta o que saiu da janela, para a memória não crescer sem limite."""
    for endereco in list(_tentativas):
        recentes = [t for t in _tentativas[endereco] if agora - t < JANELA_EM_SEGUNDOS]
        if recentes:
            _tentativas[endereco] = recentes
        else:
            _tentativas.pop(endereco, None)
            _falhas.pop(endereco, None)
    for desafio in list(_desafios):
        if agora - _desafios[desafio] > JANELA_EM_SEGUNDOS:
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


def novo_desafio() -> str:
    """Cria um desafio de uso único, válido pela mesma janela do teto."""
    desafio = secrets.token_hex(16)
    with _trava:
        _desafios[desafio] = time.time()
    return desafio


def resposta_confere(desafio: str, resposta: str, dificuldade: Optional[int] = None) -> bool:
    """Confere a prova de trabalho e consome o desafio (uso único)."""
    if not desafio or not resposta:
        return False
    exigidos = DIFICULDADE if dificuldade is None else dificuldade
    with _trava:
        if desafio not in _desafios:
            return False
    resumo = hashlib.sha256(f"{desafio}{resposta}".encode("utf-8")).hexdigest()
    if not resumo.startswith("0" * exigidos):
        return False
    with _trava:
        # Consumido: reapresentar a mesma resposta não passa de novo.
        _desafios.pop(desafio, None)
    return True


def endereco_do_cliente(client_address) -> str:
    """Só o endereço, sem a porta de origem, que muda a cada conexão."""
    try:
        return str(client_address[0])
    except (TypeError, IndexError):
        return "desconhecido"
