"""Renderização server-side do dashboard do LiteLlmRTKSync.

Todo o HTML é montado aqui, no servidor, com os dados já embutidos. O navegador
nunca consulta o proxy: ele recebe a página pronta. Isso mantém a master key
inteiramente do lado do servidor e faz o painel funcionar mesmo com JavaScript
desabilitado — o jQuery serve só para conforto.

A casca é a mesma dos projetos irmãos, de propósito: cabeçalho, cartões de
métrica, seletor de idioma, modais e rodapé são idênticos, e o que muda são os
tokens de cor e as tabelas de domínio. Onde os irmãos listam conexões e combos,
aqui estão chaves virtuais, modelos e coerência de limites.

Ícones: Bootstrap Icons e flag-icons (fontes/CSS de ícones), nunca emoji.
Idioma padrão: inglês, com português e espanhol no seletor de bandeiras.
"""

import html
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .i18n import DEFAULT_LANGUAGE, LANGUAGES, normalize_language, translate

BOOTSTRAP_CSS = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"
BOOTSTRAP_ICONS = "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css"
FLAG_ICONS = "https://cdn.jsdelivr.net/npm/flag-icons@7.2.3/css/flag-icons.min.css"
BOOTSTRAP_JS = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"
JQUERY_JS = "https://cdn.jsdelivr.net/npm/jquery@3.7.1/dist/jquery.min.js"
# Tipografia: Google Fonts, com pilha de sistema como reserva se o CDN cair.
GOOGLE_FONTS = (
    "https://fonts.googleapis.com/css2?"
    "family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
)
FONT_STACK = "'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
MONO_STACK = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace"

# Estado semântico -> (classe do badge, ícone, CHAVE de tradução).
# O rótulo é resolvido na hora de desenhar, e não guardado pronto aqui: um
# rótulo embutido nesta tabela fica preso a um idioma e nunca é traduzido.
HEALTH_PRESENTATION = {
    "active": ("text-bg-success", "bi-check-circle-fill", "health.active"),
    "expiring_soon": ("text-bg-warning", "bi-hourglass-split", "health.expiring_soon"),
    "expired": ("text-bg-danger", "bi-x-octagon-fill", "health.expired"),
    "blocked": ("text-bg-secondary", "bi-slash-circle-fill", "health.blocked"),
    "over_budget": ("text-bg-danger", "bi-cash-stack", "health.over_budget"),
    "rate_limited": ("text-bg-warning", "bi-pause-circle-fill", "health.rate_limited"),
    "unknown": ("text-bg-secondary", "bi-question-circle-fill", "health.unknown"),
    # Estados vindos da validação viva da credencial do modelo.
    "valid": ("text-bg-success", "bi-check-circle-fill", "health.valid"),
    "invalid": ("text-bg-danger", "bi-shield-exclamation", "health.invalid"),
    "unreachable": ("text-bg-warning", "bi-plug", "health.unreachable"),
    "not_checked": ("text-bg-secondary", "bi-dash-circle", "health.not_checked"),
}


def esc(value: Any) -> str:
    """Escapa qualquer valor para inserção segura no HTML."""
    return html.escape(str(value if value is not None else ""), quote=True)


def format_duration(seconds: Optional[int], lang: str = DEFAULT_LANGUAGE) -> str:
    """Formata uma duração em segundos de forma legível."""
    if seconds is None:
        return translate("duration.unlimited", lang)
    if seconds <= 0:
        return translate("duration.expired", lang)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    rest = minutes % 60
    if hours < 24:
        return f"{hours}h {rest:02d}min"
    days = hours // 24
    return f"{days}d {hours % 24}h"


def format_timestamp(value: Optional[str]) -> str:
    """Normaliza um timestamp ISO para exibição."""
    if not value:
        return "—"
    return str(value).replace("T", " ").replace("Z", " UTC")


