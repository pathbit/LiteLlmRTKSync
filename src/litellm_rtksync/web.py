"""Painel do LiteLlmRTKSync, renderizado inteiramente no servidor.

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
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlencode, urlparse

from .config import Settings
from .cron import CronScheduler
from .i18n import DEFAULT_LANGUAGE, normalize_language, translate
from .models import ModelEntry, VirtualKey
from .prefs import get_preference, set_preference
from . import protecao, sessao
from .render import render_dashboard, render_login_page, render_notice_page


def strip_markup(text: str) -> str:
    """Tira as etiquetas de um texto do catálogo destinado a virar aviso puro."""
    return re.sub(r"<[^>]+>", "", text)


class LiteLlmDashboardHandler(BaseHTTPRequestHandler):
    settings: Settings = None
    engine: Any = None
    cron_scheduler: Any = None
    last_cycle: Dict[str, Any] = {}
    _lock = threading.Lock()

    server_version = "LiteLlmRTKSync"
    sys_version = ""

    def version_string(self) -> str:
        # Sem isto o BaseHTTPRequestHandler concatena server_version com
        # sys_version e serve "LiteLlmRTKSync " -- com espaco sobrando.
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
        self.send_header("WWW-Authenticate", 'Basic realm="LiteLlmRTKSync"')
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

    def serve_login_page(self, erro: str = "") -> None:
        """Formulario de entrada: a porta do navegador para o painel."""
        lang = self.resolve_language()
        # O desafio so entra depois de algumas falhas: quem acerta de primeira
        # nunca o ve, e quem insiste passa a pagar CPU por tentativa.
        endereco = protecao.endereco_do_cliente(self.client_address)
        desafio = protecao.novo_desafio() if protecao.precisa_de_desafio(endereco) else ""
        dificuldade = protecao.dificuldade_para(endereco)
        body = render_login_page(lang, erro, desafio, dificuldade)
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
        return caminho in self.ROTAS_CONHECIDAS or caminho.startswith(self.PREFIXOS_CONHECIDOS)

    def recusa_rota_desconhecida(self, caminho: str) -> bool:
        """Devolve True e responde 404 quando a rota nao existe neste servidor."""
        if self.rota_existe(caminho):
            return False
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        return True

    def do_GET(self):
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
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Rota inexistente")

    def do_POST(self):
        rota_inicial = urlparse(self.path).path
        if rota_inicial == "/login":
            self.handle_login()
            return
        if rota_inicial == "/logout":
            self.handle_logout()
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
        return LiteLlmDashboardHandler.execute_cycle()

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
        state = LiteLlmDashboardHandler.last_cycle or {}
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
        state = LiteLlmDashboardHandler.last_cycle or self.run_cycle()

        keys: List[Any] = []
        models: List[Any] = []
        team_aliases: Dict[str, str] = {}
        if self.engine:
            try:
                keys = [VirtualKey(k) for k in self.engine.client.list_keys()]
            except Exception:
                keys = []
            try:
                models = [ModelEntry(m) for m in self.engine.client.list_models()]
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
        ).encode("utf-8")
        self.respond_html(content)


def start_web(settings: Settings, engine: Any) -> ThreadingHTTPServer:
    """Sobe o painel em uma thread própria e devolve o servidor.

    O agendador é criado sempre, mesmo com `CRON_ENABLED=0`: só assim o botão
    "Executar agora" continua funcionando e o histórico existe para ser lido.
    O que a flag controla é o laço automático, não a existência do agendador.
    """
    LiteLlmDashboardHandler.settings = settings
    LiteLlmDashboardHandler.engine = engine

    scheduler = CronScheduler(
        sync_callback=LiteLlmDashboardHandler.execute_cycle,
        interval_seconds=settings.cron_interval,
        name="LiteLlmRTKSync-CronScheduler",
    )
    LiteLlmDashboardHandler.cron_scheduler = scheduler

    server = ThreadingHTTPServer((settings.web_host, settings.web_port), LiteLlmDashboardHandler)
    # Pendurado no servidor para que quem o desligar (CLI, teste) também consiga
    # parar o agendador: são o mesmo ciclo de vida.
    server.cron_scheduler = scheduler
    # E o ciclo do painel fica alcançável de fora: com o agendador desligado
    # (`CRON_ENABLED=0`) quem roda os ciclos é o laço do CLI, e chamar
    # `engine.sync_all` direto de lá deixaria a tela presa no primeiro resultado.
    server.execute_cycle = LiteLlmDashboardHandler.execute_cycle
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Depois do bind: com a porta ocupada, `start_web` levanta e ninguém
    # ficaria com um agendador órfão inspecionando o proxy em segundo plano.
    if settings.cron_enabled:
        scheduler.start()
    return server
