"""Renderização server-side do dashboard.

Todo o HTML é montado aqui, no servidor, com os dados já embutidos. O navegador
nunca consulta o gateway: ele recebe a página pronta. Isso mantém a credencial
inteiramente do lado do servidor e faz o painel funcionar mesmo com JavaScript
desabilitado — o jQuery serve só para conforto.

A casca é a mesma dos projetos irmãos, de propósito: cabeçalho, cartões de
métrica, seletor de idioma, modais e rodapé são idênticos, e o que muda são os
tokens de cor e o CONTEÚDO das tabelas de domínio.

Os seis cartões existem nos três painéis, sempre, e nesta ordem: conexão com o
gateway, agendador, conexões monitoradas, chaves virtuais, modelos cadastrados
e combos de resiliência. Quando um gateway não tem o conceito, o cartão aparece
com o estado vazio explicando por quê — nunca some da tela. Assimetria entre os
três é pior que um cartão vazio: quem abre as três telas lado a lado precisa
encontrar as mesmas peças no mesmo lugar.

Todo grid pagina de dez em dez, por `paginacao.py`: o catálogo de um gateway
chega a centenas de modelos, e despejá-los de uma vez faz a tela rolar por
minutos. O contador do cabeçalho do cartão continua mostrando o TOTAL — a
paginação muda o que se vê, não o que existe.

Ícones: Bootstrap Icons e flag-icons (fontes/CSS de ícones), nunca emoji.
Idioma padrão: inglês, com português e espanhol no seletor de bandeiras.
"""

import html
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .i18n import DEFAULT_LANGUAGE, LANGUAGES, normalize_language, translate
from .identidade import (
    COR_DO_FAVICON,
    GLIFO_DO_FAVICON,
    ICONE_DO_PRODUTO,
    NOME_DO_PRODUTO,
    PALETA,
)
from .paginacao import recortar, render_paginacao

# Icone da aba, embutido como data URI: /favicon.ico responde 401 atras do
# Basic Auth, entao um arquivo servido deixaria a aba sem icone ate o
# operador autenticar -- e a pagina de erro nunca teria icone nenhum.
FAVICON = (
    "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    f"<rect width='32' height='32' rx='7' fill='{COR_DO_FAVICON}'/>"
    "<g transform='translate(6 6) scale(1.25)' fill='%23ffffff'>"
    f"{GLIFO_DO_FAVICON}</g></svg>"
)

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

# Papéis cromáticos na ordem em que o `:root` os declara, e o que cada um pinta.
# Os VALORES vêm de identidade.py; a ORDEM e a explicação são comuns aos três
# painéis — é nisto que a casca ser a mesma consiste.
PAPEIS_DO_TEMA = (
    ("--bg", "fundo da pagina"),
    ("--surface", "cartao"),
    ("--surface-2", "cabecalho de cartao, chip"),
    ("--line", "borda"),
    ("--accent", "acao primaria"),
    ("--accent-2", "acao secundaria, realce"),
    ("--brand-a", "marca, inicio do gradiente"),
    ("--brand-b", "marca, fim do gradiente"),
    ("--text-dim", "texto secundario"),
)

# Cor do texto: NÃO é papel cromático — vale o mesmo nos três painéis, e por
# isso fica aqui e não na identidade.
COR_DO_TEXTO = "#e6e8ee"

# Papéis que as páginas servidas antes do login precisam: login e erro têm
# cartão, borda e um botão, e mais nada.
PAPEIS_ANTES_DO_LOGIN = ("--bg", "--surface", "--line", "--accent")


def tokens_do_tema(recuo: str = "      ") -> str:
    """Monta as linhas `--token: valor;` do bloco `:root` do painel."""
    linhas = [
        f"{recuo}{token}:{' ' * max(1, 12 - len(token))}{PALETA[token]};   /* {papel} */"
        for token, papel in PAPEIS_DO_TEMA
    ]
    linhas.append(f"{recuo}--text:      {COR_DO_TEXTO};")
    return "\n".join(linhas)


def tokens_antes_do_login() -> str:
    """A fatia do tema que as páginas anteriores ao login usam, em uma linha."""
    valores = " ".join(f"{token}: {PALETA[token]};" for token in PAPEIS_ANTES_DO_LOGIN)
    return f"{valores} --text: {COR_DO_TEXTO};"


