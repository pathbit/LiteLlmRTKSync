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
import logging
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
from typing import Any, Dict, Optional, Tuple

# A descoberta é o único passo do fluxo que fala com o provedor sem ninguém
# esperando na frente da tela: quando ela falha, o botão some do login e o
# motivo não chega a lugar nenhum se não for registrado aqui.
_log = logging.getLogger(__name__)

# -- chaves de configuração --------------------------------------------------
#
# Prefixo `sso.` no MESMO banco de preferências do idioma e do usuário do
# painel. Nada aqui é segredo: são endereços públicos e uma lista de quem pode
# entrar.
CHAVE_PROVEDOR = "sso.enabled"
CHAVE_SENHA_HABILITADA = "auth.password_enabled"
CHAVE_OIDC_HABILITADO = "sso.oidc.enabled"
CHAVE_SAML_HABILITADO = "sso.saml.enabled"
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

# Os dois tempos de espera do fluxo, nomeados.
# Espalhados como `5.0` e `10.0` no meio das chamadas, eles são a primeira coisa
# que alguém esquece de rever — e um pedido sem limite pendura a thread que
# serve o painel até o provedor devolver algo.
TIMEOUT_DESCOBERTA = 5.0
TIMEOUT_TOKEN = 10.0

# Os endereços que não põem um byte na rede.
# Declarados como conjunto, e não embutidos na checagem, porque é esta lista que
# define a ÚNICA exceção ao HTTPS obrigatório.
HOSTS_DE_LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}

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

# Descoberta: uma leitura por hora, por processo. A FALHA também é guardada,
# por um minuto: a tela de login pergunta ao emissor para saber se desenha o
# botão, e sem esse negativo curto cada carregamento da tela com o provedor
# fora do ar pagaria o tempo de espera inteiro -- a página que tem de
# continuar de pé quando o provedor cai seria a primeira a parar.
VALIDADE_DA_DESCOBERTA = 3600
VALIDADE_DA_FALHA_DE_DESCOBERTA = 60

# Conjunto de pendentes do SAML: dez minutos e teto duro. `/sso/saml/iniciar` é
# pública, e estado de servidor criado por rota pública sem teto é consumo de
# memória ilimitado à distância de um laço `while true`.
VALIDADE_DO_PENDENTE = 600
LIMITE_DE_PENDENTES = 500

_trava = threading.Lock()
_descobertas: Dict[str, Tuple[float, Optional[dict]]] = {}
_pendentes: Dict[str, float] = {}
_assercoes_consumidas: Dict[str, float] = {}


class FalhaDeSSO(Exception):
    """Falha de qualquer passo do fluxo federado.

    O motivo fica AQUI, para o log interno. A tela recebe sempre a mesma
    mensagem genérica: distinguir "state trocado" de "e-mail fora da lista"
    conta ao atacante em que ponto do fluxo ele parou.

    O motivo também fica em `detalhe`, e não só no texto da exceção: quem
    escreve no log pede `erro.detalhe` sem depender de `str(erro)`, que muda de
    forma no dia em que alguém acrescentar um argumento à exceção.
    """

    def __init__(self, detalhe: str):
        super().__init__(detalhe)
        self.detalhe = detalhe


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
        "1", "true", "yes", "on", "sim",
    )


def segredo_do_ambiente() -> str:
    return os.environ.get("OIDC_CLIENT_SECRET", "").strip()


def _le_segredo(caminho: str) -> Tuple[str, bool]:
    """Devolve (segredo, veio_do_ambiente). Ambiente primeiro, depois o arquivo.

    É esta função, e nunca o nome público abaixo, que o resto do módulo chama.
    No fim do arquivo cada painel liga as suas rotas ao módulo, e lá
    `ler_segredo` pode ter outra assinatura -- a de quem ainda passa o
    DIRETÓRIO em vez do caminho. Chamar daqui o nome público faria o núcleo
    enxergar a versão de quem ligou por último.
    """
    do_ambiente = segredo_do_ambiente()
    if do_ambiente:
        return do_ambiente, True
    if caminho and os.path.isdir(caminho):
        caminho = os.path.join(caminho, ARQUIVO_DO_SEGREDO)
    if caminho and os.path.exists(caminho):
        try:
            with open(caminho, "r", encoding="utf-8") as arquivo:
                return arquivo.read().strip(), False
        except OSError:
            # Sem leitura equivale a "não há segredo": o SSO não liga, e o
            # formulário local continua de pé.
            return "", False
    return "", False


def ler_segredo(caminho: str) -> Tuple[str, bool]:
    """O mesmo que `_le_segredo`. É este o nome que as rotas chamam."""
    return _le_segredo(caminho)


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
    """Divide uma lista separada por vírgula, em minúsculas e sem vazios.

    O `@` da frente cai: quem cadastra um domínio costuma escrever
    `@empresa.com`, e uma entrada que nunca casa é pior que um erro -- ela liga
    o SSO com uma lista que não autoriza ninguém e não diz por quê.
    """
    return tuple(
        parte.strip().lower().lstrip("@") for parte in (valor or "").split(",")
        if parte.strip()
    )