def render_notice_page(title: str, body: str, link_label: str = "") -> bytes:
    """Pagina autonoma para respostas fora do painel autenticado.

    E o que o navegador exibe quando o usuario aperta ESC no dialogo do Basic
    Auth, entao nao pode conter nem credencial nem dica de credencial.
    """
    link = (
        f'<p><a href="/">{esc(link_label)}</a></p>' if link_label else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <!-- Favicon embutido: o /favicon.ico do painel responde 401, entao sem
       isto a aba fica com o icone generico. Cada sincronizador tem o seu. -->
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='%233d0a0a'/><g fill='none' stroke='%23ffffff' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'><path d='M6 22 a10 10 0 0 1 20 0'/><path d='M16 22 L21.5 13.5'/><circle cx='16' cy='22' r='1.6' fill='%23ffffff'/></g></svg>">
  <meta name="robots" content="noindex, nofollow">
  <title>{esc(title)}</title>
  <link rel="stylesheet" href="{BOOTSTRAP_CSS}">
  <link rel="stylesheet" href="{BOOTSTRAP_ICONS}">
  <style>body {{ background: #2b0707; }}</style>
</head>
<body class="d-flex align-items-center justify-content-center" style="min-height:100vh">
  <div class="card text-center" style="max-width:34rem">
    <div class="card-body p-4">
      <i class="bi bi-shield-lock fs-1 text-secondary d-block mb-3" aria-hidden="true"></i>
      <h1 class="h5 mb-3">{esc(title)}</h1>
      <p class="text-secondary mb-3">{esc(body)}</p>
      {link}
    </div>
  </div>
</body>
</html>""".encode("utf-8")


def health_badge(status: str, lang: str) -> str:
    """Monta o badge de saúde com ícone de fonte."""
    css, icon, label_key = HEALTH_PRESENTATION.get(status, HEALTH_PRESENTATION["unknown"])
    label = translate(label_key, lang)
    return (
        f'<span class="badge {css} d-inline-flex align-items-center gap-1">'
        f'<i class="bi {icon}" aria-hidden="true"></i>{esc(label)}</span>'
    )


def render_language_switcher(current: str) -> str:
    """Seletor de idioma com bandeiras reais (flag-icons), não emoji."""
    current = normalize_language(current)
    _, current_flag = LANGUAGES[current]
    items = []
    for code, (label, flag) in LANGUAGES.items():
        active = " active" if code == current else ""
        items.append(
            f'<li><button class="dropdown-item d-flex align-items-center gap-2{active}" '
            f'type="submit" name="lang" value="{esc(code)}">'
            f'<span class="fi {esc(flag)}"></span>{esc(label)}</button></li>'
        )
    return f"""
        <form method="post" action="/acoes/idioma" class="m-0 dropdown">
          <button class="btn btn-outline-light btn-sm dropdown-toggle d-inline-flex align-items-center gap-2"
                  type="button" data-bs-toggle="dropdown" aria-expanded="false"
                  aria-label="{esc(translate('language.label', current))}">
            <span class="fi {esc(current_flag)}"></span>
          </button>
          <ul class="dropdown-menu dropdown-menu-end">{"".join(items)}
          </ul>
        </form>"""


def metric_card(label: str, value: Any, icon: str, tone: str) -> str:
    return f"""
      <div class="col-6 col-lg-3">
        <div class="card metric h-100">
          <div class="card-body">
            <div class="d-flex align-items-center gap-2 metric-label">
              <i class="bi {icon} {tone}" aria-hidden="true"></i><span>{esc(label)}</span>
            </div>
            <div class="metric-value {tone}">{esc(value)}</div>
          </div>
        </div>
      </div>"""


def render_security_banner(is_default_password: bool, lang: str) -> str:
    if not is_default_password:
        return ""
    return f"""
      <div class="alert alert-warning d-flex align-items-center justify-content-between gap-3" role="alert">
        <div class="d-flex align-items-start gap-2">
          <i class="bi bi-shield-exclamation fs-5" aria-hidden="true"></i>
          <div><strong>{esc(translate("security.title", lang))}</strong>
            {translate("security.body", lang)}</div>
        </div>
        <button class="btn btn-warning btn-sm text-nowrap" data-bs-toggle="modal" data-bs-target="#modalCredenciais">
          <i class="bi bi-key-fill me-1" aria-hidden="true"></i>{esc(translate("action.change_credentials", lang))}
        </button>
      </div>"""


def render_flash(flash: Optional[Dict[str, str]]) -> str:
    if not flash:
        return ""
    tone = flash.get("tone", "info")
    icon = {
        "success": "bi-check-circle-fill",
        "danger": "bi-exclamation-octagon-fill",
        "warning": "bi-exclamation-triangle-fill",
        "info": "bi-info-circle-fill",
    }.get(tone, "bi-info-circle-fill")
    return f"""
      <div class="alert alert-{esc(tone)} d-flex align-items-center gap-2" role="status">
        <i class="bi {icon}" aria-hidden="true"></i>
        <div>{esc(flash.get("message", ""))}</div>
      </div>"""


def render_remaining(remaining: Optional[int], lang: str) -> str:
    """Validade restante, sem chamar de ilimitado o que só está faltando.

    Uma chave virtual sem `expires` legível é validade **não declarada**, e não
    uma credencial eterna. Dizer "ilimitada" afirmaria algo que o dado não
    sustenta, e é justamente a afirmação que faz o operador parar de olhar.
    """
    if remaining is None:
        return (
            '<span class="text-warning d-inline-flex align-items-center gap-1">'
            '<i class="bi bi-exclamation-triangle" aria-hidden="true"></i>'
            f'{esc(translate("duration.unknown_expiry", lang))}</span>'
        )
    return esc(format_duration(remaining, lang))


def render_keys_table(keys: List[Any], refresh_margin: int, lang: str) -> str:
    """Chaves virtuais emitidas pelo proxy, uma por linha."""
    if not keys:
        return f"""
        <div class="text-center text-secondary py-5">
          <i class="bi bi-inbox fs-1 d-block mb-2" aria-hidden="true"></i>
          {esc(translate("keys.empty", lang))}
        </div>"""

    rows = []
    for key in keys:
        team = key.team_id
        team_cell = (
            f'<span class="provider-chip">{esc(team)}</span>'
            if team
            else '<span class="text-secondary">—</span>'
        )
        # O apelido já chega mascarado quando a chave não tem nome: quem monta a
        # identificação é o modelo, para que a máscara valha em toda saída.
        rows.append(f"""
            <tr>
              <td class="fw-semibold font-monospace">{esc(key.alias)}</td>
              <td>{team_cell}</td>
              <td>{health_badge(key.health_status(refresh_margin), lang)}</td>
              <td class="text-nowrap">{render_remaining(key.remaining_seconds, lang)}</td>
              <td class="text-end font-monospace">{esc(f"{key.spend:.4f}")}</td>
            </tr>""")

    return f"""
        <div class="table-responsive">
          <table class="table table-dark table-hover align-middle mb-0">
            <thead>
              <tr>
                <th scope="col">{esc(translate("table.alias", lang))}</th>
                <th scope="col">{esc(translate("table.team", lang))}</th>
                <th scope="col">{esc(translate("table.status", lang))}</th>
                <th scope="col">{esc(translate("table.remaining", lang))}</th>
                <th scope="col" class="text-end">{esc(translate("table.spend", lang))}</th>
              </tr>
            </thead>
            <tbody>{"".join(rows)}
            </tbody>
          </table>
        </div>"""


def credential_label(model: Any, lang: str) -> str:
    """De ONDE vem a credencial do modelo — nunca qual é ela.

    A chave declarada no cadastro é um segredo e não aparece em lugar nenhum da
    página; o que o operador precisa saber é se há credencial e onde ela mora.
    """
    if model.key_is_env_reference:
        return translate("credential.env", lang)
    if model.uses_named_credential:
        return translate("credential.named", lang)
    if model.api_key:
        return translate("credential.inline", lang)
    return translate("credential.absent", lang)


def render_models_table(models: List[Any], states: Dict[str, str], lang: str) -> str:
    """Modelos cadastrados e a procedência da credencial de cada um."""
    if not models:
        return f"""
        <div class="text-center text-secondary py-4">
          <i class="bi bi-cpu fs-3 d-block mb-2" aria-hidden="true"></i>
          {esc(translate("models.empty", lang))}
        </div>"""

    rows = []
    for model in models:
        provider = model.provider
        provider_cell = (
            f'<span class="provider-chip">{esc(provider)}</span>'
            if provider
            else '<span class="text-secondary">—</span>'
        )
        # Sem validação viva ligada não há estado a mostrar: um badge cinza
        # dizendo "desconhecido" seria inventar um veredito que ninguém emitiu.
        state = states.get(model.name)
        state_cell = health_badge(state, lang) if state else '<span class="text-secondary">—</span>'
        rows.append(f"""
            <tr>
              <td class="fw-semibold">{esc(model.name)}</td>
              <td>{provider_cell}</td>
              <td class="small text-secondary">{esc(credential_label(model, lang))}</td>
              <td>{state_cell}</td>
            </tr>""")

    return f"""
        <div class="table-responsive">
          <table class="table table-dark table-hover align-middle mb-0">
            <thead>
              <tr>
                <th scope="col">{esc(translate("table.name", lang))}</th>
                <th scope="col">{esc(translate("table.provider", lang))}</th>
                <th scope="col">{esc(translate("credential.title", lang))}</th>
                <th scope="col">{esc(translate("table.status", lang))}</th>
              </tr>
            </thead>
            <tbody>{"".join(rows)}
            </tbody>
          </table>
        </div>"""


def render_limits_card(findings: List[Dict[str, Any]], lang: str) -> str:
    """Incoerências entre o limite da chave e o teto do nível acima.

    A mensagem vem pronta do avaliador de limites: ela nomeia o campo, os dois
    valores e a consequência. Resumir aqui produziria "limite incoerente", que
    não diz a ninguém o que fazer a respeito.
    """
    if not findings:
        return f"""
        <div class="card-body">
          <p class="mb-0 d-flex align-items-center gap-2">
            <i class="bi bi-check-circle-fill text-success" aria-hidden="true"></i>
            <span class="text-secondary">{esc(translate("limits.ok", lang))}</span>
          </p>
        </div>"""

    items = []
    for finding in findings:
        actions = finding.get("actions") or []
        message = actions[0] if actions else finding.get("name", "—")
        items.append(f"""
            <li class="list-group-item d-flex align-items-start gap-2">
              <i class="bi bi-exclamation-triangle text-warning mt-1" aria-hidden="true"></i>
              <span>{esc(message)}</span>
            </li>""")

    return f"""
        <ul class="list-group list-group-flush">{"".join(items)}
        </ul>"""


def render_proxy_card(proxy: Dict[str, Any], lang: str) -> str:
    """Cartão de liveness do proxy.

    Existe para que "o painel está de pé" e "o proxy está de pé" nunca sejam
    confundidos: são dois processos distintos, e o painel responde mesmo com o
    proxy fora.
    """
    online = bool(proxy.get("online"))
    tone = "text-success" if online else "text-danger"
    icon = "bi-plug-fill" if online else "bi-plug"
    label = (
        "ONLINE"
        if online
        else f'{esc(translate("gateway.offline", lang))} — '
             f'{esc(translate("gateway.no_response", lang))}'
    )

    return f"""
      <div class="card h-100">
        <div class="card-header d-flex align-items-center justify-content-between">
          <span class="d-inline-flex align-items-center gap-2">
            <i class="bi bi-hdd-network" aria-hidden="true"></i>{esc(translate("gateway.title", lang))}
          </span>
          <form method="post" action="/acoes/testar-gateway" class="m-0">
            <button class="btn btn-outline-light btn-sm" type="submit">
              <i class="bi bi-activity me-1" aria-hidden="true"></i>{esc(translate("action.test_connection", lang))}
            </button>
          </form>
        </div>
        <div class="card-body">
          <dl class="row mb-0 small">
            <dt class="col-5 text-secondary fw-normal">{esc(translate("gateway.gateway", lang))}</dt>
            <dd class="col-7 text-end font-monospace text-truncate">{esc(proxy.get("url") or "—")}</dd>
            <dt class="col-5 text-secondary fw-normal">{esc(translate("gateway.status", lang))}</dt>
            <dd class="col-7 text-end font-monospace {tone}">
              <i class="bi {icon} me-1" aria-hidden="true"></i>{label}
            </dd>
            <dt class="col-5 text-secondary fw-normal">{esc(translate("gateway.latency", lang))}</dt>
            <dd class="col-7 text-end font-monospace mb-0">{esc(proxy.get("latencyMs", "—"))} ms</dd>
          </dl>
        </div>
      </div>"""


def render_credentials_modal(auth_from_env: bool, lang: str) -> str:
    """Corpo do modal de troca de credenciais.

    Nenhum valor vem preenchido: um usuário sugerido na tela é uma metade da
    credencial entregue de graça a quem abrir a página.
    """
    if auth_from_env:
        return f"""
          <div class="alert alert-secondary d-flex align-items-center gap-2 mb-0" role="note">
            <i class="bi bi-lock-fill" aria-hidden="true"></i>
            <div>{translate("auth.env_managed", lang)}</div>
          </div>"""
    return f"""
          <form method="post" action="/acoes/credenciais">
            <div class="mb-3">
              <label class="form-label" for="novoUsuario">{esc(translate("auth.user", lang))}</label>
              <input class="form-control" id="novoUsuario" name="user" autocomplete="username" required>
            </div>
            <div class="mb-3">
              <label class="form-label" for="novaSenha">{esc(translate("auth.new_password", lang))}</label>
              <input type="password" class="form-control" id="novaSenha" name="password"
                     minlength="6" autocomplete="new-password" required
                     pattern="(?=.*[a-z])(?=.*[A-Z])(?=.*\\d)(?=.*[^A-Za-z0-9]).{{6,}}"
                     title="{esc(translate("password.policy", lang))}">
              <div class="form-text">{esc(translate("password.policy", lang))}</div>
            </div>
            <button class="btn btn-primary w-100" type="submit">
              <i class="bi bi-save me-1" aria-hidden="true"></i>{esc(translate("action.save_credentials", lang))}
            </button>
          </form>"""


def render_dashboard(
    *,
    keys: List[Any],
    models: List[Any],
    model_states: Dict[str, str],
    findings: List[Dict[str, Any]],
    counters: Dict[str, Any],
    proxy: Dict[str, Any],
    current_user: str,
    is_default_password: bool,
    refresh_margin: int,
    auth_from_env: bool = False,
    flash: Optional[Dict[str, str]] = None,
    lang: str = DEFAULT_LANGUAGE,
) -> str:
    """Monta a página completa do dashboard, já com todos os dados embutidos."""
    lang = normalize_language(lang)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    metrics = "".join([
        metric_card(translate("metric.virtual_keys", lang), counters.get("keys", 0), "bi-key", "text-info"),
        metric_card(translate("metric.teams", lang), counters.get("teams", 0), "bi-people", "text-primary"),
        metric_card(translate("metric.expiring", lang), counters.get("expiring", 0),
                    "bi-clock-history", "text-warning"),
        metric_card(translate("metric.limit_findings", lang), counters.get("limit_findings", 0),
                    "bi-sliders", "text-danger"),
    ])

    return f"""<!DOCTYPE html>
<html lang="{esc(lang)}" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>LiteLlmRTKSync</title>
  <link rel="stylesheet" href="{BOOTSTRAP_CSS}">
  <link rel="stylesheet" href="{BOOTSTRAP_ICONS}">
  <link rel="stylesheet" href="{FLAG_ICONS}">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="{GOOGLE_FONTS}">
  <style>
    /* ------------------------------------------------------------------
       Identidade visual: os tres paineis da familia RTKSync tem a MESMA
       estrutura e a MESMA folha de estilo. O que muda e o valor destes
       tokens -- vinho profundo.
       Trocar o produto e trocar estas oito linhas, nada mais.

       O fundo nao e o vinho da marca (#6D0808): uma pagina inteira nele
       cansa a vista em poucos minutos, e este painel fica aberto o dia
       todo. A marca vive no gradiente, que e onde ela precisa estar.
       ------------------------------------------------------------------ */
    :root {{
      --bg:        #2b0707;   /* fundo da pagina */
      --surface:   #3d0a0a;   /* cartao */
      --surface-2: #4d0e0e;   /* cabecalho de cartao, chip */
      --line:      #6b1616;   /* borda */
      --accent:    #ffab5e;   /* acao primaria */
      --accent-2:  #ffc78f;   /* acao secundaria, realce */
      --brand-a:   #6D0808;   /* marca, inicio do gradiente */
      --brand-b:   #c2410c;   /* marca, fim do gradiente */
      --text:      #e6e8ee;
      --text-dim:  #97a0b5;
    }}
    body {{ background: var(--bg); color: var(--text); }}
    .card {{ background: var(--surface); border: 1px solid var(--line); }}
    .card-header {{ background: var(--surface-2); border-bottom: 1px solid var(--line); font-weight: 600; }}
    .metric .metric-label {{ font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; color: var(--text-dim); }}
    .metric .metric-value {{ font-size: 2rem; font-weight: 700; line-height: 1.2; margin-top: .35rem; }}
    .provider-chip {{ background: var(--surface-2); border: 1px solid var(--line); border-radius: .35rem;
                      padding: .15rem .5rem; font-family: var(--bs-font-monospace); font-size: .78rem;
                      text-transform: uppercase; }}
    .table-dark {{ --bs-table-bg: transparent; --bs-table-border-color: var(--line); }}
    /* A marca e icone BRANCO sobre um tom claro do proprio tema. O gradiente
       de duas cores fazia as tres telas parecerem a mesma marca em cores
       diferentes; com a forma do icone distinta e o fundo discreto, quem
       identifica o produto e o desenho, e a cor fica por conta do tema. */
    .brand-mark {{ width: 2.25rem; height: 2.25rem; display: grid; place-items: center; border-radius: .5rem;
                   background: color-mix(in srgb, var(--brand-b) 22%, transparent);
                   border: 1px solid color-mix(in srgb, var(--brand-b) 45%, transparent);
                   color: #fff; font-size: 1.15rem; }}
    .list-group-item {{ background: var(--surface); color: var(--text); border-color: var(--line); }}
    /* O botao primario segue o acento do produto, em vez do azul fixo do
       Bootstrap: senao os tres mudam de fundo e ficam com o mesmo botao, o que
       faz a identidade parecer acidental. */
    .btn-primary {{ --bs-btn-bg: var(--accent); --bs-btn-border-color: var(--accent);
                    --bs-btn-hover-bg: var(--accent-2); --bs-btn-hover-border-color: var(--accent-2);
                    --bs-btn-active-bg: var(--accent-2); --bs-btn-active-border-color: var(--accent-2);
                    --bs-btn-color: #0b0d12; --bs-btn-hover-color: #0b0d12; --bs-btn-active-color: #0b0d12; }}
    a {{ color: var(--accent-2); }}
    a:hover {{ color: var(--accent); }}
    /* Barra de acoes do cabecalho: todos os controles com a MESMA altura. O
       seletor de idioma carrega so a bandeira, um elemento com altura propria;
       sem texto ao lado para definir a linha, ele esticava o botao. */
    .barra-acoes {{ display: flex; align-items: stretch; gap: .5rem; }}
    .barra-acoes > * {{ display: flex; align-items: center; }}
    .barra-acoes .btn {{ height: 2rem; padding-top: 0; padding-bottom: 0;
                         display: inline-flex; align-items: center; line-height: 1; }}
    .barra-acoes .fi {{ line-height: 1; }}
  </style>
</head>
<body>
  <div class="container-xl py-4">

    {render_flash(flash)}
    {render_security_banner(is_default_password, lang)}

    <header class="d-flex flex-wrap align-items-center justify-content-between gap-3 mb-4">
      <div class="d-flex align-items-center gap-3">
        <span class="brand-mark"><i class="bi bi-speedometer2" aria-hidden="true"></i></span>
        <div>
          <h1 class="h4 mb-0">LiteLlmRTKSync</h1>
          <p class="text-secondary small mb-0 font-monospace">
            {esc(proxy.get("url") or translate("app.gateway_unset", lang))}
          </p>
        </div>
      </div>
      <div class="barra-acoes">
        {render_language_switcher(lang)}
        <button class="btn btn-outline-light btn-sm" data-bs-toggle="modal" data-bs-target="#modalCredenciais">
          <i class="bi bi-key me-1" aria-hidden="true"></i>{esc(translate("action.access", lang))}
        </button>
        <form method="post" action="/acoes/atualizar" class="m-0">
          <button class="btn btn-primary btn-sm" type="submit"
                  title="{esc(translate("action.refresh_title", lang))}">
            <i class="bi bi-arrow-clockwise me-1" aria-hidden="true"></i>{esc(translate("action.refresh", lang))}
          </button>
        </form>
      </div>
    </header>

    <div class="row g-3 mb-4">{metrics}
    </div>

    <div class="row g-3 mb-4">
      <div class="col-lg-6">{render_proxy_card(proxy, lang)}</div>
      <div class="col-lg-6">
        <div class="card h-100">
          <div class="card-header d-flex align-items-center justify-content-between">
            <span class="d-inline-flex align-items-center gap-2">
              <i class="bi bi-sliders" aria-hidden="true"></i>{esc(translate("limits.title", lang))}
            </span>
            <span class="badge text-bg-dark">{len(findings)}</span>
          </div>
          {render_limits_card(findings, lang)}
        </div>
      </div>
    </div>

    <div class="card mb-4">
      <div class="card-header d-flex align-items-center justify-content-between">
        <span class="d-inline-flex align-items-center gap-2">
          <i class="bi bi-key" aria-hidden="true"></i>{esc(translate("keys.title", lang))}
        </span>
        <span class="badge text-bg-dark">{len(keys)}</span>
      </div>
      {render_keys_table(keys, refresh_margin, lang)}
    </div>

    <div class="card mb-4">
      <div class="card-header d-flex align-items-center justify-content-between">
        <span class="d-inline-flex align-items-center gap-2">
          <i class="bi bi-cpu" aria-hidden="true"></i>{esc(translate("models.title", lang))}
        </span>
        <span class="badge text-bg-dark">{len(models)}</span>
      </div>
      {render_models_table(models, model_states, lang)}
    </div>

    <footer class="d-flex flex-wrap justify-content-between gap-2 text-secondary small pb-3">
      <span>
        <i class="bi bi-person-circle me-1" aria-hidden="true"></i>{esc(translate("footer.signed_in", lang))}
        <span class="font-monospace">{esc(current_user)}</span>
      </span>
      <span>
        <i class="bi bi-clock-history me-1" aria-hidden="true"></i>{esc(translate("footer.generated", lang))}
        <span class="font-monospace">{esc(generated_at)}</span>
      </span>
    </footer>
  </div>

  <div class="modal fade" id="modalCredenciais" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h2 class="modal-title h6 d-inline-flex align-items-center gap-2">
            <i class="bi bi-shield-lock" aria-hidden="true"></i>{esc(translate("auth.title", lang))}
          </h2>
          <button type="button" class="btn-close" data-bs-dismiss="modal"
                  aria-label="{esc(translate("action.close", lang))}"></button>
        </div>
        <div class="modal-body">{render_credentials_modal(auth_from_env, lang)}
        </div>
      </div>
    </div>
  </div>

  <script src="{JQUERY_JS}"></script>
  <script src="{BOOTSTRAP_JS}"></script>
  <script>
    // A página é renderizada no servidor; o jQuery só cuida de conforto de uso.
    jQuery(function ($) {{
      $('form[action^="/acoes/"]').not('[action="/acoes/idioma"]').on('submit', function () {{
        $(this).find('button[type=submit]')
               .prop('disabled', true)
               .find('i').attr('class', 'bi bi-hourglass-split me-1');
      }});
    }});
  </script>
</body>
</html>"""
