"""Paginação dos grids do painel: dez linhas por página, sempre.

Um catálogo de gateway chega a centenas de modelos -- foram 550 numa medição --
e despejar isso numa página faz a tela rolar por minutos até o rodapé. Dez por
vez é o suficiente para olhar sem perder o resto de vista.

Três decisões que moldam este módulo, e valem registrar porque cada uma tem uma
alternativa óbvia e pior:

**Cada grid pagina sozinho.** O parâmetro carrega o nome do grid
(`?pag_modelos=2`), e não um `?pag=2` global. Com um só, avançar a página dos
modelos moveria junto a tabela de conexões, que ninguém pediu para mexer.

**O contador continua mostrando o total.** A paginação muda o que se vê, não o
que existe: um cabeçalho que passasse a dizer "10" depois de paginar faria a
tela mentir sobre o tamanho do catálogo -- e é justamente esse número que o
operador usa para saber que há 550 modelos lá.

**Funciona sem JavaScript.** A página é montada no servidor e o compromisso de
funcionar com o script desligado já está declarado no resto do painel; a
paginação seria o único ponto a quebrá-lo. Os links são links de verdade,
carregando a mesma página com outro parâmetro.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

# Dez por página, em todos os grids dos três painéis.
POR_PAGINA = 10


def _pagina_pedida(consulta: Optional[Dict[str, List[str]]], nome: str) -> int:
    """Lê o número da página para este grid, tolerando lixo na query."""
    if not consulta:
        return 1
    bruto = (consulta.get(f"pag_{nome}") or ["1"])[0]
    try:
        pagina = int(bruto)
    except (TypeError, ValueError):
        # `?pag_modelos=abc` não é motivo para derrubar a página inteira.
        return 1
    return pagina if pagina >= 1 else 1


def recortar(
    itens: Sequence[Any],
    nome: str,
    consulta: Optional[Dict[str, List[str]]] = None,
    por_pagina: int = POR_PAGINA,
) -> Tuple[List[Any], Dict[str, Any]]:
    """Devolve a fatia visível e o estado da paginação daquele grid.

    O estado carrega o TOTAL, e não o tamanho da fatia: quem desenha o cabeçalho
    precisa do número inteiro.
    """
    total = len(itens)
    ultima = max(1, (total + por_pagina - 1) // por_pagina)
    # Uma página além do fim vira a última, em vez de uma tabela vazia sem
    # explicação -- isso acontece sozinho quando o catálogo encolhe entre dois
    # carregamentos e o link da página 7 continua no histórico do navegador.
    pagina = min(_pagina_pedida(consulta, nome), ultima)
    inicio = (pagina - 1) * por_pagina
    return list(itens[inicio : inicio + por_pagina]), {
        "nome": nome,
        "pagina": pagina,
        "ultima": ultima,
        "total": total,
        "inicio": inicio + 1 if total else 0,
        "fim": min(inicio + por_pagina, total),
        "por_pagina": por_pagina,
    }


def _link(consulta: Optional[Dict[str, List[str]]], nome: str, pagina: int) -> str:
    """Monta a URL desta página preservando os demais parâmetros.

    Preservar importa: sem isso, avançar a página dos modelos zeraria a página
    das conexões e apagaria o aviso da última ação.
    """
    parametros: Dict[str, str] = {}
    for chave, valores in (consulta or {}).items():
        if not valores:
            continue
        # O aviso da última ação é de uso único: repeti-lo a cada clique de
        # página faria a mesma mensagem reaparecer indefinidamente.
        if chave in ("aviso", "tom"):
            continue
        parametros[chave] = valores[0]
    parametros[f"pag_{nome}"] = str(pagina)
    return "?" + urlencode(parametros)


def render_paginacao(estado: Dict[str, Any], consulta, traduzir, lang: str) -> str:
    """Desenha a barra de páginas. Some sozinha quando há uma página só."""
    if estado["ultima"] <= 1:
        return ""

    nome = estado["nome"]
    pagina = estado["pagina"]
    ultima = estado["ultima"]

    def item(rotulo: str, destino: int, ativo: bool = False, morto: bool = False) -> str:
        if morto:
            return (
                f'<li class="page-item disabled"><span class="page-link">{rotulo}</span></li>'
            )
        if ativo:
            return (
                f'<li class="page-item active" aria-current="page">'
                f'<span class="page-link">{rotulo}</span></li>'
            )
        return (
            f'<li class="page-item"><a class="page-link" '
            f'href="{_link(consulta, nome, destino)}#grid-{nome}">{rotulo}</a></li>'
        )

    # Uma janela em volta da página atual: com 55 páginas, listar todas daria
    # uma barra mais alta que a própria tabela.
    primeira_visivel = max(1, pagina - 2)
    ultima_visivel = min(ultima, primeira_visivel + 4)
    primeira_visivel = max(1, ultima_visivel - 4)

    itens = [item("&laquo;", pagina - 1, morto=pagina <= 1)]
    if primeira_visivel > 1:
        itens.append(item("1", 1))
        if primeira_visivel > 2:
            itens.append(item("…", 0, morto=True))
    for numero in range(primeira_visivel, ultima_visivel + 1):
        itens.append(item(str(numero), numero, ativo=numero == pagina))
    if ultima_visivel < ultima:
        if ultima_visivel < ultima - 1:
            itens.append(item("…", 0, morto=True))
        itens.append(item(str(ultima), ultima))
    itens.append(item("&raquo;", pagina + 1, morto=pagina >= ultima))

    intervalo = traduzir(
        "pagination.range",
        lang,
        inicio=estado["inicio"],
        fim=estado["fim"],
        total=estado["total"],
    )
    return f"""
        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 px-3 py-2 barra-paginas">
          <span class="text-secondary small">{intervalo}</span>
          <nav aria-label="{traduzir("pagination.label", lang)}">
            <ul class="pagination pagination-sm mb-0">{"".join(itens)}</ul>
          </nav>
        </div>"""