@dataclass
class ConfiguracaoSSO:
    """O que o painel sabe sobre o provedor. Nenhum segredo mora aqui."""

    provedor: str = ""
    oidc_habilitado: bool = False
    saml_habilitado: bool = False
    senha_habilitada: bool = True
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

    def oidc_esta_ligado(self) -> bool:
        if self.desligado_no_ambiente:
            return False
        if not self.provedor or self.provedor in ("0", "none", "off", "false", "disabled"):
            return False
        if not self.oidc_habilitado and self.provedor not in ("oidc", "both", "all"):
            return False
        if not self.base_url or not self.tem_allowlist():
            return False
        return bool(self.issuer and self.client_id and self.tem_segredo)

    def saml_esta_ligado(self) -> bool:
        if self.desligado_no_ambiente:
            return False
        if not self.provedor or self.provedor in ("0", "none", "off", "false", "disabled"):
            return False
        if not self.saml_habilitado and self.provedor not in ("saml", "both", "all"):
            return False
        if not self.base_url or not self.tem_allowlist():
            return False
        return bool(
            self.idp_entity_id
            and self.idp_sso_url
            and self.idp_cert
            and saml_disponivel()
        )

    def esta_ligado(self) -> bool:
        """Só liga com configuração COMPLETA. Meia configuração fica desligada."""
        return self.oidc_esta_ligado() or self.saml_esta_ligado()

    def senha_esta_ligada(self) -> bool:
        """A senha local sempre pode ser usada se o SSO estiver desligado no ambiente
        ou se nenhum provedor federado estiver ativo (salvaguarda contra lockout)."""
        if self.desligado_no_ambiente:
            return True
        if not self.esta_ligado():
            return True
        return self.senha_habilitada

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
        if self.oidc_esta_ligado():
            return ROTA_OIDC_INICIAR
        return ROTA_SAML_INICIAR if self.saml_esta_ligado() else ROTA_OIDC_INICIAR

    def nome_do_oidc(self) -> str:
        if not self.issuer:
            return "OIDC"
        alvo = urllib.parse.urlsplit(self.issuer)
        return alvo.hostname or self.issuer

    def nome_do_saml(self) -> str:
        origem = self.idp_sso_url or self.idp_entity_id
        if not origem:
            return "SAML 2.0"
        alvo = urllib.parse.urlsplit(origem)
        return alvo.hostname or origem

    def nome_do_provedor(self) -> str:
        """Como o botão da tela de login chama o provedor."""
        if self.oidc_esta_ligado():
            return self.nome_do_oidc()
        if self.saml_esta_ligado():
            return self.nome_do_saml()
        origem = self.issuer or self.idp_sso_url or self.idp_entity_id
        alvo = urllib.parse.urlsplit(origem or "")
        return alvo.hostname or (origem or "")


