"""Sessão do painel: um cookie assinado, emitido por um formulário de login.

O painel nasceu só com Basic Auth, e isso cobra três preços. O navegador abre um
diálogo próprio, fora da página, que não se pode estilizar nem traduzir; não há
logout, porque o navegador reenvia a credencial até fechar a janela; e qualquer
ferramenta que dirija um navegador trava no diálogo, que não é HTML.

O Basic Auth continua aceito — é o que faz `curl` e scripts funcionarem sem
sessão. O que muda é que agora existe uma segunda porta: um formulário que
entrega um cookie assinado.

O segredo que assina o cookie nasce a cada processo, em memória. Reiniciar o
serviço invalida as sessões abertas, o que é a escolha certa para um painel que
lê credenciais: não há sessão sobrevivendo a uma troca de senha ou a um
container recriado.
"""

import base64
import hashlib
import hmac
import secrets
import time
from typing import Optional

from .identidade import NOME_DO_COOKIE, NOME_DO_COOKIE_DE_ESTADO

# Oito horas: um turno de trabalho. Depois disso o operador entra de novo.
VALIDADE_EM_SEGUNDOS = 8 * 60 * 60

# O estado do SSO dura o tempo de escolher a conta no provedor, e não mais.
VALIDADE_DO_ESTADO_EM_SEGUNDOS = 600

_SEGREDO = secrets.token_bytes(32)


