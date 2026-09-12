"""Painel do LiteLlmRTKSync, renderizado inteiramente no servidor.

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
import html
import json
import os
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .config import Settings
from .i18n import translate
from .limits import SEVERIDADE_INCOERENTE, SEVERIDADE_SEM_TETO, avaliar
from .models import VirtualKey, summarize

CDN_BOOTSTRAP_CSS = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"
CDN_BOOTSTRAP_ICONS = "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css"
CDN_BOOTSTRAP_JS = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"
CDN_JQUERY = "https://cdn.jsdelivr.net/npm/jquery@3.7.1/dist/jquery.min.js"
GOOGLE_FONTS = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"

# Ícone por estado. Fonte de ícone de verdade, nunca emoji.
ICONES = {
    "active": ("bi-check-circle-fill", "success", "Ativa"),
    "expiring_soon": ("bi-clock-history", "warning", "Vencendo"),
    "expired": ("bi-x-octagon-fill", "danger", "Vencida"),
    "blocked": ("bi-slash-circle-fill", "secondary", "Bloqueada"),
    "over_budget": ("bi-cash-stack", "danger", "Orçamento esgotado"),
    "unknown": ("bi-question-circle", "secondary", "Desconhecido"),
    "valid": ("bi-check-circle-fill", "success", "Aceita"),
    "invalid": ("bi-x-octagon-fill", "danger", "Recusada"),
    "rate_limited": ("bi-hourglass-split", "warning", "Limitada"),
    "unreachable": ("bi-plug", "warning", "Inacessível"),
    "not_checked": ("bi-dash-circle", "secondary", "Não verificável"),
}


def esc(valor: Any) -> str:
    return html.escape(str(valor if valor is not None else ""))


def _badge(estado: str) -> str:
    icone, cor, rotulo = ICONES.get(estado, ICONES["unknown"])
    return f'<span class="badge text-bg-{cor}"><i class="bi {icone} me-1"></i>{esc(rotulo)}</span>'


def _duracao(segundos: Optional[int]) -> str:
    if segundos is None:
        # Validade não declarada NÃO é "ilimitada": afirmar isso seria dizer
        # algo que o dado não sustenta.
        return '<span class="text-secondary">validade não declarada</span>'
    if segundos <= 0:
        return '<span class="text-danger">vencida</span>'
    horas, resto = divmod(segundos, 3600)
    if horas >= 24:
        return f"{horas // 24}d {horas % 24}h"
    return f"{horas}h {resto // 60}min"


class PainelHandler(BaseHTTPRequestHandler):
    settings: Settings = None
    engine: Any = None
    ultimo_ciclo: Dict[str, Any] = {}
    _lock = threading.Lock()

    server_version = "LiteLlmRTKSync"
    sys_version = ""

    # -- cabeçalhos ---------------------------------------------------------

    CABECALHOS_DE_SEGURANCA = (
        ("Referrer-Policy", "no-referrer"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        (
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com data:; "
            "img-src 'self' data:; "
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
        ja_enviados = set()
        for linha in getattr(self, "_headers_buffer", []) or []:
            try:
                texto = linha.decode("latin-1", "ignore")
            except Exception:
                continue
            if ":" in texto:
                ja_enviados.add(texto.split(":", 1)[0].strip().lower())
        for nome, valor in self.CABECALHOS_DE_SEGURANCA:
            if nome.lower() not in ja_enviados:
                self.send_header(nome, valor)
        super().end_headers()

    def log_message(self, formato, *args):
        # O log de acesso do http.server escreve em stderr sem passar pelo
        # logger, e carrega a linha de requisição inteira. Silenciado.
        return

    def write_body(self, corpo: bytes) -> None:
        try:
            self.wfile.write(corpo)
        except (BrokenPipeError, ConnectionResetError):
            # O navegador fechou antes de ler. Não é erro do servidor, e deixar
            # subir enchia o log de traceback a cada refresh cancelado.
            pass

    # -- autenticação -------------------------------------------------------

    def require_auth(self) -> bool:
        cabecalho = self.headers.get("Authorization", "")
        if cabecalho.startswith("Basic "):
            try:
                bruto = base64.b64decode(cabecalho[6:]).decode("utf-8")
                usuario, _, senha = bruto.partition(":")
            except Exception:
                usuario = senha = ""
            if self.settings and self.settings.verify_credentials(usuario, senha):
                return True

        corpo = self.pagina_de_aviso(
            translate("auth.required", self.idioma()),
            translate("auth.required_body", self.idioma()),
        ).encode("utf-8")
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="LiteLlmRTKSync"')
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(corpo)
        return False

    def is_same_origin(self) -> bool:
        """Recusa POST vindo de outra origem.

        O navegador anexa o Basic Auth sozinho num formulário de terceiro, então
        sem esta barreira uma página maliciosa trocaria a senha do painel. O
        Origin decide primeiro; o Sec-Fetch-Site só entra quando não há Origin.
        """
        host = self.headers.get("Host", "")
        origem = self.headers.get("Origin", "")
        if origem and origem != "null":
            return urlparse(origem).netloc == host
        destino = self.headers.get("Sec-Fetch-Site", "")
        if destino:
            return destino in ("same-origin", "none")
        # curl e scripts não têm sessão para sequestrar.
        return True

    def idioma(self) -> str:
        return "pt"

    # -- rotas --------------------------------------------------------------

    def do_GET(self):
        caminho = urlparse(self.path).path
        if caminho == "/healthz":
            self.serve_healthz()
            return
        # Servida ANTES do require_auth de propósito: o navegador ainda está com
        # a senha antiga neste instante, e exigir autenticação aqui daria um 401
        # cru exatamente depois de a troca ter dado certo.
        if caminho == "/credenciais-atualizadas":
            corpo = self.pagina_de_aviso(
                "Credenciais atualizadas",
                "A senha foi gravada. Feche esta aba e abra o painel de novo para "
                "entrar com a credencial nova.",
            ).encode("utf-8")
            self.responder_html(corpo)
            return

        if not self.require_auth():
            return

        if caminho in ("/", "/index.html"):
            self.serve_painel(parse_qs(urlparse(self.path).query))
        elif caminho == "/api/status":
            self.serve_status_json()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Rota inexistente")

    def do_POST(self):
        if not self.require_auth():
            return
        if not self.is_same_origin():
            # Redireciona com aviso em vez de devolver 403 cru: o operador
            # precisa entender o que houve, e um 403 na tela depois de trocar a
            # senha parece um defeito.
            self.redirecionar("/?aviso=Requisicao+de+outra+origem+recusada&tom=danger")
            return

        caminho = urlparse(self.path).path
        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho) if tamanho else b""
        campos = parse_qs(corpo.decode("utf-8", errors="replace"))

        if caminho == "/acoes/atualizar":
            self.executar_ciclo()
            self.redirecionar("/?aviso=Estado+atualizado&tom=success")
        elif caminho == "/acoes/credenciais":
            self.trocar_credenciais(campos)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Ação inexistente")

    # -- ações --------------------------------------------------------------

    def executar_ciclo(self) -> Dict[str, Any]:
        with PainelHandler._lock:
            resultado = self.engine.sync_all() if self.engine else {}
            PainelHandler.ultimo_ciclo = resultado
            return resultado

    def trocar_credenciais(self, campos: Dict[str, List[str]]) -> None:
        usuario = (campos.get("user", [""])[0] or "").strip()
        senha = (campos.get("password", [""])[0] or "").strip()

        problemas = self.settings.check_password_strength(senha) if self.settings else []
        if problemas:
            # Todas as regras violadas de uma vez: uma por tentativa faria o
            # operador descobrir a política aos poucos.
            texto = " ".join(translate(chave, self.idioma()) for chave in problemas)
            self.redirecionar(f"/?aviso={texto.replace(' ', '+')}&tom=danger")
            return
        if self.settings and self.settings.dashboard_auth_from_env:
            self.redirecionar(
                "/?aviso=Credenciais+definidas+por+variavel+de+ambiente.+Altere-as+no+ambiente.&tom=warning"
            )
            return
        if self.settings and self.settings.update_auth_credentials(usuario, senha):
            self.redirecionar("/credenciais-atualizadas")
            return
        self.redirecionar("/?aviso=Nao+foi+possivel+gravar+a+senha&tom=danger")

    # -- respostas ----------------------------------------------------------

    def redirecionar(self, destino: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", destino)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def responder_html(self, corpo: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.write_body(corpo)

    def serve_healthz(self) -> None:
        proxy_ok = bool(self.engine and self.engine.client.health())
        if proxy_ok:
            status, carga = HTTPStatus.OK, b"OK"
        else:
            status, carga = HTTPStatus.SERVICE_UNAVAILABLE, b"LITELLM_UNREACHABLE"
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(carga)))
        self.end_headers()
        self.write_body(carga)

    def serve_status_json(self) -> None:
        """Projeção explícita. Nenhum token, nenhuma chave de provedor."""
        estado = PainelHandler.ultimo_ciclo or {}
        carga = json.dumps(
            {
                "timestamp": estado.get("timestamp"),
                "keys": estado.get("keys", 0),
                "teams": estado.get("teams", 0),
                "models": estado.get("models", 0),
                "expiring": estado.get("expiring", 0),
                "invalidCredentials": estado.get("invalid_credentials", 0),
                "limitFindings": estado.get("limit_findings", 0),
                "summary": estado.get("summary", {}),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(carga)))
        self.end_headers()
        self.write_body(carga)

    # -- renderização -------------------------------------------------------

    def cabecalho_html(self, titulo: str) -> str:
        return f"""<!doctype html>