def carregar(prefs_path: str, caminho_do_segredo: str) -> ConfiguracaoSSO:
    """Lê a configuração do banco de preferências e o estado do segredo."""
    from .prefs import get_preference

    def ler(chave: str, padrao: str = "") -> str:
        return (get_preference(prefs_path, chave, padrao) or "").strip()

    segredo, do_ambiente = _le_segredo(caminho_do_segredo)
    prov = ler(CHAVE_PROVEDOR).lower()
    if prov in ("", "0", "none", "off", "false", "disabled"):
        oidc_hab = False
        saml_hab = False
    else:
        oidc_hab = ler(CHAVE_OIDC_HABILITADO, "1" if prov in ("oidc", "both", "all") else "0") == "1"
        saml_hab = ler(CHAVE_SAML_HABILITADO, "1" if prov in ("saml", "both", "all") else "0") == "1"
    senha_hab = ler(CHAVE_SENHA_HABILITADA, "1") != "0"

    return ConfiguracaoSSO(
        provedor=prov,
        oidc_habilitado=oidc_hab,
        saml_habilitado=saml_hab,
        senha_habilitada=senha_hab,
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


def email_autorizado(email: str, config: Any) -> bool:
    """Allowlist OBRIGATÓRIA: e-mail exato, ou domínio inteiro.

    Sem ela, "entrar com Google" significa que toda conta Google do planeta
    entra no painel. Por isso a lista vazia não é "sem filtro": é o painel
    recusando ligar o SSO.
    """
    if isinstance(config, dict):
        config = _configuracao_de(config)
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
    return partes.scheme == "http" and partes.hostname in HOSTS_DE_LOOPBACK


def _pedir(url: str, dados: Optional[bytes] = None,
           cabecalhos: Optional[Dict[str, str]] = None,
           timeout: float = TIMEOUT_TOKEN) -> dict:
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
    """Documento de descoberta do issuer, com cache de uma hora por processo.

    Levanta FalhaDeSSO por qualquer motivo -- inclusive por um que acabou de
    acontecer: a falha fica memorizada por um minuto, e dentro dessa janela a
    resposta sai daqui sem pôr um byte na rede.
    """
    issuer = (issuer or "").rstrip("/")
    if not issuer:
        raise FalhaDeSSO("issuer não configurado")
    if not _transporte_seguro(issuer):
        raise FalhaDeSSO("issuer sem TLS fora do loopback")
    agora = agora if agora is not None else time.time()
    with _trava:
        guardado = _descobertas.get(issuer)
        if guardado and guardado[0] > agora:
            if guardado[1] is None:
                raise FalhaDeSSO("a descoberta falhou há pouco e ainda está memorizada")
            return guardado[1]

    try:
        documento = _pedir(
            issuer + "/.well-known/openid-configuration", timeout=TIMEOUT_DESCOBERTA
        )

        # Defesa contra mix-up: o documento tem de se declarar do MESMO issuer
        # que está configurado aqui. Sem esta conferência, um provedor hostil
        # que responda pelo endereço configurado pode se apresentar como outro.
        if str(documento.get("issuer", "")).rstrip("/") != issuer:
            raise FalhaDeSSO("o documento de descoberta declara outro issuer")
        for campo in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
            alvo = str(documento.get(campo) or "")
            if not alvo:
                raise FalhaDeSSO(f"o documento de descoberta não traz {campo}")
            # O endereço é recusado AQUI, e não só na hora de usá-lo: um
            # documento que aponta o token_endpoint para `http://` não vale nada
            # inteiro, e guardá-lo seria repetir a recusa a cada passo.
            if not _transporte_seguro(alvo):
                raise FalhaDeSSO(f"{campo} do documento de descoberta está sem TLS")
    except Exception as erro:
        _log.warning("SSO: a descoberta do emissor falhou (%s)", erro)
        with _trava:
            _descobertas[issuer] = (agora + VALIDADE_DA_FALHA_DE_DESCOBERTA, None)
        if isinstance(erro, FalhaDeSSO):
            raise
        raise FalhaDeSSO(f"descoberta falhou: {type(erro).__name__}") from erro

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


def parametros_de_autorizacao(config: ConfiguracaoSSO, state: str, nonce: str,
                              desafio: str) -> Dict[str, str]:
    """Os campos da ida, separados de como eles viram endereço.

    Quem já calculou o desafio PKCE monta a URL com estes campos sem refazer a
    conta, e a lista de campos continua existindo num lugar só.
    """
    return {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.url_de_retorno(),
        "scope": config.escopos or ESCOPOS_PADRAO,
        "state": state,
        "nonce": nonce,
        "code_challenge": desafio,
        "code_challenge_method": "S256",
        # Escolher a conta explicitamente: sem isto, quem já está logado no
        # provedor com OUTRA conta entra com ela sem ver qual é.
        "prompt": "select_account",
    }


def url_de_autorizacao(documento: dict, config: ConfiguracaoSSO,
                       state: str, nonce: str, verificador: str) -> str:
    """Endereço para onde o navegador é enviado, já com PKCE."""
    destino = str(documento.get("authorization_endpoint") or "")
    juncao = "&" if "?" in destino else "?"
    return destino + juncao + urllib.parse.urlencode(
        parametros_de_autorizacao(config, state, nonce, desafio_de(verificador))
    )


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


def monta_authn_request(config: Any, identificador: str,
                        agora: Optional[float] = None) -> str:
    """AuthnRequest mínima, NÃO assinada.

    Assinar exigiria uma chave privada do SP -- mais um segredo, mais um arquivo
    0600 -- e quase nenhum IdP a exige. Se o do cliente exigir, é a próxima
    versão, com a chave seguindo a mesma regra do segredo OIDC.
    """
    if isinstance(config, dict):
        config = _configuracao_de(config)
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


def url_de_ida_saml(config: Any, xml: str) -> str:
    """HTTP-Redirect binding: deflate cru, base64 e querystring.

    Não é HTTP-POST binding porque um `<form action="https://idp/...">` bateria
    na CSP do painel, que declara `form-action 'self'` -- o navegador bloqueia a
    submissão e nada na tela explica por quê.
    """
    if isinstance(config, dict):
        config = _configuracao_de(config)
    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    cru = compressor.compress(xml.encode("utf-8")) + compressor.flush()
    parametro = base64.b64encode(cru).decode("ascii")
    juncao = "&" if "?" in config.idp_sso_url else "?"
    return config.idp_sso_url + juncao + urllib.parse.urlencode({"SAMLRequest": parametro})


def configuracao_da_biblioteca(config: Any) -> dict:
    """Settings do `python3-saml`, com os defaults perigosos sobrescritos.

    Por padrão a biblioteca monta a URL do ACS a partir de `http_host`/`https`
    da requisição e compara o `Destination` com ela -- exatamente a porta que
    fechamos no OIDC. Aqui tudo sai de `sso.base_url`.
    """
    if isinstance(config, dict):
        config = _configuracao_de(config)
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


def dados_da_requisicao(config: Any, post: Dict[str, str]) -> dict:
    """O dicionário que a biblioteca lê no lugar da requisição.

    Montado por NÓS a partir de `sso.base_url`: nenhum cabeçalho da requisição
    entra aqui, que é o que impede o cliente de escolher o `Destination` aceito.
    """
    if isinstance(config, dict):
        config = _configuracao_de(config)
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


def processa_resposta_saml(config: Any, saml_response: str,
                           id_pendente: str, agora: Optional[float] = None) -> str:
    """Valida a resposta do IdP e devolve o e-mail. Levanta FalhaDeSSO se algo falhar.

    A assinatura, o `Audience`, as janelas de tempo, o `Destination` e o
    `InResponseTo` são conferidos pela biblioteca -- é para isso que ela existe,
    e é por isso que ela é dependência: defender-se de XML Signature Wrapping
    exige amarrar a referência da assinatura ao elemento efetivamente lido, o
    que não se faz com regex nem com `xml.etree`.
    """
    if isinstance(config, dict):
        config = _configuracao_de(config)
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


def metadata_do_sp(config: Any) -> str:
    """XML de metadados que o operador entrega ao IdP.

    Servido apenas COM sessão: não há pressa nenhuma em publicá-lo sem login, e
    cada rota pública a mais é superfície a mais.
    """
    if isinstance(config, dict):
        config = _configuracao_de(config)
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


# ---------------------------------------------------------------------------
# Testes de Conexão (OIDC e SAML2)
# ---------------------------------------------------------------------------

def testar_conexao_oidc(issuer: str, client_id: str = "", client_secret: str = "",
                        base_url: str = "") -> Tuple[bool, str]:
    """Testa a conexão com o provedor OIDC consultando o documento de descoberta."""
    issuer = (issuer or "").strip().rstrip("/")
    if not issuer:
        return False, "O emissor (issuer) não foi informado."
    partes = urllib.parse.urlsplit(issuer)
    if partes.query or partes.fragment or not partes.netloc:
        return False, "O emissor deve ser uma URL válida sem consulta ou fragmento."
    if not _transporte_seguro(issuer):
        return False, "O emissor deve utilizar HTTPS (exceto no loopback)."
    limpa_cache_descoberta()
    try:
        doc = descobrir(issuer)
    except Exception as e:
        return False, f"Falha ao obter documento de descoberta: {e}"

    if str(doc.get("issuer", "")).rstrip("/") != issuer:
        return False, f"Emissor no documento ({doc.get('issuer')}) difere do configurado ({issuer})."

    for endpoint in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
        val = str(doc.get(endpoint) or "")
        if not val:
            return False, f"Documento de descoberta não contém '{endpoint}'."
        if not _transporte_seguro(val):
            return False, f"Endpoint '{endpoint}' não utiliza HTTPS."

    return True, "Conexão OIDC validada com sucesso! Endpoints encontrados."

testa_conexao_oidc = testar_conexao_oidc


def testar_conexao_saml(idp_entity_id: str, idp_sso_url: str, idp_cert: str,
                        base_url: str = "") -> Tuple[bool, str]:
    """Testa a configuração do IdP SAML2 e a geração de metadados do SP."""
    idp_entity_id = (idp_entity_id or "").strip()
    idp_sso_url = (idp_sso_url or "").strip()
    idp_cert = (idp_cert or "").strip()

    if not idp_entity_id:
        return False, "O Entity ID do IdP é obrigatório."
    if not idp_sso_url:
        return False, "A URL de SSO do IdP é obrigatória."
    if not _transporte_seguro(idp_sso_url):
        return False, "A URL do IdP SSO deve utilizar HTTPS (exceto no loopback)."
    if not idp_cert:
        return False, "O certificado X.509 do IdP é obrigatório."

    cert_limpo = idp_cert.replace("-----BEGIN CERTIFICATE-----", "").replace("-----END CERTIFICATE-----", "").strip()
    cert_limpo = "".join(cert_limpo.split())
    if len(cert_limpo) < 64:
        return False, "Certificado X.509 parece truncado ou inválido."

    try:
        base64.b64decode(cert_limpo, validate=True)
    except Exception:
        return False, "Certificado X.509 em formato inválido (base64 inválido)."

    if not saml_disponivel():
        return False, "A biblioteca python3-saml não está instalada nesta imagem."

    try:
        base = (base_url or "https://localhost").rstrip("/")
        cfg_teste = ConfiguracaoSSO(
            provedor="saml",
            saml_habilitado=True,
            base_url=base,
            idp_entity_id=idp_entity_id,
            idp_sso_url=idp_sso_url,
            idp_cert=cert_limpo,
            dominios=("exemplo.com",),
        )
        xml_sp = metadata_do_sp(cfg_teste)
        if not xml_sp or ("<md:EntityDescriptor" not in xml_sp and "<EntityDescriptor" not in xml_sp):
            return False, "Falha ao gerar metadados do Service Provider (SP)."
    except Exception as e:
        return False, f"Falha na validação SAML2: {e}"

    return True, "Parâmetros e certificado do SAML 2.0 validados com sucesso!"

testa_conexao_saml = testar_conexao_saml


# ---------------------------------------------------------------------------
# Ligação com as rotas deste painel
# ---------------------------------------------------------------------------

_grava_segredo_no_arquivo = grava_segredo

PROVEDORES = ("", "oidc", "saml", "both", "all")
ROTA_CALLBACK = ROTA_OIDC_CALLBACK


def caminho_do_segredo(base_dir: str) -> str:
    """O arquivo do segredo, ao lado da credencial de recuperação."""
    return os.path.join(base_dir or ".", ARQUIVO_DO_SEGREDO)


def segredo_vem_do_ambiente() -> bool:
    """A tela trava o campo quando o ambiente manda, como já faz com a senha."""
    return bool(segredo_do_ambiente())


def ler_segredo(caminho_ou_dir: str):
    """O segredo em vigor, ou vazio. Aceita arquivo ou diretório."""
    if not caminho_ou_dir:
        return ("", False) if caminho_ou_dir is not None else ""
    if os.path.isdir(caminho_ou_dir) or not os.path.basename(caminho_ou_dir).endswith(ARQUIVO_DO_SEGREDO):
        caminho = caminho_do_segredo(caminho_ou_dir) if os.path.isdir(caminho_ou_dir) else caminho_ou_dir
        return _le_segredo(caminho)[0]
    return _le_segredo(caminho_ou_dir)


def grava_segredo(caminho_ou_dir: str, valor: str) -> bool:
    """Grava o segredo 0600 no diretório ou arquivo dado. False quando o disco recusa."""
    if not caminho_ou_dir:
        return False
    if os.path.isdir(caminho_ou_dir) or not os.path.basename(caminho_ou_dir).endswith(ARQUIVO_DO_SEGREDO):
        caminho = caminho_do_segredo(caminho_ou_dir)
    else:
        caminho = caminho_ou_dir
    return _grava_segredo_no_arquivo(caminho, str(valor or "").strip())


def normaliza_base_url(url: str) -> str:
    return str(url or "").strip().rstrip("/")


def url_segura(url: str) -> bool:
    """Aceita `https` em qualquer lugar e `http` apenas no loopback."""
    return _transporte_seguro(url)


def base_url_valida(url: str) -> bool:
    """Origem pública EXATA: esquema e host, sem caminho, sem consulta."""
    if not url:
        return False
    partes = urllib.parse.urlsplit(str(url).strip())
    if partes.scheme not in ("http", "https"):
        return False
    if not partes.netloc:
        return False
    if partes.path not in ("", "/") or partes.query or partes.fragment:
        return False
    return url_segura(url)


def redirect_uri(config: Dict[str, object]) -> str:
    """SEMPRE da configuração, JAMAIS do cabeçalho `Host`."""
    return str(config.get("base_url") or "").rstrip("/") + ROTA_OIDC_CALLBACK


def nome_do_provedor(config: Optional[Dict[str, object]]) -> str:
    """Como o botão da tela de login chama o provedor."""
    if not config:
        return ""
    if config.get("oidc_issuer") or config.get("issuer"):
        origem = str(config.get("issuer") or config.get("oidc_issuer") or "")
        return urllib.parse.urlsplit(origem).hostname or origem
    if config.get("idp_sso_url") or config.get("idp_entity_id"):
        origem = str(config.get("idp_sso_url") or config.get("idp_entity_id") or "")
        return urllib.parse.urlsplit(origem).hostname or origem
    return ""


def ler_configuracao(prefs_path: str) -> Dict[str, str]:
    """Tudo o que a tela precisa mostrar. NUNCA inclui o segredo do cliente."""
    from .prefs import get_preference

    def ler(chave: str, padrao: str = "") -> str:
        return (get_preference(prefs_path, chave, padrao) or "").strip()

    prov = ler(CHAVE_PROVEDOR).lower()
    senha_hab = "1" if ler(CHAVE_SENHA_HABILITADA, "1") != "0" else "0"
    oidc_hab = "1" if ler(CHAVE_OIDC_HABILITADO, "1" if prov in ("oidc", "both", "all") else "0") == "1" else "0"
    saml_hab = "1" if ler(CHAVE_SAML_HABILITADO, "1" if prov in ("saml", "both", "all") else "0") == "1" else "0"

    base_url = ler(CHAVE_BASE_URL).rstrip("/")
    issuer = ler(CHAVE_OIDC_ISSUER).rstrip("/")
    client_id = ler(CHAVE_OIDC_CLIENT_ID)
    scopes = ler(CHAVE_OIDC_SCOPES) or ESCOPOS_PADRAO
    idp_entity = ler(CHAVE_SAML_IDP_ENTITY_ID)
    idp_sso = ler(CHAVE_SAML_IDP_SSO_URL)
    idp_cert = ler(CHAVE_SAML_IDP_CERT)

    return {
        "enabled": prov,
        "password_enabled": senha_hab,
        "oidc_enabled": oidc_hab,
        "saml_enabled": saml_hab,
        "base_url": base_url,
        "issuer": issuer,
        "oidc_issuer": issuer,
        "client_id": client_id,
        "oidc_client_id": client_id,
        "scopes": scopes,
        "oidc_scopes": scopes,
        "idp_entity_id": idp_entity,
        "saml_idp_entity_id": idp_entity,
        "idp_sso_url": idp_sso,
        "saml_idp_sso_url": idp_sso,
        "idp_cert": idp_cert,
        "saml_idp_cert": idp_cert,
        "allowed_domains": ler(CHAVE_DOMINIOS),
        "allowed_emails": ler(CHAVE_EMAILS),
        "updated_at": ler(CHAVE_ATUALIZADO_EM),
    }


ler_config = ler_configuracao


def grava_configuracao(prefs_path: str, campos: Dict[str, str]) -> bool:
    """Grava a configuração não sensível. O segredo tem caminho próprio."""
    senha_hab = "1" if str(campos.get("password_enabled", "1")).strip() in ("1", "true", "on", "yes") else "0"

    prov_in = str(campos.get("enabled", "")).strip().lower()
    if "oidc_enabled" in campos:
        oidc_hab = "1" if str(campos.get("oidc_enabled", "")).strip() in ("1", "true", "on", "yes") else "0"
    else:
        oidc_hab = "1" if prov_in in ("oidc", "both", "all") else "0"

    if "saml_enabled" in campos:
        saml_hab = "1" if str(campos.get("saml_enabled", "")).strip() in ("1", "true", "on", "yes") else "0"
    else:
        saml_hab = "1" if prov_in in ("saml", "both", "all") else "0"

    if prov_in == "":
        if "oidc_enabled" not in campos and "saml_enabled" not in campos:
            oidc_hab = "0"
            saml_hab = "0"

    if oidc_hab == "1" and saml_hab == "1":
        prov = "both"
    elif oidc_hab == "1":
        prov = "oidc"
    elif saml_hab == "1":
        prov = "saml"
    else:
        prov = ""

    idp_entity = (campos.get("idp_entity_id") or campos.get("saml_idp_entity_id") or "").strip()
    idp_sso = (campos.get("idp_sso_url") or campos.get("saml_idp_sso_url") or "").strip()
    idp_cert = (campos.get("idp_cert") or campos.get("saml_idp_cert") or "").strip()

    return gravar(prefs_path, {
        CHAVE_PROVEDOR: prov,
        CHAVE_SENHA_HABILITADA: senha_hab,
        CHAVE_OIDC_HABILITADO: oidc_hab,
        CHAVE_SAML_HABILITADO: saml_hab,
        CHAVE_BASE_URL: normaliza_base_url(campos.get("base_url", "")),
        CHAVE_OIDC_ISSUER: normaliza_base_url(campos.get("issuer", "") or campos.get("oidc_issuer", "")),
        CHAVE_OIDC_CLIENT_ID: (campos.get("client_id") or campos.get("oidc_client_id") or "").strip(),
        CHAVE_OIDC_SCOPES: (campos.get("scopes") or campos.get("oidc_scopes") or "").strip() or ESCOPOS_PADRAO,
        CHAVE_SAML_IDP_ENTITY_ID: idp_entity,
        CHAVE_SAML_IDP_SSO_URL: idp_sso,
        CHAVE_SAML_IDP_CERT: idp_cert,
        CHAVE_DOMINIOS: campos.get("allowed_domains", ""),
        CHAVE_EMAILS: campos.get("allowed_emails", ""),
    })


grava_config = grava_configuracao


def problemas_da_configuracao(campos: Dict[str, str], tem_segredo: bool) -> Tuple[str, ...]:
    """Chaves de tradução do que impede LIGAR o SSO OIDC."""
    problemas = []
    if not base_url_valida(campos.get("base_url", "")):
        problemas.append("sso.need_base_url")
    if not str(campos.get("issuer", "")).strip() or not url_segura(campos.get("issuer", "")):
        problemas.append("sso.need_issuer")
    if not str(campos.get("client_id", "")).strip():
        problemas.append("sso.need_client_id")
    if not tem_segredo:
        problemas.append("sso.need_secret")
    if not lista_de(campos.get("allowed_domains", "")) and not lista_de(
        campos.get("allowed_emails", "")
    ):
        problemas.append("sso.need_allowlist")
    return tuple(problemas)


def problemas_do_saml(campos: Dict[str, str]) -> Tuple[str, ...]:
    """Chaves de tradução do que impede LIGAR o SSO SAML2."""
    problemas = []
    if not base_url_valida(campos.get("base_url", "")):
        problemas.append("sso.need_base_url")
    if not str(campos.get("idp_entity_id", "")).strip():
        problemas.append("sso.save_refused_fields")
    if not str(campos.get("idp_sso_url", "")).strip() or not url_segura(campos.get("idp_sso_url", "")):
        problemas.append("sso.save_refused_fields")
    if not str(campos.get("idp_cert", "")).strip():
        problemas.append("sso.save_refused_fields")
    if not saml_disponivel():
        problemas.append("sso.save_refused_saml")
    if not lista_de(campos.get("allowed_domains", "")) and not lista_de(
        campos.get("allowed_emails", "")
    ):
        problemas.append("sso.need_allowlist")
    return tuple(problemas)


def configuracao_efetiva(prefs_path: str, base_dir: str) -> Optional[Dict[str, object]]:
    """A configuração que o fluxo usa, ou None quando o SSO não deve funcionar."""
    if desligado_por_ambiente():
        return None

    campos = ler_configuracao(prefs_path)
    prov = campos.get("enabled", "")
    oidc_ativo = campos.get("oidc_enabled") == "1" or prov in ("oidc", "both", "all")
    saml_ativo = campos.get("saml_enabled") == "1" or prov in ("saml", "both", "all")

    if not oidc_ativo and not saml_ativo:
        return None

    segredo = ler_segredo(base_dir) if base_dir else ""
    oidc_valido = bool(oidc_ativo and not problemas_da_configuracao(campos, bool(segredo)))
    saml_valido = bool(saml_ativo and not problemas_do_saml(campos))

    if not oidc_valido and not saml_valido:
        return None

    return {
        "enabled": prov,
        "password_enabled": campos.get("password_enabled") != "0",
        "oidc_enabled": oidc_valido,
        "saml_enabled": saml_valido,
        "base_url": normaliza_base_url(campos.get("base_url", "")),
        "issuer": normaliza_base_url(campos.get("issuer", "")),
        "oidc_issuer": normaliza_base_url(campos.get("issuer", "")),
        "client_id": campos.get("client_id", ""),
        "client_secret": segredo,
        "scopes": campos.get("scopes", "") or ESCOPOS_PADRAO,
        "idp_entity_id": campos.get("idp_entity_id", ""),
        "idp_sso_url": campos.get("idp_sso_url", ""),
        "idp_cert": campos.get("idp_cert", ""),
        "allowed_domains": campos.get("allowed_domains", ""),
        "allowed_emails": campos.get("allowed_emails", ""),
    }


def _para_bool(val: Any, padrao: bool = False) -> bool:
    if val is None:
        return padrao
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("1", "true", "on", "yes"):
        return True
    if s in ("0", "false", "off", "no", ""):
        return False
    return padrao


def _configuracao_de(config: Dict[str, object]) -> ConfiguracaoSSO:
    """O dicionário que as rotas carregam, no formato do núcleo."""
    def campo(*nomes: str) -> str:
        for nome in nomes:
            valor = str(config.get(nome) or "").strip()
            if valor:
                return valor
        return ""

    prov = str(config.get("enabled", "")).strip().lower()

    if "oidc_enabled" in config:
        oidc_hab = _para_bool(config.get("oidc_enabled"))
    else:
        oidc_hab = prov in ("oidc", "both", "all")

    if "saml_enabled" in config:
        saml_hab = _para_bool(config.get("saml_enabled"))
    else:
        saml_hab = prov in ("saml", "both", "all")

    if "password_enabled" in config:
        senha_hab = _para_bool(config.get("password_enabled"), padrao=True)
    else:
        senha_hab = True

    if prov == "" and "oidc_enabled" not in config and "saml_enabled" not in config:
        oidc_hab = False
        saml_hab = False

    return ConfiguracaoSSO(
        provedor=prov,
        oidc_habilitado=oidc_hab,
        saml_habilitado=saml_hab,
        senha_habilitada=senha_hab,
        base_url=campo("base_url").rstrip("/"),
        issuer=campo("issuer", "oidc_issuer").rstrip("/"),
        client_id=campo("client_id", "oidc_client_id"),
        escopos=campo("scopes", "oidc_scopes") or ESCOPOS_PADRAO,
        idp_entity_id=campo("idp_entity_id", "saml_idp_entity_id"),
        idp_sso_url=campo("idp_sso_url", "saml_idp_sso_url"),
        idp_cert=campo("idp_cert", "saml_idp_cert"),
        dominios=lista_de(campo("allowed_domains")),
        emails=lista_de(campo("allowed_emails")),
        tem_segredo=bool(config.get("client_secret")),
    )


def limpa_cache_descoberta() -> None:
    """Zera o documento memorizado. O emissor pode ter mudado na tela."""
    esquece_descobertas()


esquece_descoberta = limpa_cache_descoberta


def descobre(issuer: str, agora: Optional[float] = None) -> Optional[Dict[str, object]]:
    """O documento do emissor, ou None quando ele não responde. Levanta ErroDeSSO se o emissor for inseguro."""
    if not _transporte_seguro(issuer):
        raise FalhaDeSSO("o emissor precisa de HTTPS fora do loopback")
    try:
        return descobrir(issuer, agora=agora)
    except FalhaDeSSO:
        return None


def url_de_autorizacao(
    arg1: Any,
    arg2: Any,
    state: str,
    nonce: str,
    desafio_ou_verificador: str,
) -> str:
    """Para onde mandamos o navegador. O `redirect_uri` sai da configuração."""
    if isinstance(arg1, dict) and "authorization_endpoint" in arg1:
        doc = arg1
        cfg = _configuracao_de(arg2) if isinstance(arg2, dict) else arg2
        desafio = desafio_de(desafio_ou_verificador)
    elif isinstance(arg2, dict) and "authorization_endpoint" in arg2:
        doc = arg2
        cfg = _configuracao_de(arg1) if isinstance(arg1, dict) else arg1
        desafio = desafio_ou_verificador
    elif isinstance(arg1, ConfiguracaoSSO):
        cfg = arg1
        doc = arg2
        desafio = desafio_ou_verificador
    else:
        cfg = _configuracao_de(arg2) if isinstance(arg2, dict) else arg2
        doc = arg1
        desafio = desafio_ou_verificador

    destino = str(doc.get("authorization_endpoint") if isinstance(doc, dict) else getattr(doc, "authorization_endpoint", "") or "")
    juncao = "&" if "?" in destino else "?"
    return destino + juncao + urllib.parse.urlencode(
        parametros_de_autorizacao(cfg, state, nonce, desafio)
    )


def conclui_login(*, config: Dict[str, object], estado: Optional[Dict[str, str]],
                  parametros: Dict[str, str],
                  agora: Optional[float] = None) -> Tuple[str, str]:
    """Valida a volta do provedor. Devolve (email, motivo_da_recusa)."""
    alvo = _configuracao_de(config)
    try:
        if not estado or not estado.get("state"):
            raise FalhaDeSSO("cookie de estado ausente ou corrompido")
        if not mesmo_texto(str(parametros.get("state") or ""), str(estado["state"])):
            raise FalhaDeSSO("state diferente do gravado no cookie")
        if estado_ja_usado(str(estado["state"]), agora):
            raise FalhaDeSSO("state já gasto: volta repetida")
        if parametros.get("error"):
            raise FalhaDeSSO("o provedor devolveu erro na autorização")
        codigo = str(parametros.get("code") or "").strip()
        if not codigo:
            raise FalhaDeSSO("code ausente na volta do provedor")

        documento = descobrir(alvo.issuer, agora=agora)
        tokens = troca_o_code(
            documento, alvo, str(config.get("client_secret") or ""), codigo,
            str(estado.get("verificador") or ""),
        )
        payload = decodifica_payload(tokens["id_token"])
        confere_id_token(payload, alvo, str(estado.get("nonce") or ""), agora=agora)
        email = email_do_userinfo(busca_userinfo(documento, tokens["access_token"]), payload)
        if not email_autorizado(email, alvo):
            raise FalhaDeSSO("e-mail fora da lista de permissão")
    except FalhaDeSSO as erro:
        return "", erro.detalhe
    return email, ""


# Aliases para convergência de chamadas entre os painéis
def resolve_client_secret(caminho_ou_dir: str) -> str:
    """Retorna apenas o segredo como string, lido do arquivo ou diretório."""
    if not caminho_ou_dir:
        return ""
    if os.path.isdir(caminho_ou_dir):
        caminho = caminho_do_segredo(caminho_ou_dir)
    else:
        caminho = caminho_ou_dir
    return _le_segredo(caminho)[0]


def tem_client_secret(base_dir: str) -> bool:
    return bool(resolve_client_secret(base_dir))


grava_client_secret = grava_segredo


def allowlist_esta_vazia(config: Dict[str, object]) -> bool:
    return not lista_de(str(config.get("allowed_domains") or "")) and not lista_de(
        str(config.get("allowed_emails") or "")
    )


def oidc_esta_completo(config: Dict[str, object], arquivo_do_segredo: str) -> bool:
    c = _configuracao_de(config)
    c.tem_segredo = bool(resolve_client_secret(arquivo_do_segredo))
    return c.oidc_esta_ligado()


def provedor_ativo(config: Dict[str, object], arquivo_do_segredo: str) -> str:
    if desligado_por_ambiente():
        return ""
    if not config:
        return ""
    c = _configuracao_de(config)
    c.tem_segredo = bool(resolve_client_secret(arquivo_do_segredo))
    if c.oidc_esta_ligado() and c.saml_esta_ligado():
        return "both"
    if c.oidc_esta_ligado():
        return "oidc"
    if c.saml_esta_ligado():
        return "saml"
    return ""


def novo_desafio_pkce() -> Tuple[str, str]:
    verificador = novo_verificador()
    return verificador, desafio_de(verificador)


def consome_estado(state: str, agora: Optional[float] = None) -> bool:
    return not estado_ja_usado(state, agora)


def conclui_callback(
    config: Dict[str, object],
    arquivo_do_segredo: str,
    state_da_query: str,
    codigo: str,
    erro_da_query: str,
    cookie_de_estado: str,
    agora: Optional[float] = None,
) -> str:
    from . import sessao
    estado = sessao.ler_estado_sso(cookie_de_estado, agora=agora) if cookie_de_estado else None
    params = {"state": state_da_query, "code": codigo}
    if erro_da_query:
        params["error"] = erro_da_query
    email, motivo = conclui_login(config=config, estado=estado, parametros=params, agora=agora)
    if not email:
        raise FalhaDeSSO(motivo)
    return email


desligado_pelo_ambiente = desligado_por_ambiente
ErroDeSSO = FalhaDeSSO

