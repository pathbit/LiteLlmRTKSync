"""Painel deste sincronizador, renderizado inteiramente no servidor.

Este módulo cuida do transporte — rotas, autenticação, cabeçalhos e ações. Todo
o HTML vive em `render.py`, como nos projetos irmãos, porque foi a mistura das
duas coisas que fez o painel daqui divergir deles sem que ninguém percebesse.

Mesma postura dos irmãos, pelas mesmas razões:

- nada de JavaScript buscando dado: a página chega pronta, então não existe
  endpoint público servindo estado do proxy;
- cabeçalhos de segurança em **toda** resposta, inclusive no corpo do 401 — que
  é o que o navegador mostra quando se aperta ESC no diálogo do Basic Auth;
- nenhuma credencial aparece em corpo de resposta, log ou banner;
- POST de outra origem é recusado, porque o navegador anexa o Basic Auth
  sozinho num formulário de terceiro.
"""

import base64
import json
import re
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlencode, urlparse

from .config import Settings
from .cron import CronScheduler
from .i18n import DEFAULT_LANGUAGE, normalize_language, translate
from .identidade import NOME_DO_PRODUTO
from .logs import get_logger
from .gateway import fallback_combos
from .models import RegisteredModelRecord, VirtualKeyRecord, group_connections
from .prefs import get_preference, set_preference
from . import protecao, sessao, sso
from .render import render_dashboard, render_login_page, render_notice_page