def _assina(carga: str) -> str:
    return hmac.new(
        _SEGREDO,
        f"sessao|{NOME_DO_COOKIE}|".encode("ascii") + carga.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _assina_estado(carga: str) -> str:
    """Assinatura do cookie de estado, com o MESMO segredo e domínio separado.

    O segredo é um só — dois segredos seriam duas coisas para rodar e uma para
    esquecer. O prefixo é o que impede que um valor assinado para um dos dois
    cookies seja aceito como o outro: sem ele, um estado de SSO forjado poderia
    ser apresentado como cookie de sessão.
    """
    return hmac.new(
        _SEGREDO, b"estado-sso|" + carga.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _assinatura_confere(apresentada: str, esperada: str) -> bool:
    """Compara em tempo constante e em BYTES, sem levantar com texto hostil.

    A comparação não pode vazar, pelo tempo que leva, quantos caracteres do
    início bateram. E tem de ser em bytes: o cabeçalho Cookie é decodificado em
    latin-1, e um único byte acima de 0x7f na assinatura faria `compare_digest`
    levantar TypeError — uma exceção não tratada numa rota pública, com o
    conteúdo escolhido pelo visitante.
    """
    return hmac.compare_digest(
        str(apresentada or "").encode("utf-8", "surrogatepass"),
        esperada.encode("ascii"),
    )


def emitir(usuario: str, agora: Optional[float] = None) -> str:
    """Devolve o valor do cookie para um usuário já autenticado."""
    expira = int((agora if agora is not None else time.time()) + VALIDADE_EM_SEGUNDOS)
    carga = f"{usuario}|{expira}"
    codificada = base64.urlsafe_b64encode(carga.encode("utf-8")).decode("ascii")
    return f"{codificada}.{_assina(carga)}"


def usuario_da_sessao(valor: str, agora: Optional[float] = None) -> Optional[str]:
    """Devolve o usuário se o cookie for íntegro e estiver no prazo, senão None."""
    if not valor or "." not in valor:
        return None
    codificada, assinatura = valor.rsplit(".", 1)
    try:
        carga = base64.urlsafe_b64decode(codificada.encode("ascii")).decode("utf-8")
    except Exception:
        return None
    if not _assinatura_confere(assinatura, _assina(carga)):
        return None
    if "|" not in carga:
        return None
    usuario, _, expira = carga.rpartition("|")
    try:
        if float(expira) < (agora if agora is not None else time.time()):
            return None
    except ValueError:
        return None
    return usuario or None


def cabecalho_para_gravar(valor: str, seguro: bool = False) -> str:
    """Cookie de sessão: inacessível ao script da página e presa a este site.

    Sem `Secure` por padrão para desenvolvimento em loopback HTTP; com
    `seguro=True`, adiciona `; Secure` quando servido em HTTPS ou atrás de proxy.
    """
    s = "; Secure" if seguro else ""
    return (
        f"{NOME_DO_COOKIE}={valor}; Path=/; HttpOnly; SameSite=Strict; "
        f"Max-Age={VALIDADE_EM_SEGUNDOS}{s}"
    )


def cabecalho_para_apagar(seguro: bool = False) -> str:
    s = "; Secure" if seguro else ""
    return (
        f"{NOME_DO_COOKIE}=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; "
        f"Max-Age=0; HttpOnly; SameSite=Strict{s}"
    )


def ler_cookie(cabecalho_cookie: str, nome_procurado: str) -> str:
    """Extrai um cookie pelo nome de um cabeçalho Cookie cru."""
    for parte in (cabecalho_cookie or "").split(";"):
        nome, _, valor = parte.strip().partition("=")
        if nome == nome_procurado:
            return valor
    return ""


def ler_do_cabecalho(cabecalho_cookie: str) -> str:
    """Extrai o valor do nosso cookie de sessão de um cabeçalho Cookie cru."""
    return ler_cookie(cabecalho_cookie, NOME_DO_COOKIE)


# --- Cookie de estado do SSO ------------------------------------------------
#
# Fica aqui, e não num módulo próprio, para que o segredo que assina continue
# sendo UM só por processo: dois segredos seriam duas superfícies para manter
# em dia, e a segunda é sempre a que alguém esquece de rotacionar.


def emitir_estado_sso(
    state: str, nonce: str, verificador: str, agora: Optional[float] = None
) -> str:
    """Assina o estado da ida ao provedor de identidade.

    Os três valores nascem de `secrets.token_urlsafe`, que não produz `|`: o
    separador é seguro, e um valor que o contenha não sobreviveria à leitura.
    """
    expira = int(
        (agora if agora is not None else time.time()) + VALIDADE_DO_ESTADO_EM_SEGUNDOS
    )
    carga = f"{state}|{nonce}|{verificador}|{expira}"
    codificada = base64.urlsafe_b64encode(carga.encode("utf-8")).decode("ascii")
    return f"{codificada}.{_assina_estado(carga)}"


def ler_estado_sso(valor: str, agora: Optional[float] = None) -> Optional[dict]:
    """Devolve o estado se o cookie for íntegro e estiver no prazo, senão None."""
    if not valor or "." not in valor:
        return None
    codificada, assinatura = valor.rsplit(".", 1)
    try:
        carga = base64.urlsafe_b64decode(codificada.encode("ascii")).decode("utf-8")
    except Exception:
        return None
    if not _assinatura_confere(assinatura, _assina_estado(carga)):
        return None
    partes = carga.split("|")
    if len(partes) != 4:
        return None
    state, nonce, verificador, expira = partes
    try:
        if float(expira) < (agora if agora is not None else time.time()):
            return None
    except ValueError:
        return None
    if not state or not nonce or not verificador:
        return None
    return {"state": state, "nonce": nonce, "verificador": verificador}


def cabecalho_para_gravar_estado(valor: str, seguro: bool = False) -> str:
    """Cookie de estado do SSO: `Lax`, curto e restrito ao caminho do fluxo.

    NÃO pode ser `SameSite=Strict` como o de sessão: a volta do provedor é uma
    navegação vinda de outro site, e um cookie `Strict` simplesmente não é
    enviado nela — a falha apareceria como "login que não funciona", sem erro
    nenhum na tela. Sem `Secure` no loopback, mas com `; Secure` quando em HTTPS.
    """
    s = "; Secure" if seguro else ""
    return (
        f"{NOME_DO_COOKIE_DE_ESTADO}={valor}; Path=/sso/; HttpOnly; SameSite=Lax; "
        f"Max-Age={VALIDADE_DO_ESTADO_EM_SEGUNDOS}{s}"
    )


def cabecalho_para_apagar_estado(seguro: bool = False) -> str:
    """Consumo de uso único: o mesmo `Path` do cookie, ou o navegador não o apaga."""
    s = "; Secure" if seguro else ""
    return (
        f"{NOME_DO_COOKIE_DE_ESTADO}=; Path=/sso/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; "
        f"Max-Age=0; HttpOnly; SameSite=Lax{s}"
    )


def ler_estado_do_cabecalho(cabecalho_cookie: str) -> str:
    return ler_cookie(cabecalho_cookie, NOME_DO_COOKIE_DE_ESTADO)