<html lang="pt-BR"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(titulo)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{GOOGLE_FONTS}">
<link rel="stylesheet" href="{CDN_BOOTSTRAP_CSS}">
<link rel="stylesheet" href="{CDN_BOOTSTRAP_ICONS}">
<style>
  body {{ font-family: 'Inter', system-ui, -apple-system, sans-serif; background:#f6f7f9; }}
  code, .mono {{ font-family: 'JetBrains Mono', ui-monospace, monospace; }}
  .card {{ border:0; box-shadow:0 1px 3px rgba(16,24,40,.08); }}
  .table > :not(caption) > * > * {{ padding:.7rem .75rem; }}
</style>
</head><body class="py-4">"""

    def rodape_html(self) -> str:
        return f"""<script src="{CDN_JQUERY}"></script>
<script src="{CDN_BOOTSTRAP_JS}"></script>
</body></html>"""

    def pagina_de_aviso(self, titulo: str, corpo: str) -> str:
        return (
            self.cabecalho_html(titulo)
            + f"""<main class="container" style="max-width:640px">
  <div class="card"><div class="card-body p-4">
    <h1 class="h5 mb-3"><i class="bi bi-shield-lock me-2"></i>{esc(titulo)}</h1>
    <p class="text-secondary mb-0">{esc(corpo)}</p>
  </div></div>
</main>"""
            + self.rodape_html()
        )

    def serve_painel(self, consulta: Dict[str, List[str]]) -> None:
        estado = PainelHandler.ultimo_ciclo or self.executar_ciclo()
        aviso = (consulta.get("aviso", [""])[0] or "").strip()
        tom = (consulta.get("tom", ["info"])[0] or "info").strip()

        chaves = [VirtualKey(k) for k in (estado.get("_keys_raw") or [])]
        if not chaves and self.engine:
            try:
                chaves = [VirtualKey(k) for k in self.engine.client.list_keys()]
            except Exception:
                chaves = []
        resumo = summarize(chaves, self.settings.refresh_margin) if chaves else {}

        partes = [self.cabecalho_html("LiteLlmRTKSync")]
        partes.append('<main class="container" style="max-width:1100px">')
        partes.append(
            '<div class="d-flex align-items-center justify-content-between mb-4">'
            '<h1 class="h4 mb-0"><i class="bi bi-diagram-3 me-2"></i>LiteLlmRTKSync</h1>'
            '<form method="post" action="/acoes/atualizar" class="m-0">'
            '<button class="btn btn-primary btn-sm" type="submit">'
            '<i class="bi bi-arrow-clockwise me-1"></i>Atualizar agora</button></form></div>'
        )

        if aviso:
            partes.append(
                f'<div class="alert alert-{esc(tom)} d-flex align-items-center" role="alert">'
                f'<i class="bi bi-info-circle me-2"></i><div>{esc(aviso)}</div></div>'
            )

        if self.settings and self.settings.is_default_password():
            # Some assim que houver senha gravada no banco de preferências.
            partes.append(
                '<div class="alert alert-warning d-flex align-items-center">'
                '<i class="bi bi-exclamation-triangle me-2"></i><div>'
                'Nenhuma senha definida ainda. Defina a sua abaixo — o acesso atual usa a '
                'credencial de recuperação, que fica no arquivo indicado no log.</div></div>'
            )

        partes.append(self._cartoes(estado, resumo))
        partes.append(self._tabela_de_chaves(chaves))
        partes.append(self._tabela_de_limites(estado))
        partes.append(self._formulario_de_senha())
        partes.append("</main>")
        partes.append(self.rodape_html())
        self.responder_html("".join(partes).encode("utf-8"))

    def _cartoes(self, estado: Dict[str, Any], resumo: Dict[str, int]) -> str:
        itens = [
            ("Chaves virtuais", estado.get("keys", 0), "bi-key"),
            ("Times", estado.get("teams", 0), "bi-people"),
            ("Modelos", estado.get("models", 0), "bi-cpu"),
            ("Vencendo / vencidas", estado.get("expiring", 0), "bi-clock-history"),
            ("Incoerências de limite", estado.get("limit_findings", 0), "bi-sliders"),
        ]
        celulas = "".join(
            f'<div class="col"><div class="card h-100"><div class="card-body">'
            f'<div class="text-secondary small mb-1"><i class="bi {icone} me-1"></i>{esc(rotulo)}</div>'
            f'<div class="h3 mb-0">{esc(valor)}</div></div></div></div>'
            for rotulo, valor, icone in itens
        )
        return f'<div class="row row-cols-2 row-cols-md-5 g-3 mb-4">{celulas}</div>'

    def _tabela_de_chaves(self, chaves: List[VirtualKey]) -> str:
        if not chaves:
            return (
                '<div class="card mb-4"><div class="card-body text-secondary">'
                'Nenhuma chave virtual cadastrada no proxy.</div></div>'
            )
        margem = self.settings.refresh_margin if self.settings else 900
        linhas = []
        for chave in chaves:
            linhas.append(
                "<tr>"
                f'<td class="mono">{esc(chave.alias)}</td>'
                f"<td>{esc(chave.team_id or '—')}</td>"
                f"<td>{_badge(chave.health_status(margem))}</td>"
                f'<td class="text-end">{_duracao(chave.remaining_seconds)}</td>'
                f'<td class="text-end mono">{esc(f"{chave.spend:.4f}")}</td>'
                "</tr>"
            )
        return (
            '<div class="card mb-4"><div class="card-body">'
            '<h2 class="h6 mb-3"><i class="bi bi-key me-2"></i>Chaves virtuais</h2>'
            '<div class="table-responsive"><table class="table table-sm align-middle mb-0">'
            '<thead><tr><th>Apelido</th><th>Time</th><th>Estado</th>'
            '<th class="text-end">Validade</th><th class="text-end">Gasto</th></tr></thead>'
            f"<tbody>{''.join(linhas)}</tbody></table></div></div></div>"
        )

    def _tabela_de_limites(self, estado: Dict[str, Any]) -> str:
        achados = [d for d in estado.get("details", []) if d.get("kind") == "limit"]
        if not achados:
            return (
                '<div class="card mb-4"><div class="card-body">'
                '<h2 class="h6 mb-2"><i class="bi bi-sliders me-2"></i>Coerência dos limites</h2>'
                '<p class="text-secondary mb-0">'
                '<i class="bi bi-check-circle-fill text-success me-1"></i>'
                'Todo limite respeita o nível acima.</p></div></div>'
            )
        itens = "".join(
            f'<li class="list-group-item"><i class="bi bi-exclamation-triangle text-warning me-2"></i>'
            f"{esc(d['actions'][0] if d.get('actions') else d.get('name'))}</li>"
            for d in achados
        )
        return (
            '<div class="card mb-4"><div class="card-body">'
            '<h2 class="h6 mb-3"><i class="bi bi-sliders me-2"></i>Coerência dos limites</h2>'
            f'<ul class="list-group list-group-flush">{itens}</ul></div></div>'
        )

    def _formulario_de_senha(self) -> str:
        return (
            '<div class="card mb-4"><div class="card-body">'
            '<h2 class="h6 mb-3"><i class="bi bi-shield-lock me-2"></i>Credenciais do painel</h2>'
            '<form method="post" action="/acoes/credenciais" class="row g-2 align-items-end">'
            '<div class="col-sm-4"><label class="form-label small">Usuário</label>'
            '<input class="form-control form-control-sm" name="user" value="admin" autocomplete="username"></div>'
            '<div class="col-sm-5"><label class="form-label small">Nova senha</label>'
            '<input class="form-control form-control-sm" type="password" name="password" '
            'autocomplete="new-password"></div>'
            '<div class="col-sm-3"><button class="btn btn-outline-primary btn-sm w-100" type="submit">'
            '<i class="bi bi-check2 me-1"></i>Gravar</button></div>'
            '<div class="col-12"><div class="form-text">Mínimo de 6 caracteres, com maiúscula, '
            'minúscula, número e caractere especial.</div></div>'
            "</form></div></div>"
        )


def start_web(settings: Settings, engine: Any) -> ThreadingHTTPServer:
    """Sobe o painel em uma thread própria e devolve o servidor."""
    PainelHandler.settings = settings
    PainelHandler.engine = engine
    servidor = ThreadingHTTPServer((settings.web_host, settings.web_port), PainelHandler)
    servidor.daemon_threads = True
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    return servidor
