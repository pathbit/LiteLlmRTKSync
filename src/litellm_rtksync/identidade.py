"""Identidade deste produto — o único arquivo que pode divergir dos irmãos.

Os três sincronizadores da família RTKSync são clones em código. O que muda
entre eles é cor, nome, logo, porta e a quem cada um se conecta, e tudo isso
mora aqui. Depois deste arquivo, nenhum outro módulo do pacote escreve uma cor
em hexadecimal nem o nome do produto ou do gateway à mão — `tests/
test_identidade_isolada.py` reprova se voltar a escrever.

Este módulo NÃO importa nada do pacote, de propósito: assim qualquer módulo
pode importá-lo sem risco de ciclo de importação.

O conjunto de nomes MAIÚSCULOS aqui é contrato fechado: `tests/
test_identidade_visual.py` compara o conjunto, e um nome a mais é uma peça que
só este painel sabe desenhar.
"""

# Nome comercial do produto, como aparece no cabeçalho e no título da aba.
NOME_DO_PRODUTO = "LiteLlmRTKSync"

# Gateway ao qual este sincronizador se conecta, escrito como a marca dele.
NOME_DO_GATEWAY = "LiteLLM"

# O mesmo gateway como identificador (minúsculo, sem espaço): é o que vai para
# campo de dados, rótulo de provedor e chave de configuração.
PROVEDOR_DO_GATEWAY = "litellm"

# Prefixo dos containers desta pilha no compose.
PREFIXO_DE_CONTAINER = "litellmrtk-"

# Portas padrão. O ambiente continua mandando (config.py lê a variável); aqui
# fica só o valor de fábrica, para não haver duas fontes de verdade.
PORTA_DO_PAINEL = 8083
PORTA_DE_METRICAS = 9093

# Ícone Bootstrap Icons da marca, no cabeçalho do painel.
ICONE_DO_PRODUTO = "bi-speedometer2"

# Desenho do ícone da aba: o(s) elemento(s) <path> de dentro do <g>. A moldura
# (viewBox, rect, transform) é comum aos três e fica em render.py.
GLIFO_DO_FAVICON = (
    "<path d='M8 4a.5.5 0 0 1 .5.5V6a.5.5 0 0 1-1 0V4.5A.5.5 0 0 1 8 4M3.732 "
    "5.732a.5.5 0 0 1 .707 0l.915.914a.5.5 0 1 1-.708.708l-.914-.915a.5.5 0 0 "
    "1 0-.707M2 10a.5.5 0 0 1 .5-.5h1.586a.5.5 0 0 1 0 1H2.5A.5.5 0 0 1 2 "
    "10m9.5 0a.5.5 0 0 1 .5-.5h1.5a.5.5 0 0 1 0 1H12a.5.5 0 0 1-.5-.5m.754-4."
    "246a.39.39 0 0 0-.527-.02L7.547 9.31a.91.91 0 1 0 1.302 1.258l3.434-4."
    "297a.39.39 0 0 0-.029-.518z'/>"
    "<path fill-rule='evenodd' d='M0 10a8 8 0 1 1 15.547 2.661c-.442 1.253-1."
    "845 1.602-2.932 1.25C11.309 13.488 9.475 13 8 13c-1.474 0-3.31.488-4.615."
    "911-1.087.352-2.49.003-2.932-1.25A8 8 0 0 1 0 10m8-7a7 7 0 0 0-6.603 9."
    "329c.203.575.923.876 1.68.63C4.397 12.533 6.358 12 8 12s3.604.532 4.923."
    "96c.757.245 1.477-.056 1.68-.631A7 7 0 0 0 8 3'/>"
)

# Fundo do ícone da aba. É o tom de superfície do tema (`--surface`), e não a
# cor-base: a cor-base numa moldura de 32px fica escura demais para distinguir
# uma aba da outra. Vai percent-encoded porque o SVG viaja dentro de uma URL.
COR_DO_FAVICON = "%237d1414"

# Paleta: os nove papéis cromáticos do tema. Existem nos três painéis, com
# valores diferentes em cada um — é nisto que a identidade visual consiste.
# O que NÃO está aqui (--text e os overrides --bs-*) vale o mesmo nos três e
# por isso fica em render.py.
PALETA = {
    "--bg": "#6D0808",
    "--surface": "#7d1414",
    "--surface-2": "#8c1c1c",
    "--line": "#a32626",
    "--accent": "#ffce6b",
    "--accent-2": "#ffe0a3",
    "--brand-a": "#c62d2d",
    "--brand-b": "#ffce6b",
    "--text-dim": "#e3b9b9",
}

# Identificador curto do produto, usado em nome de arquivo, de logger e de
# cookie. Derivado do nome comercial para não existir uma segunda grafia.
_APELIDO = NOME_DO_PRODUTO.lower()

# Cookie da sessão do painel e cookie de estado do SSO. Nomes distintos porque
# são credenciais distintas: cada um tem o seu domínio de assinatura.
NOME_DO_COOKIE = f"{_APELIDO}_sessao"
NOME_DO_COOKIE_DE_ESTADO = f"{_APELIDO}_estado_sso"

# As duas chaves de tradução que dependem do que ESTE gateway faz. O catálogo de
# `i18n.py` é o mesmo texto nos três irmãos; estas duas não podiam ser, porque o
# 9Router e o OmniRoute renovam credencial OAuth e o LiteLLM apenas inspeciona --
# não há OAuth para renovar lá. Chamar os três de "agendador de renovação"
# deixaria um deles mentindo na tela.
#
# Ficam aqui, e não no catálogo, porque este é o arquivo onde mora o que muda de
# produto para produto. O `i18n.py` sobrepõe estas por cima das comuns.
ROTULOS_DO_PRODUTO = {
    "en": {
        "cron.title": "Inspection scheduler",
        "cron.result_line": "{inspected} inspected · {findings} findings ({duration}ms)",
    },
    "pt": {
        "cron.title": "Agendador de inspeção",
        "cron.result_line": "{inspected} inspecionados · {findings} achados ({duration}ms)",
    },
    "es": {
        "cron.title": "Programador de inspección",
        "cron.result_line": "{inspected} inspeccionados · {findings} hallazgos ({duration}ms)",
    },
}
