"""Contrato de identidade visual: mesma casca nos três painéis, paleta própria.

A regra do produto é uma só -- "os frontends devem ter os mesmos componentes,
mas as cores mudam". Isso só se sustenta se a diferença entre os painéis estiver
inteiramente nos VALORES de um conjunto fechado de tokens, e nunca na existência
deles: um token a mais num painel é um componente que só ele sabe desenhar, e a
simetria acaba ali.

Esta guarda existe porque a alternativa é alguém abrir as três telas lado a lado
e reparar. Isso funcionou até parar de funcionar.
"""

import pathlib
import re
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
RENDER = RAIZ / "src" / "litellm_rtksync" / "render.py"

# A cor de fundo deste produto, escolhida pelo dono. É o único token cujo valor
# este repositório tem autoridade para fixar.
FUNDO = "#6D0808"

# Tokens de PAPEL: existem nos três, com valores diferentes em cada um.
PAPEIS_CROMATICOS = {
    "--bg", "--surface", "--surface-2", "--line",
    "--accent", "--accent-2", "--brand-a", "--brand-b", "--text-dim",
}

# Tokens ESTRUTURAIS: existem nos três com o MESMO valor. São o que faz o
# Bootstrap obedecer à paleta em vez de trazer a dele.
ESTRUTURAIS = {
    "--text": "#e6e8ee",
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

CONTRATO = PAPEIS_CROMATICOS | set(ESTRUTURAIS)


def tokens_declarados():
    """Todos os tokens definidos no tema, com o valor de cada um."""
    fonte = RENDER.read_text(encoding="utf-8")
    return {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;{}]+);", fonte)
    }


class ContratoDeTokens(unittest.TestCase):
    def test_declara_exatamente_os_tokens_do_contrato(self):
        """Nem a menos (componente sem cor), nem a mais (cor que só um painel tem)."""
        declarados = set(tokens_declarados())
        faltando = CONTRATO - declarados
        sobrando = declarados - CONTRATO
        self.assertEqual(faltando, set(), f"tokens do contrato ausentes: {sorted(faltando)}")
        self.assertEqual(
            sobrando,
            set(),
            f"tokens fora do contrato: {sorted(sobrando)} -- se a peça é legítima, "
            "acrescente o token ao contrato dos TRÊS painéis, não só deste",
        )

    def test_os_tokens_estruturais_tem_o_valor_canonico(self):
        """O que não é cor tem de ser idêntico nos três, ou o componente muda de forma."""
        declarados = tokens_declarados()
        for token, esperado in ESTRUTURAIS.items():
            self.assertEqual(
                declarados.get(token),
                esperado,
                f"{token} é estrutural: os três painéis declaram {esperado!r}",
            )

    def test_o_fundo_e_a_cor_deste_produto(self):
        self.assertEqual(
            tokens_declarados().get("--bg", "").lower(),
            FUNDO.lower(),
            "o fundo é a identidade do produto e foi escolhido pelo dono",
        )


class CoerenciaDaPaleta(unittest.TestCase):
    def test_cada_papel_cromatico_tem_a_sua_propria_cor(self):
        """Dois papéis com a mesma cor é um papel que deixou de existir.

        Se a borda e a marca valem o mesmo, elas viraram a mesma coisa na tela:
        a separação entre superfícies some, ou a marca deixa de se destacar. O
        token continua lá, mas não cumpre papel nenhum.
        """
        declarados = tokens_declarados()

        # Espelhamento deliberado, igual nos três painéis: o gradiente da marca
        # termina exatamente na cor de destaque, então `--brand-b` repete
        # `--accent` por decisão de design, e não por descuido. Fica declarado
        # aqui para que a guarda cobre o resto sem dar falso positivo nele.
        ESPELHOS_INTENCIONAIS = {frozenset({"--accent", "--brand-b"})}

        por_cor = {}
        for token in PAPEIS_CROMATICOS:
            valor = declarados.get(token, "").lower()
            if valor.startswith("var("):  # alias declarado de propósito
                continue
            por_cor.setdefault(valor, []).append(token)
        colisoes = {
            cor: sorted(ts)
            for cor, ts in por_cor.items()
            if len(ts) > 1 and frozenset(ts) not in ESPELHOS_INTENCIONAIS
        }
        self.assertEqual(colisoes, {}, "papéis diferentes com a mesma cor: " + repr(colisoes))

    def test_a_escada_de_profundidade_sobe(self):
        """bg mais escuro que surface, surface que surface-2, e a linha acima de todos."""
        declarados = tokens_declarados()

        def luz(token):
            v = declarados.get(token, "").lstrip("#")
            if len(v) != 6:
                self.skipTest(f"{token} não é hexadecimal literal: {v!r}")
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


if __name__ == "__main__":
    unittest.main()
