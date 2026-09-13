"""Entrada federada no painel: OIDC e SAML2, UM provedor por vez.

O painel continua tendo a porta de sempre — usuário e senha locais, mais a
credencial de recuperação. O SSO é uma porta A MAIS, nunca a única: se o
provedor de identidade cair, quem tem a senha local entra do mesmo jeito, e
`SSO_DISABLED=1` desliga tudo sem tocar no banco.

Três gavetas, separadas por sensibilidade:

1. **O que não é segredo** vai no SQLite de preferências, com o prefixo `sso.`
   — é a mesma tabela onde já moram o idioma e o usuário do painel.
2. **O segredo do cliente OIDC** vai num arquivo 0600 ao lado da credencial de
   recuperação, NUNCA no SQLite. Sem disco gravável o SSO não liga: ligar sem
   segredo seria anunciar uma porta que não abre.
3. **O ambiente vence**: `OIDC_CLIENT_SECRET` tem precedência sobre o arquivo, e
   quando é ele quem manda a tela trava o campo, como já acontece com
   `DASHBOARD_PASSWORD`.

Por que não cifrar o segredo no banco: cifra reversível precisa de chave, e a
chave ficaria num arquivo ao lado do próprio banco, no mesmo volume. O modelo de
ataque é idêntico ao do arquivo 0600 puro, com o custo de uma AEAD artesanal —
a imagem não tem `cryptography`. A proteção real aqui é a permissão e o
isolamento do volume; dizer "cifrado em repouso" com a chave ao lado é teatro.

Sobre a assinatura do `id_token`: ela NÃO é verificada localmente, e isso é
escolha, não atalho. O token chega pelo canal direto entre este processo e o
`token_endpoint`, sobre TLS com certificado verificado e com o cliente
autenticado — o caso que a OIDC Core 3.1.3.7, item 6, dispensa de verificação.
A alternativa exigiria escrever PKCS#1 v1.5 à mão, porque a biblioteca padrão
não verifica RSA e a imagem não tem `cryptography`; é exatamente na conferência
de padding que essas implementações falham em silêncio, aceitando assinatura
forjada. Em troca, o `sub` é ancorado no `userinfo_endpoint`, que só responde a
quem apresenta o `access_token` recebido na mesma troca.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

# -- chaves de configuração --------------------------------------------------
#
# Prefixo `sso.` no MESMO banco de preferências do idioma e do usuário do
# painel. Nada aqui é segredo: são endereços públicos e uma lista de quem pode
# entrar.
CHAVE_PROVEDOR = "sso.enabled"
CHAVE_BASE_URL = "sso.base_url"
CHAVE_OIDC_ISSUER = "sso.oidc.issuer"
CHAVE_OIDC_CLIENT_ID = "sso.oidc.client_id"
CHAVE_OIDC_SCOPES = "sso.oidc.scopes"
CHAVE_SAML_IDP_ENTITY_ID = "sso.saml.idp_entity_id"
CHAVE_SAML_IDP_SSO_URL = "sso.saml.idp_sso_url"
CHAVE_SAML_IDP_CERT = "sso.saml.idp_cert"
CHAVE_DOMINIOS = "sso.allowed_domains"
CHAVE_EMAILS = "sso.allowed_emails"
CHAVE_ATUALIZADO_EM = "sso.updated_at"

ARQUIVO_DO_SEGREDO = ".sso_client_secret"
# Os nomes das duas variáveis de ambiente. Eles aparecem escritos por extenso
# em `os.environ.get` logo abaixo, e não como constante: a guarda que confere se
# o `.env.example` documenta tudo o que o código lê varre a fonte atrás do
# literal, e uma constante a deixaria cega justamente para a variável que
# carrega um segredo. A repetição é deliberada, e o teste de SSO compara as duas
# formas.
VARIAVEL_DO_SEGREDO = "OIDC_CLIENT_SECRET"
VARIAVEL_DE_DESLIGAMENTO = "SSO_DISABLED"

ESCOPOS_PADRAO = "openid email profile"

# Caminhos das rotas. Ficam aqui para que a configuração, a tela e o servidor
# nunca discordem sobre qual é a URL de retorno registrada no provedor.
ROTA_OIDC_INICIAR = "/sso/oidc/iniciar"
ROTA_OIDC_CALLBACK = "/sso/oidc/callback"
ROTA_SAML_INICIAR = "/sso/saml/iniciar"
ROTA_SAML_ACS = "/sso/saml/acs"
ROTA_SAML_METADATA = "/sso/saml/metadata"

# Tolerância de relógio do `iat`. Container com hora fora do lugar derruba todo
# o fluxo, e o sintoma parece "SSO quebrado" em vez de "relógio errado".
TOLERANCIA_DE_RELOGIO = 300

# Descoberta: uma leitura por hora, por processo.
VALIDADE_DA_DESCOBERTA = 3600

# Conjunto de pendentes do SAML: dez minutos e teto duro. `/sso/saml/iniciar` é
# pública, e estado de servidor criado por rota pública sem teto é consumo de
# memória ilimitado à distância de um laço `while true`.
VALIDADE_DO_PENDENTE = 600
LIMITE_DE_PENDENTES = 500

_trava = threading.Lock()
_descobertas: Dict[str, Tuple[float, dict]] = {}
_pendentes: Dict[str, float] = {}
_assercoes_consumidas: Dict[str, float] = {}


class FalhaDeSSO(Exception):
    """Falha de qualquer passo do fluxo federado.

    O motivo fica AQUI, para o log interno. A tela recebe sempre a mesma
    mensagem genérica: distinguir "state trocado" de "e-mail fora da lista"
    conta ao atacante em que ponto do fluxo ele parou.
    """


# ---------------------------------------------------------------------------
# Ambiente
# ---------------------------------------------------------------------------

def desligado_por_ambiente() -> bool:
    """`SSO_DISABLED=1` vence o banco, sem tocar no SQLite.

    É o interruptor de emergência: quando o provedor cai e o painel está atrás
    de um túnel, subir o container com a variável devolve o formulário local.
    O padrão é "0" de propósito -- o auxiliar `_flag` do config assume "1" e
    usá-lo aqui desligaria o SSO em toda instalação.
    """
    return os.environ.get("SSO_DISABLED", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def segredo_do_ambiente() -> str:
    return os.environ.get("OIDC_CLIENT_SECRET", "").strip()


def ler_segredo(caminho: str) -> Tuple[str, bool]:
    """Devolve (segredo, veio_do_ambiente). Ambiente primeiro, depois o arquivo."""
    do_ambiente = segredo_do_ambiente()
    if do_ambiente:
        return do_ambiente, True
    if caminho and os.path.exists(caminho):
        try:
            with open(caminho, "r", encoding="utf-8") as arquivo:
                return arquivo.read().strip(), False
        except OSError:
            # Sem leitura equivale a "não há segredo": o SSO não liga, e o
            # formulário local continua de pé.
            return "", False
    return "", False


def grava_segredo(caminho: str, valor: str) -> bool:
    """Grava o segredo com modo 0600, como a credencial de recuperação."""
    if not caminho:
        return False
    try:
        os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
        # 0600: só o dono do processo lê o segredo do cliente.
        descritor = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descritor, "w", encoding="utf-8") as arquivo:
            arquivo.write(valor)
        return True
    except OSError:
        # Sem disco gravável o SSO NÃO liga. Ligar sem segredo seria desenhar
        # na tela de login um botão que nunca abre.
        return False


def apaga_segredo(caminho: str) -> None:
    try:
        if caminho and os.path.exists(caminho):
            os.remove(caminho)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

def lista_de(valor: str) -> Tuple[str, ...]:
    """Divide uma lista separada por vírgula, em minúsculas e sem vazios."""
    return tuple(
        parte.strip().lower() for parte in (valor or "").split(",") if parte.strip()
    )


@dataclass
class ConfiguracaoSSO:
    """O que o painel sabe sobre o provedor. Nenhum segredo mora aqui."""

    provedor: str = ""
    base_url: str = ""
    issuer: str = ""
    client_id: str = ""
    escopos: str = ESCOPOS_PADRAO
    idp_entity_id: str = ""
    idp_sso_url: str = ""
    idp_cert: str = ""
    dominios: Tuple[str, ...] = ()
    emails: Tuple[str, ...] = ()
    atualizado_em: str = ""
    tem_segredo: bool = False
    segredo_vem_do_ambiente: bool = False
    desligado_no_ambiente: bool = False

    # -- estado -------------------------------------------------------------

    def tem_allowlist(self) -> bool:
        """Allowlist vazia significa que toda conta do provedor entraria."""
        return bool(self.dominios or self.emails)

    def esta_ligado(self) -> bool:
        """Só liga com configuração COMPLETA. Meia configuração fica desligada."""
        if self.desligado_no_ambiente:
            return False
        if not self.base_url or not self.tem_allowlist():
            return False
        if self.provedor == "oidc":
            return bool(self.issuer and self.client_id and self.tem_segredo)
        if self.provedor == "saml":
            return bool(
                self.idp_entity_id
                and self.idp_sso_url
                and self.idp_cert
                and saml_disponivel()
            )
        return False

    # -- endereços ----------------------------------------------------------
    #
    # TODOS derivam de `base_url`, jamais do cabeçalho `Host`. Host é escolhido
    # pelo cliente, e derivar dali a URL de retorno é a definição de
    # redirect_uri aberto: o atacante decide para onde o `code` é entregue.

    def url_de_retorno(self) -> str:
        return self.base_url.rstrip("/") + ROTA_OIDC_CALLBACK

    def url_do_acs(self) -> str:
        return self.base_url.rstrip("/") + ROTA_SAML_ACS

    def entity_id(self) -> str:
        return self.base_url.rstrip("/") + ROTA_SAML_METADATA

    def rota_de_entrada(self) -> str:
        return ROTA_SAML_INICIAR if self.provedor == "saml" else ROTA_OIDC_INICIAR

    def nome_do_provedor(self) -> str:
        """Como o botão da tela de login chama o provedor.

        É o host do issuer (ou do IdP), e não um rótulo digitado: assim o texto
        do botão não pode discordar de para onde o clique leva.
        """
        origem = self.issuer if self.provedor == "oidc" else (
            self.idp_sso_url or self.idp_entity_id
        )
        alvo = urllib.parse.urlsplit(origem or "")
        return alvo.hostname or (origem or "")


def carregar(prefs_path: str, caminho_do_segredo: str) -> ConfiguracaoSSO:
    """Lê a configuração do banco de preferências e o estado do segredo."""
    from .prefs import get_preference

    def ler(chave: str, padrao: str = "") -> str:
        return (get_preference(prefs_path, chave, padrao) or "").strip()

    segredo, do_ambiente = ler_segredo(caminho_do_segredo)
    return ConfiguracaoSSO(
        provedor=ler(CHAVE_PROVEDOR).lower(),
        base_url=ler(CHAVE_BASE_URL),
        issuer=ler(CHAVE_OIDC_ISSUER).rstrip("/"),
        client_id=ler(CHAVE_OIDC_CLIENT_ID),
        escopos=ler(CHAVE_OIDC_SCOPES) or ESCOPOS_PADRAO,
        idp_entity_id=ler(CHAVE_SAML_IDP_ENTITY_ID),
        idp_sso_url=ler(CHAVE_SAML_IDP_SSO_URL),
        idp_cert=ler(CHAVE_SAML_IDP_CERT),
        dominios=lista_de(ler(CHAVE_DOMINIOS)),
        emails=lista_de(ler(CHAVE_EMAILS)),
        atualizado_em=ler(CHAVE_ATUALIZADO_EM),
        tem_segredo=bool(segredo),
        segredo_vem_do_ambiente=do_ambiente,
        desligado_no_ambiente=desligado_por_ambiente(),
    )


def gravar(prefs_path: str, campos: Dict[str, str]) -> bool:
    """Grava as chaves `sso.*` informadas. Devolve False se o disco recusar."""
    from .prefs import set_preference

    tudo_certo = True
    for chave, valor in campos.items():
        if not set_preference(prefs_path, chave, valor):
            tudo_certo = False
    momento = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    set_preference(prefs_path, CHAVE_ATUALIZADO_EM, momento)
    return tudo_certo


def email_autorizado(email: str, config: ConfiguracaoSSO) -> bool:
    """Allowlist OBRIGATÓRIA: e-mail exato, ou domínio inteiro.

    Sem ela, "entrar com Google" significa que toda conta Google do planeta
    entra no painel. Por isso a lista vazia não é "sem filtro": é o painel
    recusando ligar o SSO.
    """
    alvo = (email or "").strip().lower()
    if not alvo or "@" not in alvo or not config.tem_allowlist():
        return False
    if alvo in config.emails:
        return True
    return alvo.rsplit("@", 1)[1] in config.dominios


# ---------------------------------------------------------------------------
# Transporte
# ---------------------------------------------------------------------------

def _transporte_seguro(url: str) -> bool:
    """HTTPS sempre, com a única exceção do loopback.

    O contexto TLS é o padrão da biblioteca, que VERIFICA o certificado. A
    exceção do loopback existe porque um provedor de teste em `127.0.0.1` nunca
    põe um byte na rede — e é assim que a suíte exercita o fluxo inteiro sem
    depender da internet.
    """
    partes = urllib.parse.urlsplit(url or "")
    if partes.scheme == "https":
        return True
    return partes.scheme == "http" and partes.hostname in ("127.0.0.1", "localhost", "::1")


def _pedir(url: str, dados: Optional[bytes] = None,
           cabecalhos: Optional[Dict[str, str]] = None,
           timeout: float = 10.0) -> dict:
    """Uma chamada HTTP que devolve JSON, ou levanta FalhaDeSSO."""
    if not _transporte_seguro(url):
        raise FalhaDeSSO(f"endereco sem TLS fora do loopback: {url}")
    pedido = urllib.request.Request(url, data=dados, headers=cabecalhos or {})
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
            corpo = resposta.read()
    except (urllib.error.URLError, OSError, ValueError) as erro:
        raise FalhaDeSSO(f"falha ao falar com o provedor: {erro}") from erro
    try:
        return json.loads(corpo.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as erro:
        raise FalhaDeSSO("o provedor respondeu algo que não é JSON") from erro


def descobrir(issuer: str, agora: Optional[float] = None) -> dict:
    """Documento de descoberta do issuer, com cache de uma hora por processo."""
    issuer = (issuer or "").rstrip("/")
    if not issuer:
        raise FalhaDeSSO("issuer não configurado")
    agora = agora if agora is not None else time.time()
    with _trava:
        guardado = _descobertas.get(issuer)
        if guardado and guardado[0] > agora:
            return guardado[1]

    documento = _pedir(issuer + "/.well-known/openid-configuration", timeout=5.0)

    # Defesa contra mix-up: o documento tem de se declarar do MESMO issuer que
    # está configurado aqui. Sem esta conferência, um provedor hostil que
    # responda pelo endereço configurado pode se apresentar como outro.
    if str(documento.get("issuer", "")).rstrip("/") != issuer:
        raise FalhaDeSSO("o documento de descoberta declara outro issuer")
    for campo in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
        if not documento.get(campo):
            raise FalhaDeSSO(f"o documento de descoberta não traz {campo}")

    with _trava:
        _descobertas[issuer] = (agora + VALIDADE_DA_DESCOBERTA, documento)
    return documento


def esquece_descobertas() -> None:
    """Zera o cache. Existe para a suíte, e para um reload futuro."""
    with _trava:
        _descobertas.clear()


# ---------------------------------------------------------------------------
# OIDC
# ---------------------------------------------------------------------------

def novo_segredo_de_fluxo() -> str:
    return secrets.token_urlsafe(32)


def novo_verificador() -> str:
    return secrets.token_urlsafe(64)


def desafio_de(verificador: str) -> str:
    """PKCE S256, base64url sem preenchimento.

    **Sempre** S256, nunca "plain": o painel roda em HTTP no loopback, o `code`
    passa pela barra de endereços e fica no histórico. State e nonce sozinhos
    não protegem contra um code interceptado, e a decisão não muda com o
    ambiente -- atrás de um túnel HTTPS o risco cai, mas o custo de S256 é zero.
    """
    resumo = hashlib.sha256((verificador or "").encode("ascii")).digest()
    return base64.urlsafe_b64encode(resumo).decode("ascii").rstrip("=")


def url_de_autorizacao(documento: dict, config: ConfiguracaoSSO,
                       state: str, nonce: str, verificador: str) -> str:
    """Endereço para onde o navegador é enviado, já com PKCE."""
    parametros = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.url_de_retorno(),
        "scope": config.escopos or ESCOPOS_PADRAO,
        "state": state,
        "nonce": nonce,
        "code_challenge": desafio_de(verificador),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    destino = str(documento.get("authorization_endpoint") or "")
    juncao = "&" if "?" in destino else "?"
    return destino + juncao + urllib.parse.urlencode(parametros)


def troca_o_code(documento: dict, config: ConfiguracaoSSO, segredo: str,
                 code: str, verificador: str) -> dict:
    """Troca o `code` pelos tokens, autenticando o cliente por client_secret_basic."""
    corpo = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        # O MESMO endereço enviado na ida, e sempre o da configuração.
        "redirect_uri": config.url_de_retorno(),
        "code_verifier": verificador,
    }).encode("utf-8")
    credencial = "{}:{}".format(
        urllib.parse.quote(config.client_id, safe=""),
        urllib.parse.quote(segredo, safe=""),
    )
    cabecalhos = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "Authorization": "Basic " + base64.b64encode(credencial.encode("utf-8")).decode("ascii"),
    }
    resposta = _pedir(str(documento.get("token_endpoint") or ""), corpo, cabecalhos)
    if resposta.get("error"):
        raise FalhaDeSSO("o provedor recusou a troca do code")
    if not resposta.get("id_token") or not resposta.get("access_token"):
        raise FalhaDeSSO("a troca do code não devolveu os dois tokens")
    return resposta


def decodifica_payload(id_token: str) -> dict:
    """Lê o corpo do `id_token` sem verificar a assinatura -- ver o topo do módulo."""
    partes = (id_token or "").split(".")
    if len(partes) != 3:
        raise FalhaDeSSO("id_token malformado")
    corpo = partes[1]
    # base64url do JWT vem sem preenchimento; sem isto a decodificação levanta.
    corpo += "=" * (-len(corpo) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(corpo.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, TypeError) as erro:
        raise FalhaDeSSO("id_token com corpo ilegível") from erro


def mesmo_texto(a: str, b: str) -> bool:
    """Comparação em tempo constante que aceita qualquer texto.

    `hmac.compare_digest` levanta TypeError com `str` fora de ASCII, e o `state`
    da query é escolhido por quem chama -- comparar os bytes evita transformar
    um valor hostil em erro 500.
    """
    return hmac.compare_digest(str(a or "").encode("utf-8"), str(b or "").encode("utf-8"))


def confere_id_token(payload: dict, config: ConfiguracaoSSO, nonce: str,
                     agora: Optional[float] = None) -> None:
    """Lista fechada de conferências. Levanta FalhaDeSSO na primeira que falhar."""
    agora = agora if agora is not None else time.time()

    if str(payload.get("iss", "")).rstrip("/") != config.issuer:
        raise FalhaDeSSO("iss do id_token difere do issuer configurado")

    audiencia = payload.get("aud")
    audiencias = audiencia if isinstance(audiencia, list) else [audiencia]
    if config.client_id not in [str(item) for item in audiencias if item is not None]:
        raise FalhaDeSSO("aud do id_token não contém o client_id")
    if len(audiencias) > 1 and str(payload.get("azp", "")) != config.client_id:
        raise FalhaDeSSO("id_token com várias audiências e azp que não é nosso")

    try:
        expira = float(payload.get("exp"))
        emitido = float(payload.get("iat"))
    except (TypeError, ValueError) as erro:
        raise FalhaDeSSO("id_token sem exp/iat utilizáveis") from erro
    if expira <= agora:
        raise FalhaDeSSO("id_token expirado")
    if abs(agora - emitido) > TOLERANCIA_DE_RELOGIO:
        raise FalhaDeSSO("iat fora da tolerância: confira o relógio do container")

    if not mesmo_texto(str(payload.get("nonce", "")), nonce):
        raise FalhaDeSSO("nonce do id_token difere do que saiu daqui")


_estados_consumidos: Dict[str, float] = {}


def estado_ja_usado(state: str, agora: Optional[float] = None) -> bool:
    """Uso único do estado da ida. Devolve True se ele JÁ tinha sido gasto.

    Apagar o cookie na volta não basta: quem guardou o valor pode reapresentá-lo.
    O `code` também morre na primeira troca -- mas isso depende do provedor, e o
    controle de quem entra neste painel tem de ser nosso.
    """
    agora = agora if agora is not None else time.time()
    with _trava:
        _limpa_o_que_envelheceu(agora)
        if state in _estados_consumidos:
            return True
        _estados_consumidos[state] = agora
        return False


def busca_userinfo(documento: dict, access_token: str) -> dict:
    """Ancora o `sub`: o userinfo só responde a quem tem o access_token."""
    return _pedir(
        str(documento.get("userinfo_endpoint") or ""),
        cabecalhos={"Authorization": "Bearer " + str(access_token or ""),
                    "Accept": "application/json"},
    )


def email_do_userinfo(userinfo: dict, payload: dict) -> str:
    """Confere o vínculo e devolve o e-mail verificado, ou levanta."""
    # OIDC Core 5.3.2: o `sub` do userinfo TEM de ser o mesmo do id_token.
    if not mesmo_texto(str(userinfo.get("sub", "")), str(payload.get("sub", ""))):
        raise FalhaDeSSO("sub do userinfo difere do sub do id_token")
    verificado = userinfo.get("email_verified")
    if verificado is None:
        verificado = payload.get("email_verified")
    # Sem esta conferência, qualquer conta com e-mail não confirmado no provedor
    # passaria pela allowlist de domínio.
    if verificado is not True and str(verificado).lower() != "true":
        raise FalhaDeSSO("o provedor não afirma que o e-mail foi verificado")
    email = str(userinfo.get("email") or payload.get("email") or "").strip().lower()
    if not email:
        raise FalhaDeSSO("o provedor não devolveu e-mail")
    return email


# ---------------------------------------------------------------------------
# SAML2 -- o que é NOSSO
# ---------------------------------------------------------------------------
#
# A validação da assinatura XML é da biblioteca (`python3-saml`), importada de
# forma preguiçosa. O que não é dela, e por isso está aqui: o conjunto de
# requisições pendentes (que é o que dá sentido ao `InResponseTo`) e o cache de
# repetição do ID da asserção -- a biblioteca NÃO guarda IDs já consumidos.

_saml_disponivel: Optional[bool] = None


def saml_disponivel() -> bool:
    """A biblioteca de SAML está instalada nesta imagem?

    Import preguiçoso e resultado guardado: quem só quer OIDC não carrega nada,
    e a aba SAML da tela aparece desabilitada com a mensagem traduzida em vez de
    o painel quebrar.
    """
    global _saml_disponivel
    if _saml_disponivel is None:
        try:
            import onelogin.saml2.auth  # noqa: F401
            _saml_disponivel = True
        except Exception:
            _saml_disponivel = False
    return _saml_disponivel


def _limpa_o_que_envelheceu(agora: float) -> None:
    """Descarta o que saiu da janela, para a memória não crescer sem limite."""
    for guardado in (_pendentes, _assercoes_consumidas, _estados_consumidos):
        for identificador in list(guardado):
            if agora - guardado[identificador] > VALIDADE_DO_PENDENTE:
                guardado.pop(identificador, None)


def novo_id_de_requisicao() -> str:
    """ID de AuthnRequest. Começa com "_" porque xs:ID não aceita dígito inicial."""
    return "_" + secrets.token_hex(16)


def registra_pendente(identificador: str, agora: Optional[float] = None) -> None:
    """Anota uma AuthnRequest à espera de resposta, com teto duro de memória."""
    agora = agora if agora is not None else time.time()
    with _trava:
        _limpa_o_que_envelheceu(agora)
        if len(_pendentes) >= LIMITE_DE_PENDENTES:
            # Descarta o mais antigo: a rota é pública, e sem teto ela vira
            # consumo de memória ilimitado.
            mais_antigo = min(_pendentes, key=_pendentes.get)
            _pendentes.pop(mais_antigo, None)
        _pendentes[identificador] = agora


def consome_pendente(identificador: str, agora: Optional[float] = None) -> bool:
    """Uso único: a mesma resposta não vale duas vezes."""
    agora = agora if agora is not None else time.time()
    if not identificador:
        return False
    with _trava:
        _limpa_o_que_envelheceu(agora)
        return _pendentes.pop(identificador, None) is not None


def assercao_ja_usada(identificador: str, agora: Optional[float] = None) -> bool:
    """Cache de repetição: dentro da janela de validade, um ID vale uma vez só."""
    agora = agora if agora is not None else time.time()
    with _trava:
        _limpa_o_que_envelheceu(agora)
        if identificador in _assercoes_consumidas:
            return True
        _assercoes_consumidas[identificador] = agora
        return False


def esquece_estado_de_fluxo() -> None:
    """Zera pendentes, repetições e estados gastos. Existe para a suíte."""
    with _trava:
        _pendentes.clear()
        _assercoes_consumidas.clear()
        _estados_consumidos.clear()


# InResponseTo do elemento raiz. Serve APENAS para achar qual AuthnRequest nossa
# está sendo respondida; quem confere o valor de verdade é a biblioteca, contra
# o `request_id` que passamos, e sobre conteúdo assinado.
_RX_IN_RESPONSE_TO = re.compile(r'InResponseTo="([^"]+)"')


def in_response_to(saml_response: str) -> str:
    """Lê o InResponseTo da resposta. Resposta sem ele é RECUSADA.

    Uma resposta sem `InResponseTo` é o fluxo iniciado pelo IdP, e esse é o
    vetor clássico de CSRF de login em SAML: o atacante entrega ao navegador da
    vítima uma asserção legítima da conta DELE.
    """
    try:
        cru = base64.b64decode(saml_response or "", validate=False)
    except Exception as erro:
        raise FalhaDeSSO("SAMLResponse não é base64") from erro
    achado = _RX_IN_RESPONSE_TO.search(cru.decode("utf-8", "replace"))
    if not achado:
        raise FalhaDeSSO("resposta sem InResponseTo (iniciada pelo IdP) é recusada")
    return achado.group(1)


def _atributo(valor: str) -> str:
    """Escapa um valor para caber num atributo XML."""
    return (
        str(valor or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def monta_authn_request(config: ConfiguracaoSSO, identificador: str,
                        agora: Optional[float] = None) -> str:
    """AuthnRequest mínima, NÃO assinada.

    Assinar exigiria uma chave privada do SP -- mais um segredo, mais um arquivo
    0600 -- e quase nenhum IdP a exige. Se o do cliente exigir, é a próxima
    versão, com a chave seguindo a mesma regra do segredo OIDC.
    """
    agora = agora if agora is not None else time.time()
    instante = datetime.fromtimestamp(agora, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        '<samlp:AuthnRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"'
        ' xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"'
        f' ID="{_atributo(identificador)}" Version="2.0"'
        f' IssueInstant="{instante}"'
        f' Destination="{_atributo(config.idp_sso_url)}"'
        f' AssertionConsumerServiceURL="{_atributo(config.url_do_acs())}"'
        ' ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST">'
        f'<saml:Issuer>{_atributo(config.entity_id())}</saml:Issuer>'
        '</samlp:AuthnRequest>'
    )


def url_de_ida_saml(config: ConfiguracaoSSO, xml: str) -> str:
    """HTTP-Redirect binding: deflate cru, base64 e querystring.

    Não é HTTP-POST binding porque um `<form action="https://idp/...">` bateria
    na CSP do painel, que declara `form-action 'self'` -- o navegador bloqueia a
    submissão e nada na tela explica por quê.
    """
    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    cru = compressor.compress(xml.encode("utf-8")) + compressor.flush()
    parametro = base64.b64encode(cru).decode("ascii")
    juncao = "&" if "?" in config.idp_sso_url else "?"
    return config.idp_sso_url + juncao + urllib.parse.urlencode({"SAMLRequest": parametro})


def configuracao_da_biblioteca(config: ConfiguracaoSSO) -> dict:
    """Settings do `python3-saml`, com os defaults perigosos sobrescritos.

    Por padrão a biblioteca monta a URL do ACS a partir de `http_host`/`https`
    da requisição e compara o `Destination` com ela -- exatamente a porta que
    fechamos no OIDC. Aqui tudo sai de `sso.base_url`.
    """
    return {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": config.entity_id(),
            "assertionConsumerService": {
                "url": config.url_do_acs(),
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "NameIDFormat": "urn:oasis:names:tc:SAML:2.0:nameid-format:emailAddress",
        },
        "idp": {
            "entityId": config.idp_entity_id,
            "singleSignOnService": {
                "url": config.idp_sso_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": config.idp_cert,
        },
        "security": {
            "wantAssertionsSigned": True,
            "wantMessagesSigned": True,
            "rejectUnsolicitedResponsesWithInResponseTo": True,
            "wantNameId": True,
            # O painel identifica pelo NameID no formato de e-mail, e muitos
            # provedores mandam só isso. O default da biblioteca é exigir um
            # bloco de atributos, o que recusaria asserções perfeitamente
            # válidas -- e a tentação seguinte seria relaxar algo que importa.
            "wantAttributeStatement": False,
            "authnRequestsSigned": False,
            "wantAssertionsEncrypted": False,
            "requestedAuthnContext": False,
        },
    }


def dados_da_requisicao(config: ConfiguracaoSSO, post: Dict[str, str]) -> dict:
    """O dicionário que a biblioteca lê no lugar da requisição.

    Montado por NÓS a partir de `sso.base_url`: nenhum cabeçalho da requisição
    entra aqui, que é o que impede o cliente de escolher o `Destination` aceito.
    """
    partes = urllib.parse.urlsplit(config.base_url)
    # A porta viaja DENTRO do `http_host`, e não num campo próprio. O campo
    # separado está obsoleto na biblioteca, e preenchê-lo com vazio -- o que
    # acontece quando a `base_url` não declara porta -- fazia a biblioteca
    # montar "https://painel.exemplo.com:/sso/saml/acs", com dois-pontos e sem
    # número, e recusar a própria asserção correta dizendo que o destino não
    # batia. Encontrado na bancada, com asserção assinada de verdade.
    return {
        "https": "on" if partes.scheme == "https" else "off",
        "http_host": partes.netloc,
        "script_name": urllib.parse.urlsplit(config.url_do_acs()).path,
        "get_data": {},
        "post_data": dict(post or {}),
    }


def processa_resposta_saml(config: ConfiguracaoSSO, saml_response: str,
                           id_pendente: str, agora: Optional[float] = None) -> str:
    """Valida a resposta do IdP e devolve o e-mail. Levanta FalhaDeSSO se algo falhar.

    A assinatura, o `Audience`, as janelas de tempo, o `Destination` e o
    `InResponseTo` são conferidos pela biblioteca -- é para isso que ela existe,
    e é por isso que ela é dependência: defender-se de XML Signature Wrapping
    exige amarrar a referência da assinatura ao elemento efetivamente lido, o
    que não se faz com regex nem com `xml.etree`.
    """
    if not saml_disponivel():
        raise FalhaDeSSO("SAML2 não está disponível nesta imagem")
    from onelogin.saml2.auth import OneLogin_Saml2_Auth  # import preguiçoso

    autenticacao = OneLogin_Saml2_Auth(
        dados_da_requisicao(config, {"SAMLResponse": saml_response}),
        configuracao_da_biblioteca(config),
    )
    # `request_id` é o que liga o NOSSO conjunto de pendentes à validação da
    # biblioteca: sem ele, `InResponseTo` não é conferido contra nada.
    autenticacao.process_response(request_id=id_pendente)
    erros = autenticacao.get_errors()
    if erros or not autenticacao.is_authenticated():
        # O motivo detalhado da biblioteca vai junto: ele nomeia QUAL condição
        # falhou (prazo, audiência, destino, assinatura) e é o que torna o log
        # interno útil. Não carrega credencial -- a asserção em si nunca entra.
        raise FalhaDeSSO(
            "a biblioteca recusou a asserção: "
            + ", ".join(erros)
            + " (" + str(autenticacao.get_last_error_reason() or "sem detalhe") + ")"
        )

    # Repetição dentro da janela de validade: a biblioteca NÃO guarda IDs já
    # consumidos, então este controle é nosso.
    identificador = autenticacao.get_last_assertion_id()
    if identificador and assercao_ja_usada(identificador, agora):
        raise FalhaDeSSO("asserção repetida dentro da janela de validade")

    email = str(autenticacao.get_nameid() or "").strip().lower()
    if "@" not in email:
        atributos = autenticacao.get_attributes() or {}
        for nome in ("email", "mail", "urn:oid:0.9.2342.19200300.100.1.3",
                     "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"):
            valores = atributos.get(nome) or []
            if valores:
                email = str(valores[0]).strip().lower()
                break
    if "@" not in email:
        raise FalhaDeSSO("a asserção não traz e-mail")
    return email


def metadata_do_sp(config: ConfiguracaoSSO) -> str:
    """XML de metadados que o operador entrega ao IdP.

    Servido apenas COM sessão: não há pressa nenhuma em publicá-lo sem login, e
    cada rota pública a mais é superfície a mais.
    """
    if not saml_disponivel():
        raise FalhaDeSSO("SAML2 não está disponível nesta imagem")
    from onelogin.saml2.settings import OneLogin_Saml2_Settings  # import preguiçoso

    ajustes = OneLogin_Saml2_Settings(configuracao_da_biblioteca(config), sp_validation_only=True)
    metadados = ajustes.get_sp_metadata()
    # A biblioteca devolve `bytes` quando o SP tem certificado e `str` quando
    # não tem -- e o nosso não tem, porque a AuthnRequest não é assinada nesta
    # versão. Tratar os dois casos evita um AttributeError subindo até o
    # handler, onde nada o capturaria.
    if isinstance(metadados, bytes):
        return metadados.decode("utf-8")
    return str(metadados)
