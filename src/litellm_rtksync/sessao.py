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
import os
import secrets
import time
from typing import Optional

NOME_DO_COOKIE = "litellmrtksync_sessao"

# Oito horas: um turno de trabalho. Depois disso o operador entra de novo.
VALIDADE_EM_SEGUNDOS = 8 * 60 * 60

_SEGREDO = secrets.token_bytes(32)


def _assina(carga: str) -> str:
    return hmac.new(_SEGREDO, carga.encode("utf-8"), hashlib.sha256).hexdigest()


def _assinatura_confere(apresentada: str, carga: str) -> bool:
    """Compara a assinatura em tempo constante, sem levantar com texto hostil."""
    return hmac.compare_digest(
        str(apresentada or "").encode("utf-8", "surrogatepass"),
        _assina(carga).encode("ascii"),
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
    # compare_digest: a comparação não pode vazar, pelo tempo que leva, quantos
    # caracteres do início bateram. Em BYTES, e não em texto: o cabeçalho Cookie
    # é decodificado em latin-1, e um único byte acima de 0x7f na assinatura
    # faria `compare_digest` levantar TypeError -- uma exceção não tratada numa
    # rota pública, escolhida pelo visitante.
    if not _assinatura_confere(assinatura, carga):
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


def cabecalho_para_gravar(valor: str) -> str:
    """Cookie de sessão: inacessível ao script da página e presa a este site.

    Sem `Secure` de propósito: o painel é servido em HTTP no loopback, e um
    cookie `Secure` simplesmente não seria gravado ali.
    """
    return (
        f"{NOME_DO_COOKIE}={valor}; Path=/; HttpOnly; SameSite=Strict; "
        f"Max-Age={VALIDADE_EM_SEGUNDOS}"
    )


def cabecalho_para_apagar() -> str:
    return f"{NOME_DO_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"


def ler_do_cabecalho(cabecalho_cookie: str) -> str:
    """Extrai o valor do nosso cookie de um cabeçalho Cookie cru."""
    return _valor_do_cookie(cabecalho_cookie, NOME_DO_COOKIE)


def _valor_do_cookie(cabecalho_cookie: str, nome_procurado: str) -> str:
    for parte in (cabecalho_cookie or "").split(";"):
        nome, _, valor = parte.strip().partition("=")
        if nome == nome_procurado:
            return valor
    return ""


# ---------------------------------------------------------------------------
# Cookie de estado do SSO
# ---------------------------------------------------------------------------
#
# A ida ao provedor de identidade e a volta dele são uma viagem: `state`,
# `nonce` e o verificador do PKCE precisam sobreviver a ela sem existir no
# servidor. Vão num cookie assinado com o MESMO segredo de processo do cookie de
# sessão -- um segredo só, e o reinício invalida os dois de uma vez.

NOME_DO_COOKIE_DE_ESTADO = "litellmrtksync_sso_estado"

# Dez minutos: o tempo de uma tela de login no provedor, e nem um minuto a mais.
VALIDADE_DO_ESTADO_EM_SEGUNDOS = 600

_SEPARADOR_DO_ESTADO = "|"


def emitir_estado_sso(state: str, nonce: str, verificador: str,
                      agora: Optional[float] = None) -> str:
    """Empacota o estado da ida ao provedor num valor assinado."""
    expira = int(
        (agora if agora is not None else time.time()) + VALIDADE_DO_ESTADO_EM_SEGUNDOS
    )
    carga = _SEPARADOR_DO_ESTADO.join([state, nonce, verificador, str(expira)])
    codificada = base64.urlsafe_b64encode(carga.encode("utf-8")).decode("ascii")
    return f"{codificada}.{_assina(carga)}"


def ler_estado_sso(valor: str, agora: Optional[float] = None) -> Optional[dict]:
    """Devolve {state, nonce, verificador} se o cookie for íntegro e estiver no prazo."""
    if not valor or "." not in valor:
        return None
    codificada, assinatura = valor.rsplit(".", 1)
    try:
        carga = base64.urlsafe_b64decode(codificada.encode("ascii")).decode("utf-8")
    except Exception:
        return None
    if not _assinatura_confere(assinatura, carga):
        return None
    partes = carga.split(_SEPARADOR_DO_ESTADO)
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


def cabecalho_para_gravar_estado_sso(valor: str) -> str:
    """Cookie de estado: `Lax`, curto e restrito ao caminho do SSO.

    NÃO pode ser `SameSite=Strict` como o cookie de sessão: o retorno do
    provedor é navegação vinda de outro site, e o navegador simplesmente não
    envia um cookie Strict nesse salto -- a falha apareceria como "login que não
    funciona", sem erro nenhum na tela. Sem `Secure` pelo mesmo motivo do cookie
    de sessão: o painel é servido em HTTP no loopback.
    """
    return (
        f"{NOME_DO_COOKIE_DE_ESTADO}={valor}; Path=/sso/; HttpOnly; SameSite=Lax; "
        f"Max-Age={VALIDADE_DO_ESTADO_EM_SEGUNDOS}"
    )


def cabecalho_para_apagar_estado_sso() -> str:
    """Consumo do estado. O `Path` tem de ser o MESMO da gravação, ou nada é apagado."""
    return f"{NOME_DO_COOKIE_DE_ESTADO}=; Path=/sso/; HttpOnly; SameSite=Lax; Max-Age=0"


def ler_estado_do_cabecalho(cabecalho_cookie: str) -> str:
    return _valor_do_cookie(cabecalho_cookie, NOME_DO_COOKIE_DE_ESTADO)