# Erros de socket que significam apenas "o cliente desistiu antes de ler a
# resposta" — comportamento normal de health check, não falha do servidor.
CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Servidor multi-thread que não polui o log quando o cliente desconecta antes da hora."""

    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, CLIENT_DISCONNECT_ERRORS):
            return
        super().handle_error(request, client_address)


def strip_markup(text: str) -> str:
    """Tira as etiquetas de um texto do catálogo destinado a virar aviso puro."""
    return re.sub(r"<[^>]+>", "", text)


class DashboardHandler(BaseHTTPRequestHandler):
    settings: Settings = None
    engine: Any = None
    cron_scheduler: Any = None
    last_cycle: Dict[str, Any] = {}
    _lock = threading.Lock()

    server_version = NOME_DO_PRODUTO
    sys_version = ""

    def version_string(self) -> str:
        # Sem isto o BaseHTTPRequestHandler concatena server_version com
        # sys_version e serve "<produto> " -- com espaco sobrando.
        return self.server_version

    # -- cabeçalhos ---------------------------------------------------------

    SECURITY_HEADERS = (
        ("Referrer-Policy", "no-referrer"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        (
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com data:; "
            # As bandeiras do seletor de idioma são SVG que o CSS do flag-icons
            # busca no mesmo CDN. Sem esta origem elas simplesmente não
            # aparecem, e não há erro visível na tela para denunciar a falta.
            "img-src 'self' data: https://cdn.jsdelivr.net; "
            "connect-src 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'",
        ),
    )

    def end_headers(self):
        """Injeta os cabeçalhos de segurança antes de fechar o bloco.

        Aqui e não na rota: o 401 e a página de aviso também são HTML que o
        navegador renderiza, e emiti-los só na página principal deixava
        justamente essas duas sem proteção alguma.
        """
        already_sent = set()
        for line in getattr(self, "_headers_buffer", []) or []:
            try:
                text = line.decode("latin-1", "ignore")
            except Exception:
                continue
            if ":" in text:
                already_sent.add(text.split(":", 1)[0].strip().lower())
        for name, value in self.SECURITY_HEADERS:
            if name.lower() not in already_sent:
                self.send_header(name, value)
        super().end_headers()

    def log_message(self, format, *args):
        # O log de acesso do http.server escreve em stderr sem passar pelo
        # logger, e carrega a linha de requisição inteira. Silenciado.
        return

    def write_body(self, body: bytes) -> None:
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # O navegador fechou antes de ler. Não é erro do servidor, e deixar
            # subir enchia o log de traceback a cada refresh cancelado.
            pass

    # -- autenticação -------------------------------------------------------

    def require_auth(self) -> bool:
        # Duas portas, e elas NAO servem ao mesmo visitante.
        #
        # O cookie e a porta do navegador, e e a unica que tem tranca do lado de
        # dentro: "Sair" apaga o cookie e acabou. O Basic Auth nao tem logout --
        # o navegador guarda a credencial e a reenvia sozinho ate a janela
        # fechar, e nao existe cabecalho que mande ele esquecer. Enquanto a
        # navegacao aceitava Basic, o botao Sair apagava o cookie e a proxima
        # visita entrava de novo pela outra porta: o botao mentia.
        #
        # Por isso quem pede HTML (um navegador) precisa de SESSAO, e so. Quem
        # nao pede HTML -- curl, script, monitoramento -- continua com Basic
        # Auth, que e o esquema que essas ferramentas sabem usar sem guardar
        # estado, e para as quais "sair" nao quer dizer nada.
        usuario_da_sessao = sessao.usuario_da_sessao(
            sessao.ler_do_cabecalho(self.headers.get("Cookie", ""))
        )
        if usuario_da_sessao:
            self.authenticated_user = usuario_da_sessao
            return True

        if "text/html" in self.headers.get("Accept", ""):
            lang = self.resolve_language()
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/login")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                raw = base64.b64decode(header[6:]).decode("utf-8")
                user, _, password = raw.partition(":")
            except Exception:
                user = password = ""
            if self.settings and self.settings.verify_credentials(user, password):
                # Só o nome; a senha morre aqui. O rodapé mostra quem entrou, e
                # guardar o par inteiro deixaria a senha ao alcance de qualquer
                # trecho de renderização.
                self.authenticated_user = user
                return True

        lang = self.resolve_language()

        # Quem pediu HTML e um navegador: mandamos para o formulario, que e
        # pagina nossa -- traduzida, com a cara do painel e com logout. O 401
        # com WWW-Authenticate fica para quem NAO pediu HTML (curl, scripts,
        # monitoramento), que e quem sabe responder a ele.
        if "text/html" in self.headers.get("Accept", "") and urlparse(self.path).path != "/login":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/login")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        body = render_notice_page(
            translate("auth.required", lang),
            translate("auth.required_body", lang),
        )
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", f'Basic realm="{NOME_DO_PRODUTO}"')
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(body)
        return False

    def is_same_origin(self) -> bool:
        """Recusa POST vindo de outra origem.

        O navegador anexa o Basic Auth sozinho num formulário de terceiro, então
        sem esta barreira uma página maliciosa trocaria a senha do painel. O
        Origin decide primeiro; o Sec-Fetch-Site só entra quando não há Origin.
        """
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin", "")
        if origin and origin != "null":
            return urlparse(origin).netloc == host
        fetch_site = self.headers.get("Sec-Fetch-Site", "")
        if fetch_site:
            return fetch_site in ("same-origin", "none")
        # curl e scripts não têm sessão para sequestrar.
        return True

    # -- preferências -------------------------------------------------------

    def prefs_path(self) -> str:
        """Banco de preferências próprio do sincronizador, nunca o do proxy."""
        return self.settings.get_prefs_path() if self.settings else ""

    def resolve_language(self) -> str:
        """Idioma em vigor: preferência salva no SQLite, senão o padrão (inglês)."""
        return normalize_language(
            get_preference(self.prefs_path(), "language", DEFAULT_LANGUAGE)
        )

    # -- rotas --------------------------------------------------------------

    def configuracao_sso(self) -> "sso.ConfiguracaoSSO":
        """Configuracao do acesso federado, lida uma vez por requisicao.

        A leitura e barata mas nao e de graca -- sao onze chaves no SQLite -- e
        o mesmo pedido a consulta na autenticacao, na tela e no despacho.
        """
        guardada = getattr(self, "_configuracao_sso", None)
        if guardada is None:
            if self.settings:
                guardada = sso.carregar(
                    self.prefs_path(), self.settings.get_sso_secret_path()
                )
            else:
                guardada = sso.ConfiguracaoSSO(
                    desligado_no_ambiente=sso.desligado_por_ambiente()
                )
            self._configuracao_sso = guardada
        return guardada

    def serve_login_page(self, erro: str = "") -> None:
        """Formulario de entrada: a porta do navegador para o painel."""
        lang = self.resolve_language()
        # O desafio so entra depois de algumas falhas: quem acerta de primeira
        # nunca o ve, e quem insiste passa a pagar CPU por tentativa.
        endereco = protecao.endereco_do_cliente(self.client_address)
        desafio = protecao.novo_desafio() if protecao.precisa_de_desafio(endereco) else ""
        dificuldade = protecao.dificuldade_para(endereco)
        body = render_login_page(lang, erro, desafio, dificuldade, self.configuracao_sso())
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(body)

    def handle_login(self) -> None:
        """Valida a credencial do formulario e emite o cookie de sessao."""
        endereco = protecao.endereco_do_cliente(self.client_address)

        # Teto por janela: o que para o script que tenta mil senhas por minuto.
        pode, espere = protecao.registra_tentativa(endereco)
        if not pode:
            self.responde_429(espere)
            return

        length = int(self.headers.get("Content-Length", 0))
        corpo = self.rfile.read(length) if length > 0 else b""
        campos = parse_qs(corpo.decode("utf-8", "replace"))
        usuario = (campos.get("usuario") or [""])[0]
        senha = (campos.get("senha") or [""])[0]

        # Depois de algumas falhas, o formulario so e aceito com a prova de
        # trabalho resolvida. Custa CPU para quem tenta em massa e e instantanea
        # de conferir aqui.
        if protecao.precisa_de_desafio(endereco):
            desafio = (campos.get("desafio") or [""])[0]
            resposta = (campos.get("resposta") or [""])[0]
            if not protecao.resposta_confere(
                desafio, resposta, protecao.dificuldade_para(endereco)
            ):
                protecao.anota_falha(endereco)
                self.serve_login_page(translate("auth.login_failed", self.resolve_language()))
                return

        # A espera cresce a cada falha seguida. E do lado do servidor: nao ha
        # nada no cliente para desligar.
        atraso = protecao.espera_por_falhas(endereco)
        if atraso:
            time.sleep(atraso)

        if not self.settings or not self.settings.verify_credentials(usuario, senha):
            # Mensagem unica para usuario errado e senha errada: distinguir os
            # dois conta a quem tenta qual metade ja acertou.
            protecao.anota_falha(endereco)
            self.serve_login_page(translate("auth.login_failed", self.resolve_language()))
            return

        protecao.limpa_apos_sucesso(endereco)
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", sessao.cabecalho_para_gravar(sessao.emitir(usuario)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def responde_429(self, espere_segundos: int) -> None:
        """Pedidos demais: 429 com Retry-After, que e o que um cliente correto le."""
        lang = self.resolve_language()
        payload = render_notice_page(
            translate("auth.too_many", lang),
            translate("auth.too_many_body", lang, seconds=espere_segundos),
        )
        self.send_response(HTTPStatus.TOO_MANY_REQUESTS)
        self.send_header("Retry-After", str(espere_segundos))
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(payload)

    def handle_logout(self) -> None:
        """Apaga o cookie. O Basic Auth nao tem equivalente disso."""
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", "/login")
        self.send_header("Set-Cookie", sessao.cabecalho_para_apagar())
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()


    # -- acesso federado ----------------------------------------------------
    #
    # Quatro rotas publicas a mais, e todas passam pelo MESMO teto por endereco
    # do formulario de login: a ida ao provedor e a volta dele acontecem sem
    # sessao -- e a sessao que elas existem para criar.

    def anota_falha_de_sso(self, erro: Exception) -> None:
        """O motivo vai para o log interno; a tela recebe a mensagem generica.

        Nada aqui carrega credencial: nem o `code`, nem os tokens, nem o segredo
        do cliente, nem a `SAMLResponse`. O que se registra e o passo que falhou.
        """
        get_logger().warning("[SSO] fluxo recusado: %s", erro)

    def recusa_sso(self) -> None:
        """Mensagem UNICA para toda falha do fluxo federado.

        Distinguir "state trocado" de "e-mail fora da lista" conta ao atacante
        em que ponto do fluxo ele parou. O formulario local vem junto: quem tem
        senha entra mesmo com o provedor recusando.
        """
        lang = self.resolve_language()
        corpo = render_login_page(
            lang, translate("sso.failed", lang), sso=self.configuracao_sso()
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Set-Cookie", sessao.cabecalho_para_apagar_estado())
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(corpo)

    def pousa_sessao_federada(self, email: str) -> None:
        """Emite o MESMO cookie assinado do formulario e aterrissa em "/".

        NAO e um 302: no Chrome, uma cadeia de redirecionamento iniciada em
        outro site nao carrega o cookie `SameSite=Strict` no salto seguinte, e o
        operador cairia em `/login` com uma sessao valida no bolso.

        O destino e SEMPRE "/". Nenhum parametro de retorno vira destino, aqui
        ou em qualquer lugar: isso seria redirecionamento aberto autenticado.
        """
        lang = self.resolve_language()
        corpo = render_notice_page(
            translate("sso.entering", lang),
            translate("sso.entering_body", lang),
            meta_refresh="0;url=/",
        )
        # O prefixo "sso:" distingue no rodape e no log quem entrou pela porta
        # federada, sem inventar uma segunda forma de sessao.
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header(
            "Set-Cookie", sessao.cabecalho_para_gravar(sessao.emitir("sso:" + email))
        )
        self.send_header("Set-Cookie", sessao.cabecalho_para_apagar_estado())
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(corpo)

    def freio_do_sso(self) -> bool:
        """Aplica o teto por endereco. Devolve True quando ja respondeu 429."""
        endereco = protecao.endereco_do_cliente(self.client_address)
        pode, espere = protecao.registra_tentativa(endereco)
        if not pode:
            self.responde_429(espere)
            return True
        return False

    def inicia_oidc(self) -> None:
        """Ida ao provedor, com PKCE S256 e o estado num cookie assinado."""
        if self.freio_do_sso():
            return
        config = self.configuracao_sso()
        if not config.esta_ligado() or config.provedor != "oidc":
            self.recusa_sso()
            return
        try:
            documento = sso.descobrir(config.issuer)
        except sso.FalhaDeSSO as erro:
            # Descoberta quebrada nao trava o login local: a tela volta com o
            # formulario de sempre.
            self.anota_falha_de_sso(erro)
            self.recusa_sso()
            return

        state = sso.novo_segredo_de_fluxo()
        nonce = sso.novo_segredo_de_fluxo()
        verificador = sso.novo_verificador()
        self.send_response(HTTPStatus.FOUND)
        self.send_header(
            "Location", sso.url_de_autorizacao(documento, config, state, nonce, verificador)
        )
        self.send_header(
            "Set-Cookie",
            sessao.cabecalho_para_gravar_estado(
                sessao.emitir_estado_sso(state, nonce, verificador)
            ),
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def recebe_oidc(self) -> None:
        """Volta do provedor. Valida TUDO antes de emitir sessao."""
        if self.freio_do_sso():
            return
        config = self.configuracao_sso()
        if not config.esta_ligado() or config.provedor != "oidc":
            self.recusa_sso()
            return

        consulta = parse_qs(urlparse(self.path).query)
        try:
            guardado = sessao.ler_estado_sso(
                sessao.ler_estado_do_cabecalho(self.headers.get("Cookie", ""))
            )
            if not guardado:
                raise sso.FalhaDeSSO("volta sem cookie de estado integro")
            # Uso unico, e consumido ANTES de qualquer outra conferencia.
            if sso.estado_ja_usado(guardado["state"]):
                raise sso.FalhaDeSSO("estado ja gasto: volta repetida")
            if not sso.mesmo_texto((consulta.get("state") or [""])[0], guardado["state"]):
                raise sso.FalhaDeSSO("state da query difere do state do cookie")
            if consulta.get("error"):
                raise sso.FalhaDeSSO("o provedor devolveu erro na volta")
            code = (consulta.get("code") or [""])[0]
            if not code:
                raise sso.FalhaDeSSO("volta sem code")

            documento = sso.descobrir(config.issuer)
            segredo, _ = sso.ler_segredo(self.settings.get_sso_secret_path())
            tokens = sso.troca_o_code(
                documento, config, segredo, code, guardado["verificador"]
            )
            payload = sso.decodifica_payload(tokens["id_token"])
            sso.confere_id_token(payload, config, guardado["nonce"])
            userinfo = sso.busca_userinfo(documento, tokens["access_token"])
            email = sso.email_do_userinfo(userinfo, payload)
            if not sso.email_autorizado(email, config):
                raise sso.FalhaDeSSO("e-mail fora da lista de autorizados")
        except sso.FalhaDeSSO as erro:
            self.anota_falha_de_sso(erro)
            self.recusa_sso()
            return

        protecao.limpa_apos_sucesso(protecao.endereco_do_cliente(self.client_address))
        self.pousa_sessao_federada(email)

    def inicia_saml(self) -> None:
        """AuthnRequest por HTTP-Redirect binding, com o ID guardado no servidor."""
        if self.freio_do_sso():
            return
        config = self.configuracao_sso()
        if not config.esta_ligado() or config.provedor != "saml":
            self.recusa_sso()
            return
        identificador = sso.novo_id_de_requisicao()
        # No SERVIDOR, e nao em cookie: o ACS e um POST vindo de outro site, e
        # `SameSite=Lax` nao viaja em POST cross-site.
        sso.registra_pendente(identificador)
        self.send_response(HTTPStatus.FOUND)
        self.send_header(
            "Location",
            sso.url_de_ida_saml(config, sso.monta_authn_request(config, identificador)),
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def recebe_saml(self) -> None:
        """ACS: recebe a assercao do provedor e valida antes de emitir sessao.

        Nao passa pela guarda de mesma origem, e nao precisa: por definicao este
        POST vem de outro site, e a autenticidade vem da assinatura XML e do
        `InResponseTo`, nao do cabecalho Origin.
        """
        if self.freio_do_sso():
            return
        config = self.configuracao_sso()
        if not config.esta_ligado() or config.provedor != "saml":
            self.recusa_sso()
            return

        # O corpo e lido AQUI, dentro do handler -- nunca no despacho, que roda
        # antes de qualquer decisao sobre quem esta do outro lado.
        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho) if tamanho else b""
        campos = parse_qs(corpo.decode("utf-8", errors="replace"))

        try:
            resposta = (campos.get("SAMLResponse") or [""])[0]
            if not resposta:
                raise sso.FalhaDeSSO("POST no ACS sem SAMLResponse")
            identificador = sso.in_response_to(resposta)
            if not sso.consome_pendente(identificador):
                raise sso.FalhaDeSSO("InResponseTo desconhecido, gasto ou fora do prazo")
            email = sso.processa_resposta_saml(config, resposta, identificador)
            if not sso.email_autorizado(email, config):
                raise sso.FalhaDeSSO("e-mail fora da lista de autorizados")
        except sso.FalhaDeSSO as erro:
            self.anota_falha_de_sso(erro)
            self.recusa_sso()
            return

        protecao.limpa_apos_sucesso(protecao.endereco_do_cliente(self.client_address))
        self.pousa_sessao_federada(email)

    def serve_saml_metadata(self) -> None:
        """Descricao do servico, servida SO com sessao.

        Nao aumenta a lista de rotas publicas: o operador baixa o arquivo
        autenticado e o entrega ao provedor, e nao ha pressa nenhuma nisso.
        """
        try:
            corpo = sso.metadata_do_sp(self.configuracao_sso()).encode("utf-8")
        except sso.FalhaDeSSO:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(corpo)

    # Rotas que este servidor conhece. Serve para uma so decisao, tomada ANTES
    # de exigir sessao: o que nao esta aqui e 404, e nao um convite a fazer
    # login para depois descobrir que a pagina nunca existiu.
    #
    # Rota REAL e protegida continua mandando para /login -- e a diferenca entre
    # "voce precisa entrar" e "isso nao existe", que sao respostas diferentes
    # para perguntas diferentes.
    ROTAS_CONHECIDAS = {
        "/", "/index.html", "/healthz", "/login", "/logout", "/robots.txt",
        "/favicon.ico", "/credenciais-atualizadas", "/logs",
    }
    PREFIXOS_CONHECIDOS = ("/api/", "/acoes/")

    def rota_existe(self, caminho: str) -> bool:
        if caminho in self.ROTAS_CONHECIDAS or caminho.startswith(self.PREFIXOS_CONHECIDOS):
            return True
        # SEM CONFIGURACAO, NADA MUDA: as rotas do acesso federado so existem
        # quando ha provedor configurado E ligado. Desligado, elas devolvem 404
        # pelo mesmo caminho de qualquer outra rota que nunca existiu.
        return caminho.startswith("/sso/") and self.configuracao_sso().esta_ligado()

    def recusa_rota_desconhecida(self, caminho: str) -> bool:
        """Devolve True e responde 404 quando a rota nao existe neste servidor."""
        if self.rota_existe(caminho):
            return False
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        return True

    def do_GET(self):
        # Uma leitura de configuracao por requisicao; a conexao pode ser
        # reaproveitada, e uma configuracao salva no pedido anterior tem de
        # valer no seguinte.
        self._configuracao_sso = None
        path = urlparse(self.path).path
        if self.recusa_rota_desconhecida(path):
            return
        if path == "/healthz":
            self.serve_healthz()
            return
        # A pagina de login e publica por definicao: exigir sessao para exibir o
        # formulario que cria a sessao seria um circulo fechado.
        # Publico de proposito, e servido antes da sessao: um rastreador nao
        # tem credencial, e a unica forma de ele ler a regra e ela nao exigir
        # uma. O painel nao deve aparecer em indice de busca nenhum.
        if path == "/robots.txt":
            corpo = b"User-agent: *\nDisallow: /\n"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.write_body(corpo)
            return

        if path == "/login":
            self.serve_login_page()
            return
        # As tres rotas de IDA e VOLTA do provedor de identidade. Publicas
        # porque e a sessao que elas existem para criar -- e porque quem as
        # chama e o provedor, que nao tem cookie nosso. Todas passam pelo mesmo
        # teto por endereco do formulario.
        if path == "/sso/oidc/iniciar":
            self.inicia_oidc()
            return
        if path == "/sso/oidc/callback":
            self.recebe_oidc()
            return
        if path == "/sso/saml/iniciar":
            self.inicia_saml()
            return
        # Servida ANTES do require_auth de propósito: o navegador ainda está com
        # a senha antiga neste instante, e exigir autenticação aqui daria um 401
        # cru exatamente depois de a troca ter dado certo.
        if path == "/credenciais-atualizadas":
            lang = self.resolve_language()
            self.respond_html(
                render_notice_page(
                    translate("auth.updated_title", lang),
                    translate("auth.updated_body", lang),
                    translate("auth.updated_link", lang),
                )
            )
            return

        if not self.require_auth():
            return

        if path in ("/", "/index.html"):
            self.serve_dashboard(parse_qs(urlparse(self.path).query))
        elif path == "/api/status":
            self.serve_status_json()
        elif path == "/api/cron-status":
            self.serve_cron_status_json()
        elif path == "/sso/saml/metadata":
            # Protegida de proposito: ver serve_saml_metadata.
            self.serve_saml_metadata()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Rota inexistente")

    def do_POST(self):
        self._configuracao_sso = None
        rota_inicial = urlparse(self.path).path
        if rota_inicial == "/login":
            self.handle_login()
            return
        if rota_inicial == "/logout":
            self.handle_logout()
            return
        # O ACS e disparado pelo PROVEDOR, de outra origem e sem sessao ainda:
        # e o POST que cria a sessao. A guarda de mesma origem o recusaria
        # sempre, e ele nao depende dela -- a autenticidade vem da assinatura
        # XML e do InResponseTo.
        if rota_inicial == "/sso/saml/acs":
            self.recebe_saml()
            return

        if not self.require_auth():
            return
        if not self.is_same_origin():
            # Redireciona com aviso em vez de devolver 403 cru: o operador
            # precisa entender o que houve, e um 403 na tela depois de trocar a
            # senha parece um defeito.
            self.redirect_to_dashboard(
                "danger", translate("security.cross_origin", self.resolve_language())
            )
            return

        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        fields = parse_qs(body.decode("utf-8", errors="replace"))

        if path == "/acoes/atualizar":
            # Só recarrega a página, como nos irmãos. Quem roda um ciclo é
            # "Sincronizar agora", que aponta para /acoes/cron: misturar as duas
            # ações num botão só fazia cada atualização de tela custar uma
            # varredura inteira no proxy.
            self.redirect_to_dashboard("info", translate("action.refreshed", self.resolve_language()))
        elif path == "/acoes/cron":
            self.handle_cron_run()
        elif path == "/acoes/idioma":
            self.handle_language(fields)
        elif path == "/acoes/testar-gateway":
            self.handle_test_proxy()
        elif path == "/acoes/credenciais":
            self.handle_credentials(fields)
        elif path == "/acoes/sso":
            self.handle_sso(fields)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Ação inexistente")

    # -- ações --------------------------------------------------------------

    @classmethod
    def execute_cycle(cls) -> Dict[str, Any]:
        """Roda um ciclo e guarda o resultado como o último estado conhecido.

        É esta função — e não `engine.sync_all` — que o agendador recebe como
        callback: o ciclo do cron precisa alimentar a mesma memória que a tela
        lê, senão o agendador trabalha e a página continua mostrando o resultado
        do primeiro ciclo para sempre.
        """
        with cls._lock:
            result = cls.engine.sync_all() if cls.engine else {}
            cls.last_cycle = result
            return result

    def run_cycle(self) -> Dict[str, Any]:
        return DashboardHandler.execute_cycle()

    def handle_cron_run(self) -> None:
        """Dispara o agendador agora, registrando a execução no histórico dele.

        É a ÚNICA rota que roda um ciclo sob demanda, como nos irmãos. Antes
        havia também `/acoes/sincronizar`, que fazia exatamente o mesmo trabalho
        por fora do agendador: dois botões para a mesma ação, e o ciclo disparado
        pelo primeiro não aparecia no histórico que a tela mostra.
        """
        lang = self.resolve_language()
        if not self.cron_scheduler:
            self.redirect_to_dashboard("warning", translate("cron.unavailable", lang))
            return
        try:
            entry = self.cron_scheduler.trigger_now() or {}
        except Exception as e:
            self.redirect_to_dashboard("danger", translate("cron.failed", lang, error=e))
            return
        self.redirect_to_dashboard(
            "success" if entry.get("success", True) else "warning",
            translate(
                "action.cron_ran",
                lang,
                duration=entry.get("durationMs", 0),
                inspected=entry.get("totalInspected", 0),
                findings=entry.get("findingsCount", 0),
            ),
        )

    def handle_language(self, fields: Dict[str, List[str]]) -> None:
        chosen = normalize_language((fields.get("lang", [""])[0] or "").strip())
        set_preference(self.prefs_path(), "language", chosen)
        # Volta para a raiz limpa: repetir o aviso da ação anterior depois de
        # trocar de idioma o mostraria no idioma antigo.
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def handle_test_proxy(self) -> None:
        online, _ = self.probe_proxy()
        lang = self.resolve_language()
        self.redirect_to_dashboard(
            "success" if online else "danger",
            f'{translate("gateway.title", lang)}: '
            f'{"ONLINE" if online else translate("gateway.no_response", lang)}',
        )

    def handle_credentials(self, fields: Dict[str, List[str]]) -> None:
        user = (fields.get("user", [""])[0] or "").strip()
        password = (fields.get("password", [""])[0] or "").strip()
        lang = self.resolve_language()

        problems = self.settings.check_password_strength(password) if self.settings else []
        if problems:
            # Todas as regras violadas de uma vez: uma por tentativa faria o
            # operador descobrir a política aos poucos.
            self.redirect_to_dashboard("danger", " ".join(translate(key, lang) for key in problems))
            return
        if self.settings and self.settings.dashboard_auth_from_env:
            # O mesmo texto do modal, sem a marcação: o aviso é escapado antes
            # de ir para a tela, e as etiquetas <code> apareceriam cruas ali.
            self.redirect_to_dashboard("warning", strip_markup(translate("auth.env_managed", lang)))
            return
        if self.settings and self.settings.update_auth_credentials(user, password):
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/credenciais-atualizadas")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.redirect_to_dashboard("danger", translate("auth.save_failed", lang))

    def handle_sso(self, fields: Dict[str, List[str]]) -> None:
        """Grava a configuracao do acesso federado.

        Tres trancas, e as tres sao necessarias: sessao (do `do_POST`), guarda
        de mesma origem (idem) e a SENHA LOCAL ATUAL, pedida aqui. A terceira
        existe porque quem sequestra uma sessao de oito horas poderia apontar o
        painel para um provedor hostil e se pôr na lista de autorizados --
        persistencia permanente ganha com uma sessao roubada.
        """
        lang = self.resolve_language()

        def campo(nome: str) -> str:
            return (fields.get(nome, [""])[0] or "").strip()

        if not self.settings or not self.settings.verify_credentials(
            campo("usuario"), (fields.get("senha", [""])[0] or "")
        ):
            self.redirect_to_dashboard("danger", translate("sso.save_refused_password", lang))
            return

        caminho_do_segredo = self.settings.get_sso_secret_path()
        escolhido = campo("enabled").lower()
        if campo("desligar") or not escolhido:
            # Desligar NAO apaga o que estava configurado: o operador volta a
            # ligar sem redigitar o provedor inteiro.
            sso.gravar(self.prefs_path(), {sso.CHAVE_PROVEDOR: ""})
            self.redirect_to_dashboard("success", translate("sso.turned_off", lang))
            return

        base_url = campo("base_url").rstrip("/")
        dominios = campo("allowed_domains")
        emails = campo("allowed_emails")
        # Allowlist OBRIGATORIA: o painel recusa LIGAR o acesso federado com a
        # lista vazia, porque lista vazia significa "toda conta do provedor".
        if not sso.lista_de(dominios) and not sso.lista_de(emails):
            self.redirect_to_dashboard("danger", translate("sso.save_refused_allowlist", lang))
            return

        comum = {
            sso.CHAVE_BASE_URL: base_url,
            sso.CHAVE_DOMINIOS: dominios,
            sso.CHAVE_EMAILS: emails,
        }

        if escolhido == "oidc":
            issuer = campo("issuer").rstrip("/")
            client_id = campo("client_id")
            if not base_url or not issuer or not client_id:
                self.redirect_to_dashboard("danger", translate("sso.save_refused_fields", lang))
                return
            # Campo em branco MANTEM o segredo anterior. Um campo que nunca
            # reexibe o valor e limpo a cada abertura da tela; apagar o segredo
            # por causa disso seria desligar o SSO sem ninguem pedir.
            novo_segredo = fields.get("client_secret", [""])[0] or ""
            atual, do_ambiente = sso.ler_segredo(caminho_do_segredo)
            if novo_segredo and not do_ambiente:
                if not sso.grava_segredo(caminho_do_segredo, novo_segredo):
                    self.redirect_to_dashboard(
                        "danger", translate("sso.save_refused_secret", lang)
                    )
                    return
            elif not atual:
                self.redirect_to_dashboard("danger", translate("sso.save_refused_secret", lang))
                return
            comum.update({
                sso.CHAVE_PROVEDOR: "oidc",
                sso.CHAVE_OIDC_ISSUER: issuer,
                sso.CHAVE_OIDC_CLIENT_ID: client_id,
                sso.CHAVE_OIDC_SCOPES: campo("scopes") or sso.ESCOPOS_PADRAO,
            })
        elif escolhido == "saml":
            if not sso.saml_disponivel():
                self.redirect_to_dashboard("danger", translate("sso.save_refused_saml", lang))
                return
            entity_id = campo("idp_entity_id")
            sso_url = campo("idp_sso_url")
            certificado = campo("idp_cert")
            if not base_url or not entity_id or not sso_url or not certificado:
                self.redirect_to_dashboard("danger", translate("sso.save_refused_fields", lang))
                return
            comum.update({
                sso.CHAVE_PROVEDOR: "saml",
                sso.CHAVE_SAML_IDP_ENTITY_ID: entity_id,
                sso.CHAVE_SAML_IDP_SSO_URL: sso_url,
                sso.CHAVE_SAML_IDP_CERT: certificado,
            })
        else:
            self.redirect_to_dashboard("danger", translate("sso.save_refused_fields", lang))
            return

        if not sso.gravar(self.prefs_path(), comum):
            self.redirect_to_dashboard("danger", translate("auth.save_failed", lang))
            return
        # A descoberta guardada era do issuer antigo.
        sso.esquece_descobertas()
        self.redirect_to_dashboard("success", translate("sso.saved", lang))

    # -- respostas ----------------------------------------------------------

    def redirect_to_dashboard(self, tone: str, message: str) -> None:
        """Redireciona para a página com uma mensagem de resultado (POST-Redirect-GET)."""
        query = urlencode({"aviso": message, "tom": tone})
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", f"/?{query}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def respond_html(self, body: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.write_body(body)

    def serve_healthz(self) -> None:
        proxy_ok = bool(self.engine and self.engine.client.health())
        if proxy_ok:
            status, payload = HTTPStatus.OK, b"OK"
        else:
            status, payload = HTTPStatus.SERVICE_UNAVAILABLE, b"LITELLM_UNREACHABLE"
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.write_body(payload)

    def serve_status_json(self) -> None:
        """Projeção explícita. Nenhum token, nenhuma chave de provedor."""
        state = DashboardHandler.last_cycle or {}
        payload = json.dumps(
            {
                "timestamp": state.get("timestamp"),
                "keys": state.get("keys", 0),
                "teams": state.get("teams", 0),
                "models": state.get("models", 0),
                "expiring": state.get("expiring", 0),
                "invalidCredentials": state.get("invalid_credentials", 0),
                "limitFindings": state.get("limit_findings", 0),
                "summary": state.get("summary", {}),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.write_body(payload)

    def serve_cron_status_json(self) -> None:
        """Estado do agendador. O histórico só traz contagens e ações, nunca credencial."""
        cron = self.cron_scheduler.get_status() if self.cron_scheduler else {"active": False}
        payload = json.dumps(cron, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.write_body(payload)

    # -- renderização -------------------------------------------------------

    def probe_proxy(self):
        """Sonda o proxy e devolve (respondeu, latência em ms).

        A latência é medida em volta da chamada de liveness porque é a única
        medida honesta do custo de falar com o proxy: um número guardado do
        ciclo anterior descreveria outro instante.
        """
        started = time.monotonic()
        online = False
        if self.engine:
            try:
                online = bool(self.engine.client.health())
            except Exception:
                online = False
        return online, int((time.monotonic() - started) * 1000)

    def collect_dashboard_state(self) -> Dict[str, Any]:
        """Lê tudo o que a página precisa. Roda no servidor: a master key nunca sai daqui."""
        state = DashboardHandler.last_cycle or self.run_cycle()

        keys: List[Any] = []
        models: List[Any] = []
        combos: List[Dict[str, Any]] = []
        team_aliases: Dict[str, str] = {}
        if self.engine:
            try:
                keys = [VirtualKeyRecord(k) for k in self.engine.client.list_keys()]
            except Exception:
                keys = []
            try:
                models = [RegisteredModelRecord(m) for m in self.engine.client.list_models()]
            except Exception:
                models = []
            # O apelido do time NÃO vem no /key/list (o campo existe na
            # serialização e chega sempre nulo). A única fonte é o /team/list,
            # e é por isso que esta leitura existe: sem ela a tabela mostraria
            # o UUID cru ao lado de um achado de limite que cita o apelido.
            try:
                team_aliases = {
                    str(t["team_id"]): str(t.get("team_alias") or t["team_id"])
                    for t in self.engine.client.list_teams()
                    if t.get("team_id")
                }
            except Exception:
                # Rota indisponível não pode derrubar a página: sem o mapa a
                # célula volta ao UUID, que é o comportamento de antes.
                team_aliases = {}
            # Combos de resiliência = fallbacks do roteador. Proxy antigo não
            # tem `/router/settings`, e o cliente já devolve vazio nesse caso;
            # o `except` cobre o resto (cliente sem o método, rede caindo no
            # meio) para que o cartão fique vazio em vez de sumir.
            try:
                combos = fallback_combos(self.engine.client.router_settings())
            except Exception:
                combos = []

        details = state.get("details") or []
        # O estado do modelo só existe quando a validação viva está ligada; a
        # tabela mostra travessão onde não houve veredito, em vez de inventar um.
        model_states = {
            d.get("name"): d.get("status")
            for d in details
            if d.get("kind") == "model" and d.get("status")
        }

        online, latency_ms = self.probe_proxy()
        base_url = ""
        if self.engine:
            base_url = getattr(self.engine.client, "base_url", "") or ""
        if not base_url and self.settings:
            base_url = self.settings.litellm_url

        return {
            "keys": keys,
            "models": models,
            # As conexões não têm rota própria no gateway: elas SÃO os destinos
            # declarados pelos modelos, agrupados por (provedor, api_base).
            "connections": group_connections(models),
            "combos": combos,
            "team_aliases": team_aliases,
            "model_states": model_states,
            "findings": [d for d in details if d.get("kind") == "limit"],
            "counters": state,
            "cron": self.cron_scheduler.get_status() if self.cron_scheduler else {"active": False},
            "proxy": {"url": base_url, "online": online, "latencyMs": latency_ms},
        }

    def serve_dashboard(self, query: Dict[str, List[str]]) -> None:
        """Renderiza a página inteira no servidor, com os dados já embutidos."""
        state = self.collect_dashboard_state()

        flash = None
        notice = (query.get("aviso", [""])[0] or "").strip()
        if notice:
            flash = {"message": notice, "tone": (query.get("tom", ["info"])[0] or "info").strip()}

        current_user, is_default, auth_from_env, refresh_margin = "admin", False, False, 900
        if self.settings:
            current_user = getattr(self, "authenticated_user", "") or self.settings.dashboard_user
            is_default = self.settings.is_default_password()
            auth_from_env = self.settings.dashboard_auth_from_env
            refresh_margin = self.settings.refresh_margin

        content = render_dashboard(
            keys=state["keys"],
            models=state["models"],
            connections=state.get("connections") or [],
            combos=state.get("combos") or [],
            team_aliases=state.get("team_aliases") or {},
            model_states=state["model_states"],
            findings=state["findings"],
            counters=state["counters"],
            cron=state["cron"],
            proxy=state["proxy"],
            current_user=current_user,
            is_default_password=is_default,
            refresh_margin=refresh_margin,
            auth_from_env=auth_from_env,
            flash=flash,
            lang=self.resolve_language(),
            sso=self.configuracao_sso(),
        ).encode("utf-8")
        self.respond_html(content)


def start_web(settings: Settings, engine: Any) -> QuietThreadingHTTPServer:
    """Sobe o painel em uma thread própria e devolve o servidor.

    O agendador é criado sempre, mesmo com `CRON_ENABLED=0`: só assim o botão
    "Executar agora" continua funcionando e o histórico existe para ser lido.
    O que a flag controla é o laço automático, não a existência do agendador.
    """
    DashboardHandler.settings = settings
    DashboardHandler.engine = engine

    scheduler = CronScheduler(
        sync_callback=DashboardHandler.execute_cycle,
        interval_seconds=settings.cron_interval,
        name=f"{NOME_DO_PRODUTO}-CronScheduler",
    )
    DashboardHandler.cron_scheduler = scheduler

    server = QuietThreadingHTTPServer(
        (settings.web_host, settings.web_port), DashboardHandler
    )
    # Pendurado no servidor para que quem o desligar (CLI, teste) também consiga
    # parar o agendador: são o mesmo ciclo de vida.
    server.cron_scheduler = scheduler
    # E o ciclo do painel fica alcançável de fora: com o agendador desligado
    # (`CRON_ENABLED=0`) quem roda os ciclos é o laço do CLI, e chamar
    # `engine.sync_all` direto de lá deixaria a tela presa no primeiro resultado.
    server.execute_cycle = DashboardHandler.execute_cycle
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Depois do bind: com a porta ocupada, `start_web` levanta e ninguém
    # ficaria com um agendador órfão inspecionando o proxy em segundo plano.
    if settings.cron_enabled:
        scheduler.start()
    return server
