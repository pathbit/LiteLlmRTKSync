"""Contrato de identidade visual: mesma casca nos três painéis, paleta própria.

A regra do produto é uma só -- "os frontends devem ter os mesmos componentes,
mas as cores mudam". Isso só se sustenta se a diferença entre os painéis estiver
inteiramente nos VALORES de um conjunto fechado de tokens, e nunca na existência
deles: um token a mais num painel é um componente que só ele sabe desenhar, e a
simetria acaba ali.

Desde o cânone, os valores não moram mais em `render.py`: moram em
`identidade.py`, o único arquivo do pacote que pode divergir dos irmãos. Este
teste passou a ler de lá, e é por isso que ele mesmo é IDÊNTICO nos três
repositórios — se o contrato fosse diferente em algum deles, este arquivo
precisaria ser diferente, e `tests/test_irmaos_identicos.py` reprovaria.

Esta guarda existe porque a alternativa é alguém abrir as três telas lado a lado
e reparar. Isso funcionou até parar de funcionar.
"""

import importlib
import importlib.util
import pathlib
import re
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent

# O pacote é o único diretório sob src/ que declara identidade. Descobri-lo em
# vez de escrever o nome é o que permite este arquivo ser o mesmo nos três.
PACOTE = next(
    p for p in sorted((RAIZ / "src").iterdir()) if (p / "identidade.py").is_file()
)
RENDER = PACOTE / "render.py"

# Contrato fechado de identidade: exatamente estes nomes, nem um a mais. Um
# nome extra aqui é uma decisão de produto que só um dos três tomou.
CONTRATO_DE_IDENTIDADE = frozenset(
    {
        "NOME_DO_PRODUTO",
        "NOME_DO_GATEWAY",
        "PROVEDOR_DO_GATEWAY",
        "PREFIXO_DE_CONTAINER",
        "PORTA_DO_PAINEL",
        "PORTA_DE_METRICAS",
        "ICONE_DO_PRODUTO",
        "GLIFO_DO_FAVICON",
        "COR_DO_FAVICON",
        "PALETA",
        "NOME_DO_COOKIE",
        "NOME_DO_COOKIE_DE_ESTADO",
    }
)

# Tokens de PAPEL: existem nos três, com valores diferentes em cada um.
PAPEIS_CROMATICOS = {
    "--bg", "--surface", "--surface-2", "--line",
    "--accent", "--accent-2", "--brand-a", "--brand-b", "--text-dim",
}

# `--text` é estrutural, mas o tema o emite junto da paleta: é conferido no
# CSS DESENHADO, e não no código de render.py.
COR_DO_TEXTO_CANONICA = "#e6e8ee"

# Tokens ESTRUTURAIS: existem nos três com o MESMO valor. São o que faz o
# Bootstrap obedecer à paleta em vez de trazer a dele, e por isso continuam
# escritos em render.py -- não são identidade de ninguém.
ESTRUTURAIS = {
    "--bs-btn-bg": "var(--accent)",
    "--bs-btn-border-color": "var(--accent)",
    "--bs-btn-color": "var(--bg)",
    "--bs-btn-hover-bg": "var(--accent-2)",
    "--bs-btn-hover-border-color": "var(--accent-2)",
    "--bs-btn-hover-color": "var(--bg)",
    "--bs-btn-active-bg": "var(--accent-2)",
    "--bs-btn-active-border-color": "var(--accent-2)",
    "--bs-btn-active-color": "var(--bg)",
    "--bs-table-bg": "transparent",
    "--bs-table-border-color": "var(--line)",
}


def identidade():
    """Carrega identidade.py pelo caminho, sem depender do nome do pacote."""
    alvo = PACOTE / "identidade.py"
    spec = importlib.util.spec_from_file_location("identidade_sob_teste", alvo)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def tokens_de(texto):
    """Extrai `--token: valor;` de um trecho de CSS ou de código."""
    return {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;{}]+);", texto)
    }


def tokens_escritos_no_render():
    """Tokens ainda declarados com valor literal dentro de render.py."""
    return tokens_de(RENDER.read_text(encoding="utf-8"))


def tema_desenhado():
    """O bloco `:root` como o painel realmente o serve."""
    modulo = importlib.import_module(f"{PACOTE.name}.render")
    return tokens_de(modulo.tokens_do_tema())


