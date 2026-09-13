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

# Icone da aba, embutido como data URI: /favicon.ico responde 401 atras do
# Basic Auth, entao um arquivo servido deixaria a aba sem icone ate o
# operador autenticar -- e a pagina de erro nunca teria icone nenhum.
FAVICON = "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='%237d1414'/><g transform='translate(6 6) scale(1.25)' fill='%23ffffff'><path d='M8 4a.5.5 0 0 1 .5.5V6a.5.5 0 0 1-1 0V4.5A.5.5 0 0 1 8 4M3.732 5.732a.5.5 0 0 1 .707 0l.915.914a.5.5 0 1 1-.708.708l-.914-.915a.5.5 0 0 1 0-.707M2 10a.5.5 0 0 1 .5-.5h1.586a.5.5 0 0 1 0 1H2.5A.5.5 0 0 1 2 10m9.5 0a.5.5 0 0 1 .5-.5h1.5a.5.5 0 0 1 0 1H12a.5.5 0 0 1-.5-.5m.754-4.246a.39.39 0 0 0-.527-.02L7.547 9.31a.91.91 0 1 0 1.302 1.258l3.434-4.297a.39.39 0 0 0-.029-.518z'/><path fill-rule='evenodd' d='M0 10a8 8 0 1 1 15.547 2.661c-.442 1.253-1.845 1.602-2.932 1.25C11.309 13.488 9.475 13 8 13c-1.474 0-3.31.488-4.615.911-1.087.352-2.49.003-2.932-1.25A8 8 0 0 1 0 10m8-7a7 7 0 0 0-6.603 9.329c.203.575.923.876 1.68.63C4.397 12.533 6.358 12 8 12s3.604.532 4.923.96c.757.245 1.477-.056 1.68-.631A7 7 0 0 0 8 3'/></g></svg>"

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
    """Normaliza um timestamp ISO para exibição.

    Troca APENAS o "T" que separa data de hora, e não todo "T" da string. A
    versão anterior fazia `.replace("T", " ")` no texto inteiro, o que a tornava
    destrutiva ao ser aplicada duas vezes: a primeira passada produzia
    "2026-09-13 19:08:48 UTC", e a segunda comia o "T" de "UTC" e escrevia
    "19:08:48 U C" na tela. Um defeito que só aparece quando alguém formata um
    valor já formatado -- e isso é fácil de acontecer sem ninguém notar.
    """
    if not value:
        return "—"
    texto = str(value)
    if texto.endswith("Z"):
        texto = texto[:-1] + " UTC"
    # O separador ISO é o "T" na posição 10 (AAAA-MM-DDTHH:MM:SS).
    if len(texto) > 10 and texto[10] == "T":
        texto = texto[:10] + " " + texto[11:]
    return texto