# Estado semântico -> (classe do badge, ícone). O rótulo sai de `health.<estado>`
# na hora de desenhar, e nunca fica guardado nesta tabela: um rótulo embutido
# aqui ficaria preso a um idioma e nunca seria traduzido.
#
# A tabela é a UNIÃO dos estados dos três gateways. Um estado que este gateway
# nunca emite não custa nada e mantém a apresentação idêntica nos três; uma
# tabela recortada por produto é como o mesmo estado passou a ser pintado de
# cores diferentes em telas que deveriam ser a mesma.
HEALTH_PRESENTATION = {
    "active": ("text-bg-success", "bi-check-circle-fill"),
    "valid": ("text-bg-success", "bi-check-circle-fill"),
    "expiring_soon": ("text-bg-warning", "bi-hourglass-split"),
    "expired": ("text-bg-danger", "bi-x-octagon-fill"),
    "rate_limited": ("text-bg-warning", "bi-pause-circle-fill"),
    "blocked": ("text-bg-secondary", "bi-slash-circle-fill"),
    "over_budget": ("text-bg-danger", "bi-cash-stack"),
    "no_expiration": ("text-bg-secondary", "bi-infinity"),
    "unknown": ("text-bg-secondary", "bi-question-circle-fill"),
    # Estados vindos da validação viva da credencial.
    "invalid": ("text-bg-danger", "bi-shield-exclamation"),
    "unreachable": ("text-bg-warning", "bi-plug"),
    "not_checked": ("text-bg-secondary", "bi-dash-circle"),
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


def render_notice_page(title: str, body: str, link_label: str = "",
                       refresh_url: str = "", meta_refresh: str = "") -> bytes:
    """Pagina autonoma para respostas fora do painel autenticado.

    E o que o navegador exibe quando o usuario aperta ESC no dialogo do Basic
    Auth, entao nao pode conter nem credencial nem dica de credencial.

    O refresh instala um `<meta http-equiv="refresh">`, e existe para o pouso do
    acesso federado: a volta do provedor NAO pode ser um 302 para "/", porque
    numa cadeia de redirecionamento iniciada em outro site o navegador nao envia
    o cookie `SameSite=Strict` no salto seguinte -- o operador cairia em
    "/login" com uma sessao valida no bolso. Um 200 com refresh quebra a cadeia,
    e a navegacao seguinte e de primeira parte.

    `refresh_url` recebe o DESTINO; `meta_refresh`, o conteudo bruto do `<meta>`
    -- a grafia que o `web.py` de um dos irmaos ainda usa. As duas convivem ate
    `web.py` convergir, e quem passar as duas ve `refresh_url` ganhar.
    """
    link = (
        f'<p><a href="/">{esc(link_label)}</a></p>' if link_label else ""
    )
    conteudo = f"0;url={refresh_url}" if refresh_url else meta_refresh
    refresh = (
        f'<meta http-equiv="refresh" content="{esc(conteudo)}">' if conteudo else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  {refresh}
  <link rel="icon" href="{FAVICON}">
  <title>{esc(title)}</title>
  <link rel="stylesheet" href="{BOOTSTRAP_CSS}">
  <link rel="stylesheet" href="{BOOTSTRAP_ICONS}">
  <style>body {{ background: {PALETA['--bg']}; }}</style>
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


def render_landing_page(lang: str = DEFAULT_LANGUAGE) -> bytes:
    """Pouso do retorno do provedor de identidade: "Entrando..." e vai para "/".

    NAO e um 302. Uma cadeia de redirecionamento iniciada em outro site nao
    carrega o cookie `SameSite=Strict` no salto seguinte, e o operador cairia na
    tela de login com a sessao valida no bolso -- o sintoma pareceria senha
    errada. Esta pagina e navegacao nova, e o cookie viaja nela.

    O destino e SEMPRE "/": nenhum parametro da volta vira destino, ou o login
    federado viraria um redirecionamento aberto autenticado.
    """
    lang = normalize_language(lang)
    return f"""<!DOCTYPE html>
<html lang="{esc(lang)}" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <meta http-equiv="refresh" content="0;url=/">
  <link rel="icon" href="{FAVICON}">
  <title>{NOME_DO_PRODUTO}</title>
  <link rel="stylesheet" href="{BOOTSTRAP_CSS}">
  <link rel="stylesheet" href="{BOOTSTRAP_ICONS}">
  <style>body {{ background: {PALETA['--bg']}; }}</style>
</head>
<body class="d-flex align-items-center justify-content-center" style="min-height:100vh">
  <div class="card text-center" style="max-width:30rem">
    <div class="card-body p-4">
      <span class="spinner-border text-secondary mb-3" role="status" aria-hidden="true"></span>
      <h1 class="h5 mb-2">{esc(translate("sso.landing_title", lang))}</h1>
      <p class="text-secondary mb-3">{esc(translate("sso.landing_body", lang))}</p>
      <p class="mb-0"><a href="/">{esc(translate("auth.updated_link", lang))}</a></p>
    </div>
  </div>
</body>
</html>""".encode("utf-8")


def render_login_page(
    lang: str = DEFAULT_LANGUAGE,
    erro: str = "",
    desafio: str = "",
    dificuldade: int = 4,
    sso_nome: str = "",
    sso_indisponivel: bool = False,
    # `sso` e a grafia do irmao cujo `web.py` entrega a configuracao inteira em
    # vez do nome ja resolvido. As duas convivem ate `web.py` convergir.
    sso: Any = None,
) -> bytes:
    """Formulario de entrada, com a mesma casca e a mesma paleta do painel.

    Existe porque o dialogo do Basic Auth e uma janela do NAVEGADOR: nao se
    traduz, nao se estiliza, nao oferece logout e nao e HTML -- qualquer
    ferramenta que dirija um navegador para no dialogo, porque nao ha nada na
    pagina para preencher. Esta pagina resolve os quatro de uma vez.
    """
    lang = normalize_language(lang)
    # O quinto argumento chega nas duas grafias, e num dos irmaos ele e
    # posicional: objeto no lugar de texto e a configuracao, e o nome sai dela.
    if sso_nome and not isinstance(sso_nome, str):
        sso, sso_nome = sso_nome, ""
    if sso is not None and not sso_nome:
        sso_nome = sso.nome_do_provedor() if sso.esta_ligado() else ""
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
    # O botao do SSO e um LINK, nunca um `<form>`: a CSP do painel declara
    # `form-action 'self'` e o navegador bloqueia, sem erro visivel na tela, a
    # submissao que redireciona para fora. Ele fica AO LADO do formulario local,
    # que nao sai da tela em configuracao nenhuma -- se o provedor de identidade
    # cair, ninguem entraria.
    botao_sso = ""
    if sso_nome:
        botao_sso = (
            f'<div class="d-flex align-items-center gap-2 my-3 text-secondary small">'
            f'<hr class="flex-grow-1 my-0"><span>{esc(translate("sso.or", lang))}</span>'
            f'<hr class="flex-grow-1 my-0"></div>'
            f'<a class="btn btn-outline-light w-100 d-inline-flex align-items-center '
            f'justify-content-center gap-2" href="/sso/oidc/iniciar" rel="nofollow">'
            f'<i class="bi bi-shield-check" aria-hidden="true"></i>'
            f'{esc(translate("sso.sign_in_with", lang, provider=sso_nome))}</a>'
        )
    elif sso_indisponivel:
        # O provedor esta configurado, mas a imagem nao tem a biblioteca dele.
        # Dizer isso e melhor do que esconder o botao e deixar a pergunta aberta.
        botao_sso = (
            '<p class="text-secondary small mt-3 mb-0 d-flex align-items-start gap-2">'
            '<i class="bi bi-plug" aria-hidden="true"></i>'
            f'<span>{esc(translate("sso.unavailable", lang))}</span></p>'
        )
    return f"""<!DOCTYPE html>
<html lang="{esc(lang)}" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <link rel="icon" href="{FAVICON}">
  <title>{NOME_DO_PRODUTO}</title>
  <link rel="stylesheet" href="{BOOTSTRAP_CSS}">
  <link rel="stylesheet" href="{BOOTSTRAP_ICONS}">
  <style>
    :root {{ {tokens_antes_do_login()} }}
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
        <i class="bi bi-shield-lock" aria-hidden="true"></i>{NOME_DO_PRODUTO}
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
      {botao_sso}
    </div>
  </main>
</body>
</html>""".encode("utf-8")


def health_badge(status: str, lang: str) -> str:
    """Monta o badge de saúde com ícone de fonte."""
    css, icon = HEALTH_PRESENTATION.get(status, HEALTH_PRESENTATION["unknown"])
    label = translate(f"health.{status}", lang)
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


def estado_vazio(mensagem: str, dica: str = "") -> str:
    """O bloco de estado vazio da familia: icone bi-inbox, uma frase e a dica.

    Os quatro cartoes de tabela usam exatamente este bloco. Cada um deles existe
    nos tres paineis por contrato; quando o gateway deste produto nao tem aquele
    conceito, ou ainda nao tem dado nenhum, o cartao continua na tela e a frase
    diz por que esta vazio AQUI. Assimetria de cartoes e pior que estado vazio.
    """
    complemento = f'\n          <div class="small mt-2">{esc(dica)}</div>' if dica else ""
    return f"""
        <div class="text-center text-secondary py-5">
          <i class="bi bi-inbox fs-1 d-block mb-2" aria-hidden="true"></i>
          {esc(mensagem)}{complemento}
        </div>"""


def detail_button(modal_id: str, lang: str) -> str:
    """Botao (i) da linha, que abre o modal de detalhe daquele item."""
    return f"""<button class="btn btn-outline-light btn-sm py-0 px-2" type="button"
                        data-bs-toggle="modal" data-bs-target="#{esc(modal_id)}"
                        title="{esc(translate("table.details", lang))}">
                  <i class="bi bi-info-circle" aria-hidden="true"></i>
                </button>"""


def render_detail_modal(modal_id: str, titulo: str, linhas: List[tuple], lang: str,
                        extra: str = "") -> str:
    """Modal de detalhe no formato que a familia usa: titulo, pares e um extra.

    O modal e devolvido como bloco solto para ser emitido DEPOIS da tabela: um
    `<div>` dentro de `<tbody>` e HTML invalido, e o navegador o move sozinho
    para fora -- o que transforma cada linha da tabela numa surpresa de layout.
    """
    corpo = "".join(
        f'<dt class="col-5 text-secondary fw-normal">{esc(rotulo)}</dt>'
        f'<dd class="col-7 text-end">{valor}</dd>'
        for rotulo, valor in linhas
    )
    return f"""
  <div class="modal fade" id="{esc(modal_id)}" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h2 class="modal-title h6 d-inline-flex align-items-center gap-2">
            <i class="bi bi-info-circle" aria-hidden="true"></i>{esc(titulo)}
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


def cabecalho_de_dominio(rows: List[str], lang: str) -> str:
    """A casca das tabelas de dominio: SEMPRE as mesmas sete colunas.

    Conexoes, chaves virtuais e modelos sao coisas diferentes lidas do mesmo
    jeito -- quem serve, como se chama, de que tipo e, como esta, quanto tempo
    resta, quando foi renovado, e o (i) que abre o resto. Uma casca so mantem a
    largura das colunas identica entre os cartoes e entre os tres paineis.
    """
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
        </div>"""


def grid_paginado(nome: str, tabela: str, estado: Dict[str, Any], consulta: Any,
                  lang: str, detalhes: str = "") -> str:
    """Envelope de um grid: a tabela, a barra de paginas e os modais das linhas.

    Todo grid do painel passa por aqui, e e o que garante as dez linhas por
    pagina em todos eles -- um grid que nao passasse seria justamente o que
    despejaria o catalogo inteiro na tela.

    O `id` do envelope e o destino do link da barra (`#grid-<nome>`): sem ele,
    trocar de pagina recarrega a tela no topo e o operador perde de vista a
    tabela que estava lendo.

    Os modais saem DEPOIS do envelope, e nunca de dentro da tabela: um `<div>`
    em `<tbody>` e HTML invalido, e o navegador o move sozinho para fora.
    """
    return f"""
        <div id="grid-{esc(nome)}">{tabela}{render_paginacao(estado, consulta, translate, lang)}
        </div>""" + detalhes


def render_remaining_seconds(remaining: Optional[int], lang: str) -> str:
    """Validade restante de um item que nao e conexao (chave virtual, modelo).

    Sem prazo declarado a chave e estatica: vale ate ser desativada, e isso e
    "sem expiracao" de verdade -- nao o dado ausente que render_remaining trata
    com cautela no caso do OAuth.
    """
    if remaining is not None:
        return esc(format_duration(remaining, lang))
    return f'<span class="text-secondary">{esc(translate("duration.no_expiry_short", lang))}</span>'


def render_timestamp_cell(carimbo: Optional[str], lang: str, icone: str) -> str:
    """Celula de carimbo de tempo curto, com o valor inteiro guardado no modal."""
    if not carimbo:
        return f'<span class="text-secondary">{esc(translate("table.never_refreshed", lang))}</span>'
    return (f'<i class="bi {icone} me-1 text-secondary" aria-hidden="true"></i>'
            f'<span class="font-monospace">{esc(format_timestamp_curto(carimbo))}</span>')


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

    O `/key/list` do gateway devolve `team_alias` SEMPRE nulo — o apelido só
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
    return render_timestamp_cell(getattr(key, "created_at", None), lang, "bi-clock")


def render_keys_table(keys: List[Any], refresh_margin: int, lang: str,
                      team_aliases: Optional[Dict[str, str]] = None,
                      consulta: Any = None) -> str:
    """Chaves virtuais emitidas pelo gateway, uma por linha, nas sete colunas."""
    if not keys:
        return estado_vazio(translate("keys.empty", lang))

    visiveis, estado = recortar(keys, "chaves", consulta)
    rows = []
    detalhes = []
    # Id do modal pelo INDICE, nunca pelo apelido: um apelido aceita espaco,
    # acento e barra, e nada disso vale como id de elemento HTML.
    for indice, key in enumerate(visiveis):
        modal_id = f"detalhe-chave-{indice}"
        # O apelido já chega mascarado quando a chave não tem nome: quem monta a
        # identificação é o modelo, para que a máscara valha em toda saída.
        rows.append(f"""
            <tr>
              <td>{team_chip(key.team_id, team_aliases, lang)}</td>
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

    return grid_paginado("chaves", cabecalho_de_dominio(rows, lang), estado, consulta,
                         lang, "".join(detalhes))


def credential_label(model: Any, lang: str) -> str:
    """De ONDE vem a credencial do modelo — nunca qual é ela.

    A chave declarada no cadastro é um segredo e não aparece em lugar nenhum da
    página; o que o operador precisa saber é se há credencial e onde ela mora.

    Sobre o último caso, que é o mais comum e o menos óbvio: o `/model/info` do
    gateway **remove** `api_key` da resposta — `pop("api_key", None)`, antes de
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
    nao_declarado = f'<span class="text-secondary">{esc(translate("table.not_declared", lang))}</span>'
    linhas = [
        (translate("table.provider", lang),
         f'<span class="provider-chip">{esc(model.provider)}</span>' if model.provider
         else nao_declarado),
        (translate("credential.title", lang), esc(credential_label(model, lang))),
        (translate("table.status", lang),
         health_badge(state, lang) if state
         else f'<span class="text-secondary">{esc(translate("health.not_checked", lang))}</span>'),
        (translate("table.api_base", lang),
         f'<span class="font-monospace">{esc(model.api_base)}</span>' if model.api_base
         else nao_declarado),
    ]
    return render_detail_modal(modal_id, model.name, linhas, lang)


def render_models_table(models: List[Any], states: Dict[str, str], lang: str,
                        estado_do_catalogo: str = "ok", consulta: Any = None) -> str:
    """Modelos cadastrados, nas mesmas sete colunas dos irmaos.

    Duas colunas ficam em travessao de proposito: um cadastro de modelo no
    gateway nao tem validade nem renovacao -- quem expira e a credencial no
    provedor, e isso o gateway nao conta. Travessao com a explicacao no modal e
    honesto; inventar uma data nao seria.

    `estado_do_catalogo` carrega POR QUE a lista veio vazia. Sem isso, "nenhum
    modelo" e "nao deu para perguntar" desenham a mesma tela, e o operador vai
    procurar um cadastro faltando quando o problema era o gateway nao ter
    respondido.
    """
    if not models:
        motivo = {
            "no_key": "models.no_key",
            "unreachable": "models.unreachable",
        }.get(estado_do_catalogo, "models.empty")
        return estado_vazio(translate(motivo, lang))

    visiveis, estado = recortar(models, "modelos", consulta)
    rows = []
    detalhes = []
    for indice, model in enumerate(visiveis):
        modal_id = f"detalhe-modelo-{indice}"
        provedor = (f'<span class="provider-chip">{esc(model.provider)}</span>'
                    if model.provider else '<span class="text-secondary">—</span>')
        # Sem validação viva ligada não há estado a mostrar: um badge cinza
        # dizendo "desconhecido" seria inventar um veredito que ninguém emitiu.
        veredito = states.get(model.name)
        estado_cell = (health_badge(veredito, lang) if veredito
                       else '<span class="text-secondary">—</span>')
        rows.append(f"""
            <tr>
              <td>{provedor}</td>
              <td class="fw-semibold font-monospace">{esc(model.name)}</td>
              <td class="text-nowrap">
                <i class="bi bi-cpu me-1 text-secondary" aria-hidden="true"></i>{esc(credential_label(model, lang))}
              </td>
              <td>{estado_cell}</td>
              <td class="text-secondary">—</td>
              <td class="text-secondary">—</td>
              <td class="text-end">{detail_button(modal_id, lang)}</td>
            </tr>""")
        detalhes.append(render_model_details(model, modal_id, veredito, lang))

    return grid_paginado("modelos", cabecalho_de_dominio(rows, lang), estado, consulta,
                         lang, "".join(detalhes))


# Gravidade dos vereditos de modelo, do PIOR para o melhor. A conexão agrega os
# modelos que ela serve, e agregar pela média esconderia justamente o caso que
# importa: um destino com nove modelos aceitos e um recusado está com problema.
GRAVIDADE_DO_ESTADO = (
    "invalid",
    "unreachable",
    "rate_limited",
    "unsupported",
    "unknown",
    "not_checked",
    "valid",
)


def worst_state(states: List[str]) -> Optional[str]:
    """O pior veredito de um conjunto, ou None quando ninguém emitiu veredito."""
    presentes = [e for e in states if e]
    if not presentes:
        return None
    for estado in GRAVIDADE_DO_ESTADO:
        if estado in presentes:
            return estado
    # Estado fora do vocabulário conhecido não pode ser promovido a "ok": o
    # badge já cai em "desconhecido", e é isso que a tela deve dizer.
    return presentes[0]


def render_connection_details(connection: Any, state: Optional[str], lang: str,
                              modal_id: str) -> str:
    """Modal com o que não cabe na linha da conexão.

    O endereço do destino é uma URL inteira e a lista de modelos servidos pode
    ter dezenas de nomes: os dois estouram a coluna. A CREDENCIAL nunca aparece
    por valor — só o nome que o operador deu a ela no gateway.
    """
    nao_declarado = f'<span class="text-secondary">{esc(translate("table.not_declared", lang))}</span>'
    linhas = [
        (translate("table.provider", lang),
         f'<span class="provider-chip">{esc(connection.provider)}</span>' if connection.provider
         else nao_declarado),
        (translate("table.api_base", lang),
         f'<span class="font-monospace">{esc(connection.api_base)}</span>' if connection.api_base
         else nao_declarado),
        (translate("credential.title", lang),
         f'<span class="font-monospace">{esc(connection.credential_name)}</span>'
         if connection.credential_name
         else esc(credential_label(connection.models[0], lang)) if connection.models
         else nao_declarado),
        (translate("table.status", lang),
         health_badge(state, lang) if state
         else f'<span class="text-secondary">{esc(translate("health.not_checked", lang))}</span>'),
        (translate("table.remaining", lang),
         f'<span class="text-secondary">{esc(translate("connections.lifecycle_note", lang))}</span>'),
    ]
    extra = ""
    nomes = connection.model_names
    if nomes:
        itens = "".join(f'<li class="font-monospace small">{esc(n)}</li>' for n in nomes)
        extra = (f'<p class="text-secondary small mb-1 mt-3">'
                 f'{esc(translate("connections.served_models", lang))}</p>'
                 f'<ul class="mb-0">{itens}</ul>')
    return render_detail_modal(modal_id, connection.name, linhas, lang, extra)


def render_connections_table(connections: List[Any], model_states: Dict[str, str],
                             lang: str, consulta: Any = None) -> str:
    """Conexões monitoradas: os destinos por trás dos modelos cadastrados.

    As mesmas sete colunas dos irmãos. Duas delas ficam em travessão de
    propósito: um destino do gateway não tem validade nem renovação — quem
    expira é a credencial no provedor, e isso o gateway não conta. Travessão
    com a explicação no modal é honesto; inventar uma data não seria.
    """
    if not connections:
        return estado_vazio(translate("connections.empty", lang),
                            translate("connections.empty_hint", lang))

    visiveis, estado = recortar(connections, "conexoes", consulta)
    rows = []
    detalhes = []
    # Id do modal pelo ÍNDICE: o nome da conexão é uma URL ou um rótulo livre,
    # e nada disso vale como id de elemento.
    for indice, conexao in enumerate(visiveis):
        modal_id = f"detalhe-conexao-{indice}"
        veredito = worst_state([model_states.get(m.name) for m in conexao.models])
        estado_cell = (health_badge(veredito, lang) if veredito
                       else '<span class="text-secondary">—</span>')
        provedor = (f'<span class="provider-chip">{esc(conexao.provider)}</span>'
                    if conexao.provider else '<span class="text-secondary">—</span>')
        tipo = (credential_label(conexao.models[0], lang) if conexao.models
                else translate("credential.absent", lang))
        rows.append(f"""
            <tr>
              <td>{provedor}</td>
              <td class="fw-semibold">{esc(conexao.name)}
                <div class="small text-secondary">{len(conexao.models)} {esc(translate("table.models", lang))}</div>
              </td>
              <td class="text-nowrap">
                <i class="bi bi-hdd-network me-1 text-secondary" aria-hidden="true"></i>{esc(tipo)}
              </td>
              <td>{estado_cell}</td>
              <td class="text-secondary">—</td>
              <td class="text-secondary">—</td>
              <td class="text-end">{detail_button(modal_id, lang)}</td>
            </tr>""")
        detalhes.append(render_connection_details(conexao, veredito, lang, modal_id))

    return grid_paginado("conexoes", cabecalho_de_dominio(rows, lang), estado, consulta,
                         lang, "".join(detalhes))


def render_combos_table(combos: List[Dict[str, Any]], lang: str, consulta: Any = None) -> str:
    """Combos de resiliencia: o combo e a cascata que ele aciona.

    O tipo do fallback vira um chip ao lado do nome quando o gateway o
    classifica. Dois combos com o mesmo modelo principal e cascatas diferentes,
    sem dizer por que disparam, confundiriam; um gateway que nao classifica nao
    manda a chave, e a linha sai sem chip.
    """
    if not combos:
        return estado_vazio(translate("combos.empty", lang),
                            translate("combos.empty_hint", lang))

    visiveis, estado = recortar(combos, "combos", consulta)
    rows = []
    for combo in visiveis:
        models = combo.get("models") or []
        if isinstance(models, str):
            models = [models]
        preview = ", ".join(str(m) for m in models[:4])
        if len(models) > 4:
            preview += f" (+{len(models) - 4})"
        chave_do_tipo = combo.get("kindLabelKey") or ""
        chip = (f' <span class="provider-chip">{esc(translate(chave_do_tipo, lang))}</span>'
                if chave_do_tipo else "")
        rows.append(f"""
            <tr>
              <td class="fw-semibold">{esc(combo.get("name", "—"))}{chip}</td>
              <td class="small text-secondary">{esc(preview) or "—"}</td>
            </tr>""")

    tabela = f"""
        <div class="table-responsive">
          <table class="table table-dark table-hover align-middle mb-0">
            <thead>
              <tr>
                <th scope="col">{esc(translate("table.combo", lang))}</th>
                <th scope="col">{esc(translate("table.cascade", lang))}</th>
              </tr>
            </thead>
            <tbody>{"".join(rows)}
            </tbody>
          </table>
        </div>"""
    return grid_paginado("combos", tabela, estado, consulta, lang)


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
                    {esc(linha_do_ciclo(entry, lang))}
                  </span>
                </span>
              </button>
            </h3>
            <div id="ciclo{index}" class="accordion-collapse collapse">
              <div class="accordion-body py-2">{body}</div>
            </div>
          </div>""")

    return f'<div class="accordion accordion-flush" id="historicoCron">{"".join(items)}</div>'


def linha_do_ciclo(resultado: Dict[str, Any], lang: str) -> str:
    """O resumo de um ciclo: quantos foram olhados e o que o ciclo produziu.

    Os dois marcadores viajam juntos porque o trabalho do agendador muda com o
    gateway -- um renova credencial, outro relata achado -- e a frase traduzida
    usa o marcador do seu produto. `str.format` ignora o que sobra, entao passar
    os dois deixa a MESMA chamada correta nos tres paineis; escolher um faria a
    contagem do outro aparecer zerada na tela.
    """
    renovados = resultado.get("refreshedCount", resultado.get("findingsCount", 0))
    achados = resultado.get("findingsCount", resultado.get("refreshedCount", 0))
    return translate(
        "cron.result_line",
        lang,
        inspected=resultado.get("totalInspected", 0),
        refreshed=renovados,
        findings=achados,
        duration=resultado.get("durationMs", 0),
    )


def render_cron_card(cron: Dict[str, Any], lang: str) -> str:
    """Cartão do agendador: o estado do ciclo e o resultado da última passada."""
    active = bool(cron.get("active"))
    state_icon = "bi-broadcast text-success" if active else "bi-pause-circle text-secondary"
    state_text = (
        translate("cron.active", lang, interval=cron.get("intervalSeconds", "—"))
        if active
        else translate("cron.disabled", lang)
    )
    last = cron.get("lastResult") or {}
    failed = bool(last) and (not last.get("success", True) or last.get("error"))

    # O acumulado do agendador conta uma coisa em cada gateway: um soma
    # credenciais renovadas, o outro soma achados da inspecao. Quem diz qual e o
    # proprio estado, pelo contador que ele mantem -- rotular pelo produto faria
    # a tela prometer um numero que aquele ciclo nunca produz.
    if "totalFindings" in cron:
        rotulo_do_total, total_do_ciclo = "cron.total_findings", cron.get("totalFindings", 0)
    else:
        rotulo_do_total, total_do_ciclo = "cron.total_renewals", cron.get("totalRenewals", 0)

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
            <dt class="col-4 text-secondary fw-normal">{esc(translate(rotulo_do_total, lang))}</dt>
            <dd class="col-8 text-end font-monospace">{esc(total_do_ciclo)}</dd>
            <dt class="col-4 text-secondary fw-normal mt-2">{esc(translate("cron.last_result", lang))}</dt>
            <dd class="col-8 text-end font-monospace small mb-0 mt-2 {'text-danger' if failed else ''}">
              {esc(linha_do_ciclo(last, lang) if last else translate("cron.no_runs", lang))}
              {esc(last.get("error") or "")}
            </dd>
          </dl>
        </div>
      </div>"""


def render_gateway_card(gateway: Dict[str, Any], db_path: str, lang: str) -> str:
    """Cartão de liveness do gateway.

    Existe para que "o painel está de pé" e "o gateway está de pé" nunca sejam
    confundidos: são dois processos distintos, e o painel responde mesmo com o
    gateway fora.

    As linhas de banco e de diagnóstico só aparecem quando o estado TRAZ a
    bandeira `dbOk`. Um gateway que não guarda banco próprio não tem o que dizer
    ali, e desenhar "banco não encontrado" para ele afirmaria uma falha que não
    existe.
    """
    online = bool(gateway.get("online"))
    tone = "text-success" if online else "text-danger"
    icon = "bi-plug-fill" if online else "bi-plug"
    codigo = gateway.get("statusCode")
    label = (
        (f'ONLINE (HTTP {esc(codigo)})' if codigo else "ONLINE")
        if online
        else f'{esc(translate("gateway.offline", lang))} — '
             f'{esc(gateway.get("error") or translate("gateway.no_response", lang))}'
    )

    # Le a bandeira; o resumo textual nunca serve como booleano.
    tem_banco = "dbOk" in gateway
    db_ok = bool(gateway.get("dbOk"))
    if online and db_ok:
        diagnosis = translate("gateway.diag_ok", lang)
    elif online:
        diagnosis = translate("gateway.diag_db_failed", lang)
    else:
        diagnosis = translate("gateway.diag_gateway_failed", lang)

    bloco_do_banco = f"""
            <dt class="col-5 text-secondary fw-normal">{esc(translate("gateway.database", lang))}</dt>
            <dd class="col-7 text-end font-monospace text-truncate" title="{esc(db_path)}">
              {esc(translate("gateway.db_summary", lang,
                            connections=gateway.get("dbConnections", 0),
                            combos=gateway.get("dbCombos", 0))
                   if db_ok else translate("gateway.db_missing", lang))}
            </dd>
            <dt class="col-5 text-secondary fw-normal">{esc(translate("gateway.diagnostics", lang))}</dt>
            <dd class="col-7 text-end font-monospace mb-0 {tone}">{esc(diagnosis)}</dd>""" if tem_banco else ""

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
            <dd class="col-7 text-end font-monospace text-truncate">{esc(gateway.get("url") or "—")}</dd>
            <dt class="col-5 text-secondary fw-normal">{esc(translate("gateway.status", lang))}</dt>
            <dd class="col-7 text-end font-monospace {tone}">
              <i class="bi {icon} me-1" aria-hidden="true"></i>{label}
            </dd>
            <dt class="col-5 text-secondary fw-normal">{esc(translate("gateway.latency", lang))}</dt>
            <dd class="col-7 text-end font-monospace{'' if tem_banco else ' mb-0'}">{esc(gateway.get("latencyMs", "—"))} ms</dd>{bloco_do_banco}
          </dl>
        </div>
      </div>"""


def _campo_sso(rotulo: str, nome: str, valor: str, dica: str = "",
               tipo: str = "text", area: bool = False,
               somente_leitura: bool = False, prefixo: str = "oidc") -> str:
    """Um campo do formulario de acesso federado, com rotulo traduzido.

    O `prefixo` existe porque as duas abas desenham campos de mesmo nome. Sem
    ele, dois elementos dividiriam o mesmo `id` -- HTML invalido -- e o rotulo
    da aba SAML levaria o foco para o campo escondido da aba OIDC.
    """
    identificador = f"sso-{prefixo}-" + nome.replace("_", "-")
    ajuda = f'<div class="form-text">{esc(dica)}</div>' if dica else ""
    if area:
        controle = (
            f'<textarea class="form-control font-monospace" id="{identificador}" '
            f'name="{esc(nome)}" rows="4">{esc(valor)}</textarea>'
        )
    else:
        controle = (
            f'<input class="form-control" id="{identificador}" name="{esc(nome)}" '
            f'type="{esc(tipo)}" value="{esc(valor)}" autocomplete="off"'
            f'{" readonly" if somente_leitura else ""}>'
        )
    return f"""
              <div class="mb-3">
                <label class="form-label small" for="{identificador}">{esc(rotulo)}</label>
                {controle}
                {ajuda}
              </div>"""


def _confirmacao_sso(lang: str, prefixo: str = "oidc") -> str:
    """A senha local atual, exigida em toda gravacao de acesso federado.

    Quem sequestra uma sessao de oito horas pode apontar o painel para um
    provedor hostil e se pôr na lista de autorizados -- persistencia permanente
    ganha com uma sessao roubada. Pedir a senha local de novo fecha isso.
    """
    return f"""
              <hr class="my-3">
              <div class="mb-3">
                <label class="form-label small" for="sso-{prefixo}-usuario">{esc(translate("auth.user", lang))}</label>
                <input class="form-control" id="sso-{prefixo}-usuario" name="usuario"
                       autocomplete="username" required>
              </div>
              <div class="mb-3">
                <label class="form-label small" for="sso-{prefixo}-senha">{esc(translate("sso.current_password", lang))}</label>
                <input class="form-control" id="sso-{prefixo}-senha" name="senha" type="password"
                       autocomplete="current-password" required>
                <div class="form-text">{esc(translate("sso.confirm_hint", lang))}</div>
              </div>"""


def sso_disponivel_para_saml(sso: Any) -> bool:
    """A biblioteca de SAML esta nesta imagem?

    Perguntado ao modulo de SSO, e nao importado aqui: `render.py` nao conhece
    protocolo nenhum, e a aba desabilitada e uma decisao de tela.
    """
    from .sso import saml_disponivel

    return saml_disponivel()


def render_sso_modal(sso: Any, lang: str) -> str:
    """Corpo do modal de acesso federado: duas abas, um provedor por vez.

    A casca do modal (titulo, botao de fechar) e comum aos tres paineis e mora
    em `render_dashboard`; daqui sai so o CORPO, que e a parte presa ao
    formulario que `web.py` sabe receber.

    Um provedor de cada vez, e nunca os dois ligados: com dois emissores
    legitimos, uma resposta de um pode ser aceita como se fosse do outro
    (mix-up), e a allowlist passa a ter dois donos.

    O segredo do cliente NUNCA volta para a tela. O campo mostra que ha um
    valor guardado e permite substitui-lo; salvar em branco MANTEM o anterior.
    """
    if sso is None:
        # Quem nunca configurou nada tambem precisa do formulario: um modal que
        # so diz "desligado" nao tem como ligar coisa nenhuma.
        from .sso import ConfiguracaoSSO

        sso = ConfiguracaoSSO()

    if sso.desligado_no_ambiente:
        aviso_ambiente = f"""
          <div class="alert alert-warning d-flex align-items-center gap-2" role="note">
            <i class="bi bi-power" aria-hidden="true"></i>
            <div>{esc(translate("sso.disabled_by_env", lang))}</div>
          </div>"""
    else:
        aviso_ambiente = ""

    if sso.esta_ligado():
        situacao = translate("sso.status_on", lang, provider=sso.nome_do_provedor())
        tom = "text-bg-success"
    else:
        situacao = translate("sso.status_off", lang)
        tom = "text-bg-secondary"

    if sso.segredo_vem_do_ambiente:
        estado_do_segredo = translate("sso.secret_from_env", lang)
        campo_do_segredo = (
            '<input class="form-control" type="password" value="" disabled '
            'placeholder="••••••••">'
        )
    else:
        estado_do_segredo = translate(
            "sso.secret_stored" if sso.tem_segredo else "sso.secret_absent", lang
        )
        campo_do_segredo = (
            '<input class="form-control" id="sso-oidc-client-secret" name="client_secret" '
            'type="password" value="" autocomplete="new-password" placeholder="••••••••">'
        )

    retorno = sso.url_de_retorno() if sso.base_url else ""
    metadata = sso.entity_id() if sso.base_url else ""

    aba_oidc = f"""
            <form method="post" action="/acoes/sso" class="pt-3">
              <input type="hidden" name="enabled" value="oidc">
              {_campo_sso(translate("sso.base_url", lang), "base_url", sso.base_url,
                          translate("sso.base_url_hint", lang))}
              {_campo_sso(translate("sso.redirect_uri", lang), "redirect_uri_exibido", retorno,
                          somente_leitura=True)}
              {_campo_sso(translate("sso.issuer", lang), "issuer", sso.issuer)}
              {_campo_sso(translate("sso.client_id", lang), "client_id", sso.client_id)}
              <div class="mb-3">
                <label class="form-label small" for="sso-oidc-client-secret">{esc(translate("sso.client_secret", lang))}</label>
                {campo_do_segredo}
                <div class="form-text">{esc(estado_do_segredo)}</div>
              </div>
              {_campo_sso(translate("sso.scopes", lang), "scopes", sso.escopos)}
              {_campo_sso(translate("sso.allowed_domains", lang), "allowed_domains",
                          ", ".join(sso.dominios), translate("sso.allowlist_hint", lang))}
              {_campo_sso(translate("sso.allowed_emails", lang), "allowed_emails",
                          ", ".join(sso.emails))}
              {_confirmacao_sso(lang)}
              <div class="d-flex gap-2">
                <button class="btn btn-primary flex-grow-1" type="submit">
                  <i class="bi bi-save me-1" aria-hidden="true"></i>{esc(translate("sso.save", lang))}
                </button>
                <button class="btn btn-outline-light" type="submit" name="desligar" value="1">
                  <i class="bi bi-power me-1" aria-hidden="true"></i>{esc(translate("sso.status_off", lang))}
                </button>
              </div>
            </form>"""

    if not sso_disponivel_para_saml(sso):
        aba_saml = f"""
            <div class="alert alert-secondary d-flex align-items-center gap-2 mt-3 mb-0" role="note">
              <i class="bi bi-info-circle" aria-hidden="true"></i>
              <div>{esc(translate("sso.saml_unavailable", lang))}</div>
            </div>"""
    else:
        aba_saml = f"""
            <form method="post" action="/acoes/sso" class="pt-3">
              <input type="hidden" name="enabled" value="saml">
              {_campo_sso(translate("sso.base_url", lang), "base_url", sso.base_url,
                          translate("sso.base_url_hint", lang), prefixo="saml")}
              {_campo_sso(translate("sso.idp_entity_id", lang), "idp_entity_id",
                          sso.idp_entity_id, prefixo="saml")}
              {_campo_sso(translate("sso.idp_sso_url", lang), "idp_sso_url",
                          sso.idp_sso_url, prefixo="saml")}
              {_campo_sso(translate("sso.idp_cert", lang), "idp_cert", sso.idp_cert,
                          translate("sso.metadata_hint", lang, url=metadata), area=True,
                          prefixo="saml")}
              {_campo_sso(translate("sso.allowed_domains", lang), "allowed_domains",
                          ", ".join(sso.dominios), translate("sso.allowlist_hint", lang),
                          prefixo="saml")}
              {_campo_sso(translate("sso.allowed_emails", lang), "allowed_emails",
                          ", ".join(sso.emails), prefixo="saml")}
              {_confirmacao_sso(lang, "saml")}
              <button class="btn btn-primary w-100" type="submit">
                <i class="bi bi-save me-1" aria-hidden="true"></i>{esc(translate("sso.save", lang))}
              </button>
            </form>"""

    return f"""
          {aviso_ambiente}
          <p class="text-secondary small">{esc(translate("sso.intro", lang))}</p>
          <p class="small mb-3">
            <span class="text-secondary">{esc(translate("sso.provider", lang))}:</span>
            <span class="badge {tom}">{esc(situacao)}</span>
          </p>
          <ul class="nav nav-tabs" role="tablist">
            <li class="nav-item" role="presentation">
              <button class="nav-link active" data-bs-toggle="tab" data-bs-target="#abaOIDC"
                      type="button" role="tab">{esc(translate("sso.tab_oidc", lang))}</button>
            </li>
            <li class="nav-item" role="presentation">
              <button class="nav-link" data-bs-toggle="tab" data-bs-target="#abaSAML"
                      type="button" role="tab">{esc(translate("sso.tab_saml", lang))}</button>
            </li>
          </ul>
          <div class="tab-content">
            <div class="tab-pane fade show active" id="abaOIDC" role="tabpanel">{aba_oidc}
            </div>
            <div class="tab-pane fade" id="abaSAML" role="tabpanel">{aba_saml}
            </div>
          </div>"""


def render_dashboard(
    *,
    # Os seis cartoes, na ordem do contrato. Todos opcionais na assinatura: quem
    # chama sem um deles -- um teste, um script -- continua desenhando a pagina,
    # com o cartao no estado vazio, que e o comportamento correto.
    connections: Optional[List[Any]] = None,
    combos: Optional[List[Dict[str, Any]]] = None,
    keys: Optional[List[Any]] = None,
    models: Optional[List[Any]] = None,
    cron: Optional[Dict[str, Any]] = None,
    gateway: Optional[Dict[str, Any]] = None,
    db_path: str = "",
    router_url: str = "",
    current_user: str = "",
    is_default_password: bool = False,
    refresh_margin: int = 900,
    auth_from_env: bool = False,
    flash: Optional[Dict[str, str]] = None,
    lang: str = DEFAULT_LANGUAGE,
    # A query da requisicao, de onde sai a pagina de cada grid (`?pag_modelos=2`).
    # Ausente, todo grid abre na primeira pagina -- que e o que acontece hoje,
    # enquanto `web.py` ainda nao repassa a query.
    consulta: Optional[Dict[str, List[str]]] = None,
    # Estado que so um dos gateways produz. Chega por nome para que a MESMA
    # assinatura sirva aos tres `web.py`; o que este painel nao usa fica em
    # branco e nao aparece na tela.
    models_state: str = "ok",
    model_states: Optional[Dict[str, str]] = None,
    team_aliases: Optional[Dict[str, str]] = None,
    findings: Optional[List[Dict[str, Any]]] = None,
    counters: Optional[Dict[str, Any]] = None,
    # `proxy` e a grafia com que o `web.py` de um dos irmaos chama o gateway. As
    # duas apontam para o mesmo estado, e a segunda sai quando `web.py` convergir.
    proxy: Optional[Dict[str, Any]] = None,
    # Estado do SSO para a tela de configuracao, nas tres grafias que os
    # `web.py` ainda usam. Todas opcionais: sem nenhuma, o modal aparece no
    # estado desligado, que e o correto de um painel sem SSO configurado.
    sso_view: Optional[Dict[str, Any]] = None,
    sso: Any = None,
    sso_config: Optional[Dict[str, str]] = None,
    sso_tem_segredo: bool = False,
    sso_segredo_do_ambiente: bool = False,
    sso_desligado_pelo_ambiente: bool = False,
    sso_endereco_de_retorno: str = "",
) -> str:
    """Monta a página completa do dashboard, já com todos os dados embutidos."""
    lang = normalize_language(lang)
    connections = connections or []
    combos = combos or []
    keys = keys or []
    models = models or []
    cron = cron or {}
    counters = counters or {}
    findings = findings or []
    model_states = model_states or {}
    gateway = gateway or proxy or {}
    router_url = router_url or gateway.get("url") or ""
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    metrics = "".join([
        metric_card(translate("metric.virtual_keys", lang), counters.get("keys", 0), "bi-key", "text-info"),
        metric_card(translate("metric.teams", lang), counters.get("teams", 0), "bi-people", "text-primary"),
        metric_card(translate("metric.expiring", lang), counters.get("expiring", 0),
                    "bi-clock-history", "text-warning"),
        metric_card(translate("metric.limit_findings", lang), counters.get("limit_findings", 0),
                    "bi-sliders", "text-danger"),
    ])

    tabela_de_conexoes = render_connections_table(connections, model_states, lang, consulta)
    tabela_de_chaves = render_keys_table(keys, refresh_margin, lang, team_aliases, consulta)
    tabela_de_modelos = render_models_table(
        models, model_states, lang, models_state, consulta)
    tabela_de_combos = render_combos_table(combos, lang, consulta)
    corpo_do_sso = render_sso_modal(sso, lang)

    return f"""<!DOCTYPE html>
<html lang="{esc(lang)}" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <link rel="icon" href="{FAVICON}">
  <title>{NOME_DO_PRODUTO}</title>
  <link rel="stylesheet" href="{BOOTSTRAP_CSS}">
  <link rel="stylesheet" href="{BOOTSTRAP_ICONS}">
  <link rel="stylesheet" href="{FLAG_ICONS}">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="{GOOGLE_FONTS}">
  <style>
    /* ------------------------------------------------------------------
       Identidade visual: os tres paineis da familia RTKSync tem a MESMA
       estrutura e a MESMA folha de estilo. O que muda e o valor dos nove
       papeis cromaticos, e eles vivem todos em identidade.py.
       Trocar o produto e trocar aquele arquivo, nada mais.
       ------------------------------------------------------------------ */
    :root {{
{tokens_do_tema()}
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
    /* Abas do modal de acesso federado: pintadas com os tokens que ja existem, e
       nunca com tokens novos -- um token a mais aqui seria um componente que so
       um painel sabe desenhar. O Bootstrap deixa a aba inativa quase invisivel
       sobre superficie escura, e a ativa com a borda da propria pagina. */
    .nav-tabs {{ border-bottom-color: var(--line); }}
    .nav-tabs .nav-link {{ color: var(--text-dim); }}
    .nav-tabs .nav-link.active {{ background: var(--surface-2); color: var(--text);
                                  border-color: var(--line) var(--line) var(--surface-2); }}
    .form-control, .form-control:focus {{ background: var(--bg); color: var(--text);
                                          border-color: var(--line); box-shadow: none; }}
    /* Barra de paginas de cada grid. O Bootstrap desenha a paginacao clara, que
       sobre a superficie escura do painel fica ilegivel. */
    .barra-paginas {{ border-top: 1px solid var(--line); }}
    .pagination {{ --bs-pagination-bg: var(--surface); --bs-pagination-color: var(--accent-2);
                   --bs-pagination-border-color: var(--line);
                   --bs-pagination-hover-bg: var(--surface-2); --bs-pagination-hover-color: var(--accent);
                   --bs-pagination-hover-border-color: var(--line);
                   --bs-pagination-focus-bg: var(--surface-2); --bs-pagination-focus-color: var(--accent);
                   --bs-pagination-active-bg: var(--accent); --bs-pagination-active-border-color: var(--accent);
                   --bs-pagination-active-color: var(--bg);
                   --bs-pagination-disabled-bg: var(--surface); --bs-pagination-disabled-color: var(--text-dim);
                   --bs-pagination-disabled-border-color: var(--line); }}
    /* Barra de acoes do cabecalho: todos os controles com a MESMA altura. O
       seletor de idioma carrega so a bandeira, um elemento com altura propria;
       sem texto ao lado para definir a linha, ele esticava o botao. */
    .barra-acoes {{ display: flex; align-items: stretch; gap: .5rem; }}
    .barra-acoes > * {{ display: flex; align-items: center; }}
    .barra-acoes .btn {{ height: 2rem; padding-top: 0; padding-bottom: 0;
                         display: inline-flex; align-items: center; line-height: 1; }}
    .barra-acoes .fi {{ line-height: 1; }}
    /* A tabela de dominio tem sete colunas, e sem largura declarada o navegador
       as reparte pelo conteudo: a coluna com a frase mais longa recebia a menor
       fatia e quebrava em quatro linhas, enquanto "Tipo" e "Status", de largura
       fixa, sobravam espaco. Declarar a divisao resolve na origem, e
       `table-layout: fixed` faz o navegador respeita-la em vez de recalcular. */
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
    .tabela-dominio col.c-provedor    {{ width: 8rem; }}
    .tabela-dominio col.c-nome        {{ width: auto; }}
    .tabela-dominio col.c-tipo        {{ width: 9.5rem; }}
    .tabela-dominio col.c-status      {{ width: 9.5rem; }}
    .tabela-dominio col.c-validade    {{ width: 11rem; }}
    .tabela-dominio col.c-renovacao   {{ width: 10.5rem; }}
    .tabela-dominio col.c-detalhe     {{ width: 5.5rem; }}
    /* O nome do provedor e um identificador longo e sem espaco
       (openai-compatible-chat-ollama-local): sem isto ele estoura a coluna ou
       forca a tabela a rolar horizontalmente inteira. */
    .tabela-dominio .provider-chip {{ display: inline-block; max-width: 100%;
                                       overflow-wrap: anywhere; white-space: normal; }}
    .tabela-dominio .diagnostico {{ overflow-wrap: anywhere; }}
    /* Em tela estreita a tabela rola sozinha, em vez de espremer as colunas
       ate o texto virar uma palavra por linha. */
    @media (max-width: 1200px) {{
      .tabela-dominio {{ min-width: 68rem; }}
    }}
  </style>
</head>
<body>
  <div class="container-xl py-4">

    {render_flash(flash)}
    {render_security_banner(is_default_password, lang)}

    <header class="d-flex flex-wrap align-items-center justify-content-between gap-3 mb-4">
      <div class="d-flex align-items-center gap-3">
        <span class="brand-mark"><i class="bi {ICONE_DO_PRODUTO}" aria-hidden="true"></i></span>
        <div>
          <h1 class="h4 mb-0">{NOME_DO_PRODUTO}</h1>
          <p class="text-secondary small mb-0 font-monospace">
            {esc(router_url or translate("app.gateway_unset", lang))}
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
        <button class="btn btn-outline-light btn-sm" type="button"
                data-bs-toggle="modal" data-bs-target="#modalSSO">
          <i class="bi bi-gear me-1" aria-hidden="true"></i>{esc(translate("action.settings", lang))}
        </button>
        <form method="post" action="/logout" class="m-0">
          <button class="btn btn-outline-light btn-sm" type="submit">
            <i class="bi bi-box-arrow-right me-1" aria-hidden="true"></i>{esc(translate("auth.logout", lang))}
          </button>
        </form>
      </div>
    </header>

    <div class="row g-3 mb-4">{metrics}
    </div>

    <!-- Os seis cartoes, na ordem acordada para os tres paineis:
         1 conexao com o gateway - 2 agendador - 3 conexoes monitoradas
         4 chaves virtuais - 5 modelos cadastrados - 6 combos de resiliencia -->
    <div class="row g-3 mb-4">
      <div class="col-lg-6">{render_gateway_card(gateway, db_path, lang)}</div>
      <div class="col-lg-6">{render_cron_card(cron, lang)}</div>
    </div>

    <div class="card mb-4">
      <div class="card-header d-flex align-items-center justify-content-between">
        <span class="d-inline-flex align-items-center gap-2">
          <i class="bi bi-list-check" aria-hidden="true"></i>{esc(translate("connections.title", lang))}
        </span>
        <span class="badge text-bg-dark">{len(connections)}</span>
      </div>
      {tabela_de_conexoes}
    </div>

    <div class="card mb-4">
      <div class="card-header d-flex align-items-center justify-content-between">
        <span class="d-inline-flex align-items-center gap-2">
          <i class="bi bi-key" aria-hidden="true"></i>{esc(translate("keys.title", lang))}
        </span>
        <span class="badge text-bg-dark">{len(keys)}</span>
      </div>
      {tabela_de_chaves}
    </div>

    <div class="card mb-4">
      <div class="card-header d-flex align-items-center justify-content-between">
        <span class="d-inline-flex align-items-center gap-2">
          <i class="bi bi-cpu" aria-hidden="true"></i>{esc(translate("models.title", lang))}
        </span>
        <span class="badge text-bg-dark">{len(models)}</span>
      </div>
      {tabela_de_modelos}
    </div>

    <div class="card mb-4">
      <div class="card-header d-flex align-items-center justify-content-between">
        <span class="d-inline-flex align-items-center gap-2">
          <i class="bi bi-diagram-3" aria-hidden="true"></i>{esc(translate("combos.title", lang))}
        </span>
        <span class="badge text-bg-dark">{len(combos)}</span>
      </div>
      {tabela_de_combos}
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
      <!-- Assinatura da casa, igual nos tres paineis e fora do catalogo de
           traducao: e um nome proprio, nao um texto de interface. O ano vem do
           relogio -- um ano escrito a mao envelhece em silencio. -->
      <span class="w-100 text-center pt-2">
        Feito com <i class="bi bi-heart-fill" style="color: var(--bs-purple)" aria-hidden="true"></i>
        pela Pathbit - All rights reserved (c) {datetime.now().year}
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

  <div class="modal fade" id="modalSSO" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
      <div class="modal-content">
        <div class="modal-header">
          <h2 class="modal-title h6 d-inline-flex align-items-center gap-2">
            <i class="bi bi-shield-check" aria-hidden="true"></i>{esc(translate("sso.title", lang))}
          </h2>
          <button type="button" class="btn-close" data-bs-dismiss="modal"
                  aria-label="{esc(translate("action.close", lang))}"></button>
        </div>
        <div class="modal-body">{corpo_do_sso}
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