class ContratoDeIdentidade(unittest.TestCase):
    def test_declara_exatamente_os_nomes_do_contrato(self):
        """Nem a menos (peça sem identidade), nem a mais (decisão de um só)."""
        declarados = {
            nome
            for nome in vars(identidade())
            if nome.isupper() and not nome.startswith("_")
        }
        self.assertEqual(
            declarados,
            set(CONTRATO_DE_IDENTIDADE),
            "faltando: "
            f"{sorted(set(CONTRATO_DE_IDENTIDADE) - declarados)}; sobrando: "
            f"{sorted(declarados - set(CONTRATO_DE_IDENTIDADE))}",
        )

    def test_a_paleta_tem_exatamente_os_papeis_cromaticos(self):
        """Nove papéis, nos três. Um a mais é um componente que só este desenha."""
        paleta = set(identidade().PALETA)
        self.assertEqual(
            paleta,
            PAPEIS_CROMATICOS,
            f"tokens do contrato ausentes: {sorted(PAPEIS_CROMATICOS - paleta)}; "
            f"tokens fora do contrato: {sorted(paleta - PAPEIS_CROMATICOS)} -- se a "
            "peça é legítima, acrescente o token ao contrato dos TRÊS painéis",
        )

    def test_cada_papel_cromatico_e_uma_cor_literal(self):
        """Um `var(...)` aqui seria um papel que depende de outro para existir."""
        for token, valor in identidade().PALETA.items():
            self.assertRegex(
                valor,
                r"^#[0-9a-fA-F]{6}$",
                f"{token} precisa ser uma cor hexadecimal, e não {valor!r}",
            )


class ContratoDeTokens(unittest.TestCase):
    def test_o_render_declara_so_o_que_e_estrutural(self):
        """Papel cromático em render.py é identidade vazando de volta para o comum."""
        declarados = tokens_escritos_no_render()
        vazados = set(declarados) & PAPEIS_CROMATICOS
        self.assertEqual(
            vazados,
            set(),
            f"papéis cromáticos escritos em render.py: {sorted(vazados)} -- "
            "o valor deles pertence a identidade.py",
        )
        faltando = set(ESTRUTURAIS) - set(declarados)
        self.assertEqual(faltando, set(), f"tokens estruturais ausentes: {sorted(faltando)}")

    def test_o_tema_desenhado_e_a_paleta_mais_o_texto(self):
        """O `:root` servido ao navegador tem os nove papéis e a cor do texto."""
        esperado = dict(identidade().PALETA)
        esperado["--text"] = COR_DO_TEXTO_CANONICA
        self.assertEqual(tema_desenhado(), esperado)

    def test_os_tokens_estruturais_tem_o_valor_canonico(self):
        """O que não é cor tem de ser idêntico nos três, ou o componente muda de forma."""
        declarados = tokens_escritos_no_render()
        for token, esperado in ESTRUTURAIS.items():
            self.assertEqual(
                declarados.get(token),
                esperado,
                f"{token} é estrutural: os três painéis declaram {esperado!r}",
            )


class CoerenciaDaPaleta(unittest.TestCase):
    def test_cada_papel_cromatico_tem_a_sua_propria_cor(self):
        """Dois papéis com a mesma cor é um papel que deixou de existir.

        Se a borda e a marca valem o mesmo, elas viraram a mesma coisa na tela:
        a separação entre superfícies some, ou a marca deixa de se destacar. O
        token continua lá, mas não cumpre papel nenhum.
        """
        paleta = identidade().PALETA

        # Espelhamento deliberado, igual nos três painéis: o gradiente da marca
        # termina exatamente na cor de destaque, então `--brand-b` repete
        # `--accent` por decisão de design, e não por descuido. Fica declarado
        # aqui para que a guarda cubra o resto sem dar falso positivo nele.
        ESPELHOS_INTENCIONAIS = {frozenset({"--accent", "--brand-b"})}

        por_cor = {}
        for token, valor in paleta.items():
            por_cor.setdefault(valor.lower(), []).append(token)
        colisoes = {
            cor: sorted(ts)
            for cor, ts in por_cor.items()
            if len(ts) > 1 and frozenset(ts) not in ESPELHOS_INTENCIONAIS
        }
        self.assertEqual(colisoes, {}, "papéis diferentes com a mesma cor: " + repr(colisoes))

    def test_a_escada_de_profundidade_sobe(self):
        """bg mais escuro que surface, surface que surface-2, e a linha acima de todos."""
        paleta = identidade().PALETA

        def luz(token):
            v = paleta[token].lstrip("#")
            r, g, b = (int(v[i : i + 2], 16) for i in (0, 2, 4))
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        escada = ["--bg", "--surface", "--surface-2", "--line"]
        valores = [luz(t) for t in escada]
        self.assertEqual(
            valores,
            sorted(valores),
            "a escada de profundidade tem de subir: "
            + ", ".join(f"{t}={v:.0f}" for t, v in zip(escada, valores)),
        )

    def test_o_favicon_usa_o_tom_de_superficie(self):
        """A moldura do ícone é `--surface`: a cor-base fica escura demais em 32px."""
        ident = identidade()
        self.assertEqual(
            ident.COR_DO_FAVICON.lower(),
            ident.PALETA["--surface"].replace("#", "%23").lower(),
            "o fundo do favicon é o tom de superfície do tema, percent-encoded",
        )


if __name__ == "__main__":
    unittest.main()