def format_timestamp_curto(value: Optional[str]) -> str:
    """Data enxuta para a celula da tabela: dia/mes e hora, sem ano nem segundos.

    A forma completa ("2026-09-13 18:40:52 UTC") nao cabe na coluna e era
    cortada no meio, o que deixava a informacao pior do que util. O carimbo
    inteiro continua no modal de detalhe, a um clique da linha.
    """
    if not value:
        return "—"
    try:
        momento = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return format_timestamp(value)
    return momento.strftime("%d/%m %H:%M")


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
  <meta name="robots" content="noindex, nofollow">
  <link rel="icon" href="{FAVICON}">
  <title>{esc(title)}</title>
  <link rel="stylesheet" href="{BOOTSTRAP_CSS}">
  <link rel="stylesheet" href="{BOOTSTRAP_ICONS}">
  <style>body {{ background: #6D0808; }}</style>
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


def render_login_page(
    lang: str = DEFAULT_LANGUAGE,
    erro: str = "",
    desafio: str = "",
    dificuldade: int = 4,
) -> bytes:
    """Formulario de entrada, com a mesma casca e a mesma paleta do painel.

    Existe porque o dialogo do Basic Auth e uma janela do NAVEGADOR: nao se
    traduz, nao se estiliza, nao oferece logout e nao e HTML -- qualquer
    ferramenta que dirija um navegador para no dialogo, porque nao ha nada na
    pagina para preencher. Esta pagina resolve os quatro de uma vez.
    """
    lang = normalize_language(lang)
    aviso = (
        f'<div class="alert alert-danger d-flex align-items-center gap-2 mb-3" role="alert">'
        f'<i class="bi bi-exclamation-octagon-fill" aria-hidden="true"></i>'
        f'<span>{esc(erro)}</span></div>'
        if erro
        else ""
    )
    desafio_html = (
        f'<input type="hidden" name="desafio" value="{esc(desafio)}">'
        f'<input type="hidden" name="resposta" id="resposta" value="">'
        f'<p class="text-secondary small d-flex align-items-center gap-2" id="aviso-desafio">'
        f'<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>'
        f'{esc(translate("auth.challenge", lang))}</p>'
        f'<script>'
        f'(async () => {{'
        f'  const desafio = {desafio!r};'
        f'  const alvo = "0".repeat({dificuldade});'
        f'  const cod = new TextEncoder();'
        f'  for (let n = 0; n < 20000000; n++) {{'
        f'    const buf = await crypto.subtle.digest("SHA-256", cod.encode(desafio + n));'
        f'    const hex = [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");'
        f'    if (hex.startsWith(alvo)) {{'
        f'      document.getElementById("resposta").value = String(n);'
        f'      document.getElementById("aviso-desafio").remove();'
        f'      break;'
        f'    }}'
        f'  }}'
        f'}})();'
        f'</script>'
        if desafio
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="{esc(lang)}" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <link rel="icon" href="{FAVICON}">
  <title>LiteLlmRTKSync</title>
  <link rel="stylesheet" href="{BOOTSTRAP_CSS}">
  <link rel="stylesheet" href="{BOOTSTRAP_ICONS}">
  <style>
    :root {{ --bg: #6D0808; --surface: #7d1414; --line: #a32626;
             --accent: #ffce6b; --text: #e6e8ee; }}
    body {{ background: var(--bg); color: var(--text); font-family: {FONT_STACK}; }}
    .card {{ background: var(--surface); border: 1px solid var(--line); }}
    .btn-primary {{ --bs-btn-bg: var(--accent); --bs-btn-border-color: var(--accent);
                    --bs-btn-color: var(--bg); --bs-btn-hover-bg: var(--accent);
                    --bs-btn-hover-border-color: var(--accent); --bs-btn-hover-color: var(--bg); }}
    .form-control {{ background: var(--bg); border-color: var(--line); color: var(--text); }}
    .form-control:focus {{ background: var(--bg); color: var(--text);
                           border-color: var(--accent); box-shadow: none; }}
  </style>
</head>
<body class="d-flex align-items-center justify-content-center" style="min-height:100vh">
  <main class="card" style="max-width:24rem;width:100%">
    <div class="card-body p-4">
      <h1 class="h5 mb-1 d-flex align-items-center gap-2">
        <i class="bi bi-shield-lock" aria-hidden="true"></i>LiteLlmRTKSync
      </h1>
      <p class="text-secondary small mb-4">{esc(translate("auth.login_intro", lang))}</p>
      {aviso}
      <form method="post" action="/login">
        <div class="mb-3">
          <label class="form-label small" for="usuario">{esc(translate("auth.user", lang))}</label>
          <input class="form-control" id="usuario" name="usuario" autocomplete="username" autofocus required>
        </div>
        <div class="mb-4">
          <label class="form-label small" for="senha">{esc(translate("auth.password", lang))}</label>
          <input class="form-control" id="senha" name="senha" type="password"
                 autocomplete="current-password" required>
        </div>
        {desafio_html}
        <button class="btn btn-primary w-100" type="submit">
          <i class="bi bi-box-arrow-in-right me-1" aria-hidden="true"></i>{esc(translate("auth.enter", lang))}
        </button>
      </form>
    </div>
  </main>
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
      <div class="alert alert-{esc(tone)} d-flex align-items-center gap-2" role="status" data-aviso>
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


def render_detail_modal(modal_id: str, title: str, rows: List[tuple], lang: str,
                        extra: str = "") -> str:
    """Casca do modal de detalhe, igual à dos irmãos.

    O modal é devolvido como bloco solto para ser emitido DEPOIS da tabela:
    um `<div>` dentro de `<tbody>` é HTML inválido, e o navegador o move sozinho
    para fora — o que transforma cada linha da tabela numa surpresa de layout.
    """
    corpo = "".join(
        f'<dt class="col-5 text-secondary fw-normal">{esc(rotulo)}</dt>'
        f'<dd class="col-7 text-end">{valor}</dd>'
        for rotulo, valor in rows
    )
    return f"""
  <div class="modal fade" id="{esc(modal_id)}" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h2 class="modal-title h6 d-inline-flex align-items-center gap-2">
            <i class="bi bi-info-circle" aria-hidden="true"></i>{esc(title)}
          </h2>
          <button type="button" class="btn-close" data-bs-dismiss="modal"
                  aria-label="{esc(translate("action.close", lang))}"></button>
        </div>
        <div class="modal-body">
          <dl class="row mb-0 small">{corpo}</dl>
          {extra}
        </div>
      </div>
    </div>
  </div>"""


def detail_button(modal_id: str, lang: str) -> str:
    """Botão (i) da linha: coluna estreita, detalhe por extenso no modal."""
    return (
        f'<button class="btn btn-outline-light btn-sm py-0 px-2" type="button" '
        f'data-bs-toggle="modal" data-bs-target="#{esc(modal_id)}" '
        f'title="{esc(translate("table.details", lang))}">'
        f'<i class="bi bi-info-circle" aria-hidden="true"></i></button>'
    )


def render_optional_number(valor: Any, lang: str) -> str:
    """Número que pode não ter sido declarado.

    Ausência é **não declarado**, nunca "ilimitado": dizer ilimitado afirmaria
    uma decisão que ninguém tomou, e é justamente a afirmação que faz o operador
    parar de olhar.
    """
    if valor in (None, ""):
        return f'<span class="text-secondary">{esc(translate("table.not_declared", lang))}</span>'
    return f'<span class="font-monospace">{esc(valor)}</span>'


def team_chip(team_id: Optional[str], team_aliases: Optional[Dict[str, str]], lang: str) -> str:
    """Célula do time: o apelido quando ele existe, o id quando não existe.

    O `/key/list` do LiteLLM devolve `team_alias` SEMPRE nulo — o apelido só
    existe no `/team/list`. Por isso o rótulo legível chega aqui de fora, num
    mapa `{team_id: team_alias}`; sem ele a tela mostraria o UUID cru, que é
    correto mas ilegível. O id fica no `title` mesmo quando há apelido: quem
    precisa casar a tela com a API continua conseguindo.
    """
    if not team_id:
        return f'<span class="text-secondary">{esc(translate("table.not_declared", lang))}</span>'
    apelido = (team_aliases or {}).get(team_id)
    if not apelido or apelido == team_id:
        return f'<span class="provider-chip" title="{esc(team_id)}">{esc(team_id)}</span>'
    return f'<span class="provider-chip" title="{esc(team_id)}">{esc(apelido)}</span>'


def render_key_details(key: Any, modal_id: str, refresh_margin: int, lang: str,
                       team_aliases: Optional[Dict[str, str]] = None) -> str:
    """Modal com o que não cabe na linha da chave virtual.

    Limites, teto de orçamento, instante de expiração e lista de modelos são
    dado de diagnóstico: espremidos na tabela, empurravam as colunas úteis para
    fora da tela. O token NUNCA entra aqui — o apelido já chega mascarado.
    """
    dados = key.to_dict(refresh_margin)
    linhas = [
        (translate("table.team", lang), team_chip(dados.get("teamId"), team_aliases, lang)),
        (translate("table.status", lang), health_badge(dados["healthStatus"], lang)),
        (translate("table.remaining", lang), render_remaining(dados["remainingSeconds"], lang)),
        (translate("table.expires_at", lang),
         f'<span class="font-monospace">{esc(format_timestamp(dados["expiresAt"]))}</span>'
         if dados.get("expiresAt")
         else f'<span class="text-secondary">{esc(translate("table.not_declared", lang))}</span>'),
        (translate("table.rpm_limit", lang), render_optional_number(dados.get("rpmLimit"), lang)),
        (translate("table.tpm_limit", lang), render_optional_number(dados.get("tpmLimit"), lang)),
        (translate("table.max_budget", lang), render_optional_number(dados.get("maxBudget"), lang)),
        (translate("table.spend", lang), f'<span class="font-monospace">{esc(f"{key.spend:.4f}")}</span>'),
    ]
    extra = ""
    modelos = dados.get("models") or []
    if modelos:
        itens = "".join(f'<li class="font-monospace small">{esc(m)}</li>' for m in modelos)
        extra = (f'<p class="text-secondary small mb-1 mt-3">{esc(translate("table.models", lang))}</p>'
                 f'<ul class="mb-0">{itens}</ul>')
    return render_detail_modal(modal_id, dados["alias"], linhas, lang, extra)


def render_key_issued(key: Any, lang: str) -> str:
    """Quando a chave foi emitida -- o unico carimbo de tempo que ela tem.

    Chave virtual nao se renova: ela nasce com prazo e vence. A coluna existe
    para casar com a dos irmaos, e o que cabe nela aqui e a emissao.
    """
    carimbo = getattr(key, "created_at", None)
    if not carimbo:
        return f'<span class="text-secondary">{esc(translate("table.never_refreshed", lang))}</span>'
    icone = '<i class="bi bi-clock me-1 text-secondary" aria-hidden="true"></i>'
    return f'{icone}<span class="font-monospace">{esc(format_timestamp_curto(carimbo))}</span>'



def render_keys_table(keys: List[Any], refresh_margin: int, lang: str,
                      team_aliases: Optional[Dict[str, str]] = None) -> str:
    """Chaves virtuais emitidas pelo proxy, uma por linha."""
    if not keys:
        return f"""
        <div class="text-center text-secondary py-5">
          <i class="bi bi-inbox fs-1 d-block mb-2" aria-hidden="true"></i>
          {esc(translate("keys.empty", lang))}
        </div>"""

    rows = []
    detalhes = []
    # Identificador do modal pelo ÍNDICE, nunca pelo apelido: um apelido pode
    # conter espaço, acento ou barra, e nada disso vale como id de elemento.
    for indice, key in enumerate(keys):
        modal_id = f"detalhe-chave-{indice}"
        team_cell = team_chip(key.team_id, team_aliases, lang)
        # O apelido já chega mascarado quando a chave não tem nome: quem monta a
        # identificação é o modelo, para que a máscara valha em toda saída.
        rows.append(f"""
            <tr>
              <td>{team_cell}</td>
              <td class="fw-semibold font-monospace">{esc(key.alias)}</td>
              <td class="text-nowrap">
                <i class="bi bi-key me-1 text-secondary" aria-hidden="true"></i>{esc(translate("type.virtual_key", lang))}
              </td>
              <td>{health_badge(key.health_status(refresh_margin), lang)}</td>
              <td class="text-nowrap">{render_remaining(key.remaining_seconds, lang)}</td>
              <td class="text-nowrap small">{render_key_issued(key, lang)}</td>
              <td class="text-end">{detail_button(modal_id, lang)}</td>
            </tr>""")
        detalhes.append(render_key_details(key, modal_id, refresh_margin, lang, team_aliases))

    return f"""
        <div class="table-responsive">
          <table class="table table-dark table-hover align-middle mb-0 tabela-dominio">
            <colgroup>
              <col class="c-provedor"><col class="c-nome"><col class="c-tipo">
              <col class="c-status"><col class="c-validade"><col class="c-renovacao">
              <col class="c-detalhe">
            </colgroup>
            <thead>
              <tr>
                <th scope="col">{esc(translate("table.provider", lang))}</th>
                <th scope="col">{esc(translate("table.name", lang))}</th>
                <th scope="col">{esc(translate("table.type", lang))}</th>
                <th scope="col">{esc(translate("table.status", lang))}</th>
                <th scope="col">{esc(translate("table.remaining", lang))}</th>
                <th scope="col">{esc(translate("table.last_refresh", lang))}</th>
                <th scope="col" class="text-end">{esc(translate("table.details", lang))}</th>
              </tr>
            </thead>
            <tbody>{"".join(rows)}
            </tbody>
          </table>
        </div>
{"".join(detalhes)}"""


def credential_label(model: Any, lang: str) -> str:
    """De ONDE vem a credencial do modelo — nunca qual é ela.

    A chave declarada no cadastro é um segredo e não aparece em lugar nenhum da
    página; o que o operador precisa saber é se há credencial e onde ela mora.

    Sobre o último caso, que é o mais comum e o menos óbvio: o `/model/info` do
    LiteLLM **remove** `api_key` da resposta — `pop("api_key", None)`, antes de
    qualquer mascaramento. Um modelo com chave perfeitamente válida chega aqui
    sem campo nenhum, e chamar isso de "nenhuma credencial declarada" afirmaria
    algo falso sobre o cadastro: manda o operador procurar uma configuração que
    já existe. O que houve foi outra coisa — o gateway não expôs o campo — e é
    isso que a tela diz. `credential.env` e `credential.inline` continuam aqui
    porque descrevem o cadastro corretamente se um dia a rota voltar a devolvê-lo;
    hoje, por esse caminho, são inalcançáveis.
    """
    if model.key_is_env_reference:
        return translate("credential.env", lang)
    if model.uses_named_credential:
        return translate("credential.named", lang)
    if model.api_key:
        return translate("credential.inline", lang)
    return translate("credential.absent", lang)


def render_model_details(model: Any, modal_id: str, state: Optional[str], lang: str) -> str:
    """Modal com o que não cabe na linha do modelo.

    A `api_base` é um endereço inteiro e o nome do provedor é um identificador
    longo sem espaço: na célula, os dois ou estouram a coluna ou forçam a tabela
    a rolar inteira. A CHAVE do modelo nunca aparece — só de onde ela vem.
    """
    linhas = [
        (translate("table.provider", lang),
         f'<span class="provider-chip">{esc(model.provider)}</span>' if model.provider
         else f'<span class="text-secondary">{esc(translate("table.not_declared", lang))}</span>'),
        (translate("credential.title", lang), esc(credential_label(model, lang))),
        (translate("table.status", lang),
         health_badge(state, lang) if state
         else f'<span class="text-secondary">{esc(translate("health.not_checked", lang))}</span>'),
        (translate("table.api_base", lang),
         f'<span class="font-monospace">{esc(model.api_base)}</span>' if model.api_base
         else f'<span class="text-secondary">{esc(translate("table.not_declared", lang))}</span>'),
    ]
    return render_detail_modal(modal_id, model.name, linhas, lang)


def render_models_table(models: List[Any], states: Dict[str, str], lang: str) -> str:
    """Modelos cadastrados e a procedência da credencial de cada um."""
    if not models:
        return f"""
        <div class="text-center text-secondary py-4">
          <i class="bi bi-cpu fs-3 d-block mb-2" aria-hidden="true"></i>
          {esc(translate("models.empty", lang))}
        </div>"""

    rows = []
    detalhes = []
    for indice, model in enumerate(models):
        modal_id = f"detalhe-modelo-{indice}"
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
              <td class="text-end">{detail_button(modal_id, lang)}</td>
            </tr>""")
        detalhes.append(render_model_details(model, modal_id, state, lang))

    return f"""
        <div class="table-responsive">
          <table class="table table-dark table-hover align-middle mb-0 tabela-dominio">
            <colgroup>
              <col class="c-nome"><col class="c-chip"><col class="c-credencial">
              <col class="c-status"><col class="c-detalhe">
            </colgroup>
            <thead>
              <tr>
                <th scope="col">{esc(translate("table.name", lang))}</th>
                <th scope="col">{esc(translate("table.provider", lang))}</th>
                <th scope="col">{esc(translate("credential.title", lang))}</th>
                <th scope="col">{esc(translate("table.status", lang))}</th>
                <th scope="col" class="text-end">{esc(translate("table.details", lang))}</th>
              </tr>
            </thead>
            <tbody>{"".join(rows)}
            </tbody>
          </table>
        </div>
{"".join(detalhes)}"""


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


def render_cron_history(history: List[Dict[str, Any]], lang: str) -> str:
    """Lista de execuções do agendador, cada uma com o log do que aconteceu.

    O contador sozinho não distingue "nada a relatar" de "a inspeção falhou".
    O log de cada ciclo é o que responde a essa pergunta sem obrigar ninguém a
    abrir o arquivo de log do serviço.
    """
    if not history:
        return f'<p class="text-secondary small mb-0">{esc(translate("cron.no_runs", lang))}</p>'

    items = []
    for index, entry in enumerate(history):
        failed = not entry.get("success", True) or entry.get("error")
        tone = "danger" if failed else "secondary"
        icon = "bi-exclamation-octagon-fill" if failed else "bi-check-circle"
        log_lines = entry.get("log") or []
        if entry.get("error") and not any(str(entry["error"]) in line for line in log_lines):
            log_lines = [f"ERRO: {entry['error']}", *log_lines]

        body = (
            "<pre class=\"cron-log mb-0\">" + esc("\n".join(log_lines)) + "</pre>"
            if log_lines
            else f'<p class="text-secondary small mb-0">{esc(translate("cron.no_runs", lang))}</p>'
        )

        items.append(f"""
          <div class="accordion-item">
            <h3 class="accordion-header">
              <button class="accordion-button collapsed py-2" type="button"
                      data-bs-toggle="collapse" data-bs-target="#ciclo{index}"
                      aria-expanded="false" aria-controls="ciclo{index}">
                <span class="d-flex align-items-center gap-2 w-100 pe-3">
                  <i class="bi {icon} text-{tone}" aria-hidden="true"></i>
                  <span class="font-monospace small">{esc(format_timestamp(entry.get("timestamp")))}</span>
                  <span class="ms-auto small text-secondary">
                    {esc(translate("cron.result_line", lang,
                                   inspected=entry.get("totalInspected", 0),
                                   findings=entry.get("findingsCount", 0),
                                   duration=entry.get("durationMs", 0)))}
                  </span>
                </span>
              </button>
            </h3>
            <div id="ciclo{index}" class="accordion-collapse collapse">
              <div class="accordion-body py-2">{body}</div>
            </div>
          </div>""")

    return f'<div class="accordion accordion-flush" id="historicoCron">{"".join(items)}</div>'


def render_cron_card(cron: Dict[str, Any], lang: str) -> str:
    """Cartão do agendador, no vocabulário deste sincronizador.

    Onde os irmãos contam tokens renovados, aqui se conta o que a inspeção
    encontrou: o ciclo daqui é somente leitura, e um rótulo de renovação
    prometeria uma correção que ninguém aplicou.
    """
    active = bool(cron.get("active"))
    state_icon = "bi-broadcast text-success" if active else "bi-pause-circle text-secondary"
    state_text = (
        translate("cron.active", lang, interval=cron.get("intervalSeconds", "—"))
        if active
        else translate("cron.disabled", lang)
    )
    last = cron.get("lastResult") or {}
    failed = bool(last) and (not last.get("success", True) or last.get("error"))

    return f"""
      <div class="card h-100">
        <div class="card-header d-flex align-items-center justify-content-between">
          <span class="d-inline-flex align-items-center gap-2">
            <i class="bi bi-alarm" aria-hidden="true"></i>{esc(translate("cron.title", lang))}
          </span>
          <div class="d-flex gap-2">
            <button class="btn btn-outline-light btn-sm" type="button"
                    data-bs-toggle="modal" data-bs-target="#modalHistorico">
              <i class="bi bi-list-columns-reverse me-1" aria-hidden="true"></i>Logs
              {'<span class="badge text-bg-danger ms-1">!</span>' if failed else ""}
            </button>
            <form method="post" action="/acoes/cron" class="m-0">
              <button class="btn btn-outline-light btn-sm" type="submit">
                <i class="bi bi-play-fill me-1" aria-hidden="true"></i>{esc(translate("cron.run_now", lang))}
              </button>
            </form>
          </div>
        </div>
        <div class="card-body">
          <p class="d-flex align-items-center gap-2 mb-3">
            <i class="bi {state_icon}" aria-hidden="true"></i><span>{esc(state_text)}</span>
          </p>
          <dl class="row mb-0 small">
            <dt class="col-4 text-secondary fw-normal">{esc(translate("cron.next_run", lang))}</dt>
            <dd class="col-8 text-end font-monospace text-nowrap">{esc(format_timestamp(cron.get("nextRunAt")))}</dd>
            <dt class="col-4 text-secondary fw-normal">{esc(translate("cron.total_findings", lang))}</dt>
            <dd class="col-8 text-end font-monospace">{esc(cron.get("totalFindings", 0))}</dd>
            <dt class="col-4 text-secondary fw-normal mt-2">{esc(translate("cron.last_result", lang))}</dt>
            <dd class="col-8 text-end font-monospace small mb-0 mt-2 {'text-danger' if failed else ''}">
              {esc(translate("cron.result_line", lang,
                             inspected=last.get("totalInspected", 0),
                             findings=last.get("findingsCount", 0),
                             duration=last.get("durationMs", 0))
                   if last else translate("cron.no_runs", lang))}
              {esc(last.get("error") or "")}
            </dd>
          </dl>
        </div>
      </div>"""


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
    cron: Dict[str, Any],
    proxy: Dict[str, Any],
    current_user: str,
    is_default_password: bool,
    refresh_margin: int,
    auth_from_env: bool = False,
    flash: Optional[Dict[str, str]] = None,
    lang: str = DEFAULT_LANGUAGE,
    team_aliases: Optional[Dict[str, str]] = None,
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
  <link rel="icon" href="{FAVICON}">
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
      --bg:        #6D0808;   /* fundo da pagina */
      --surface:   #7d1414;   /* cartao */
      --surface-2: #8c1c1c;   /* cabecalho de cartao, chip */
      --line:      #a32626;   /* borda */
      --accent:    #ffce6b;   /* acao primaria */
      --accent-2:  #ffe0a3;   /* acao secundaria, realce */
      --brand-a:   #c62d2d;   /* marca, inicio do gradiente -- um passo acima
                                 da linha: quando os dois valiam o mesmo, a
                                 borda e a marca viravam a mesma cor na tela e
                                 um dos dois papeis deixava de existir */
      --brand-b:   #ffce6b;   /* marca, fim do gradiente */
      --text:      #e6e8ee;
      --text-dim:  #e3b9b9;
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
    /* Tabelas de dominio: largura por coluna fixada, como nos irmaos. Sem isto
       o navegador reparte a sobra e a coluna do botao (i) fica tao larga quanto
       a do nome, empurrando o conteudo util para fora da tela. */
    .tabela-dominio {{ table-layout: fixed; }}
    .tabela-dominio th, .tabela-dominio td {{ padding: .6rem .5rem; vertical-align: top; }}
    /* Com table-layout:fixed a largura da coluna e lei, e text-nowrap
       (white-space:nowrap!important) sem overflow:hidden nao corta nem quebra:
       o excesso se desenha POR CIMA da coluna vizinha. Foi assim que a validade
       apareceu escrita sobre a data de renovacao. O corte com reticencias
       mantem a linha legivel; o texto inteiro fica no botao (i) da linha. */
    .tabela-dominio td, .tabela-dominio th {{ overflow: hidden; text-overflow: ellipsis; }}
    /* O cabecalho nao pode quebrar no meio da palavra ("Detalhe" / "s"). */
    .tabela-dominio th {{ white-space: nowrap; }}
    .tabela-dominio col.c-nome       {{ width: auto; }}
    .tabela-dominio col.c-chip       {{ width: 12rem; }}
    .tabela-dominio col.c-credencial {{ width: 14rem; }}
    .tabela-dominio col.c-status     {{ width: 9.5rem; }}
    .tabela-dominio col.c-validade   {{ width: 11rem; }}
    .tabela-dominio col.c-numero     {{ width: 7rem; }}
    .tabela-dominio col.c-detalhe    {{ width: 5.5rem; }}
    /* O apelido da chave e o nome do modelo sao identificadores longos e sem
       espaco: sem isto eles estouram a celula em vez de quebrar. */
    .tabela-dominio td, .tabela-dominio .provider-chip {{ overflow-wrap: anywhere; }}
    .tabela-dominio .provider-chip {{ display: inline-block; max-width: 100%; white-space: normal; }}
    /* Em tela estreita a tabela rola sozinha, em vez de espremer as colunas ate
       o texto virar uma palavra por linha. */
    @media (max-width: 1200px) {{
      .tabela-dominio {{ min-width: 58rem; }}
    }}
    /* A marca e icone BRANCO sobre um tom claro do proprio tema. O gradiente
       de duas cores fazia as tres telas parecerem a mesma marca em cores
       diferentes; com a forma do icone distinta e o fundo discreto, quem
       identifica o produto e o desenho, e a cor fica por conta do tema. */
    .brand-mark {{ width: 2.25rem; height: 2.25rem; display: grid; place-items: center; border-radius: .5rem;
                   background: color-mix(in srgb, var(--brand-b) 22%, transparent);
                   border: 1px solid color-mix(in srgb, var(--brand-b) 45%, transparent);
                   color: #fff; font-size: 1.15rem; }}
    .list-group-item {{ background: var(--surface); color: var(--text); border-color: var(--line); }}
    .accordion-item, .accordion-button {{ background: var(--surface); color: var(--text); }}
    .accordion-button:not(.collapsed) {{ background: var(--surface-2); color: #fff; box-shadow: none; }}
    .cron-log {{ white-space: pre-wrap; word-break: break-word; font-size: .8rem; color: var(--text-dim);
                 background: var(--bg); border: 1px solid var(--line); border-radius: .35rem; padding: .6rem; }}
    /* O botao primario segue o acento do produto, em vez do azul fixo do
       Bootstrap: senao os tres mudam de fundo e ficam com o mesmo botao, o que
       faz a identidade parecer acidental. */
    .btn-primary {{ --bs-btn-bg: var(--accent); --bs-btn-border-color: var(--accent);
                    --bs-btn-hover-bg: var(--accent-2); --bs-btn-hover-border-color: var(--accent-2);
                    --bs-btn-active-bg: var(--accent-2); --bs-btn-active-border-color: var(--accent-2);
                    --bs-btn-color: var(--bg); --bs-btn-hover-color: var(--bg); --bs-btn-active-color: var(--bg); }}
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
        <form method="post" action="/acoes/cron" class="m-0">
          <button class="btn btn-primary btn-sm" type="submit">
            <i class="bi bi-arrow-repeat me-1" aria-hidden="true"></i>{esc(translate("action.sync_now", lang))}
          </button>
        </form>
        <form method="post" action="/logout" class="m-0">
          <button class="btn btn-outline-light btn-sm" type="submit">
            <i class="bi bi-box-arrow-right me-1" aria-hidden="true"></i>{esc(translate("auth.logout", lang))}
          </button>
        </form>
      </div>
    </header>

    <div class="row g-3 mb-4">{metrics}
    </div>

    <div class="row g-3 mb-4">
      <div class="col-lg-6">{render_proxy_card(proxy, lang)}</div>
      <div class="col-lg-6">{render_cron_card(cron, lang)}</div>
    </div>

    <div class="card mb-4">
      <div class="card-header d-flex align-items-center justify-content-between">
        <span class="d-inline-flex align-items-center gap-2">
          <i class="bi bi-sliders" aria-hidden="true"></i>{esc(translate("limits.title", lang))}
        </span>
        <span class="badge text-bg-dark">{len(findings)}</span>
      </div>
      {render_limits_card(findings, lang)}
    </div>

    <div class="card mb-4">
      <div class="card-header d-flex align-items-center justify-content-between">
        <span class="d-inline-flex align-items-center gap-2">
          <i class="bi bi-key" aria-hidden="true"></i>{esc(translate("keys.title", lang))}
        </span>
        <span class="badge text-bg-dark">{len(keys)}</span>
      </div>
      {render_keys_table(keys, refresh_margin, lang, team_aliases)}
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
        <button class="btn btn-link btn-sm p-0 ms-2 align-baseline text-secondary"
                data-bs-toggle="modal" data-bs-target="#modalCredenciais">
          <i class="bi bi-key me-1" aria-hidden="true"></i>{esc(translate("action.change_credentials", lang))}
        </button>
      </span>
      <span>
        <i class="bi bi-clock-history me-1" aria-hidden="true"></i>{esc(translate("footer.generated", lang))}
        <span class="font-monospace">{esc(generated_at)}</span>
      </span>
    </footer>
  </div>

  <div class="modal fade" id="modalHistorico" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
      <div class="modal-content">
        <div class="modal-header">
          <h2 class="modal-title h6 d-inline-flex align-items-center gap-2">
            <i class="bi bi-list-columns-reverse" aria-hidden="true"></i>{esc(translate("cron.title", lang))}
          </h2>
          <button type="button" class="btn-close" data-bs-dismiss="modal"
                  aria-label="{esc(translate("action.close", lang))}"></button>
        </div>
        <div class="modal-body">{render_cron_history(cron.get("history") or [], lang)}
        </div>
      </div>
    </div>
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

      // O aviso da ultima acao viaja na querystring (POST-Redirect-GET, para o
      // F5 nao repetir a acao). O efeito colateral e que ele fica: a URL guarda
      // o texto, e recarregar traz de volta uma mensagem de algo que ja
      // aconteceu. Assim que a pagina desenha, a querystring e limpa do
      // historico -- sem nova requisicao -- e o aviso some sozinho.
      var $aviso = $('[data-aviso]');
      if ($aviso.length) {{
        if (window.history.replaceState) {{
          window.history.replaceState({{}}, document.title, window.location.pathname);
        }}
        window.setTimeout(function () {{
          $aviso.fadeOut(400, function () {{ $(this).remove(); }});
        }}, 6000);
      }}
    }});
  </script>
</body>
</html>"""
