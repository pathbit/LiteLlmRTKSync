"""Toda página servida declara o ícone da aba.

`/favicon.ico` responde 401 atrás do Basic Auth, então o navegador não consegue
buscá-lo: sem um `<link rel="icon">` embutido, a aba fica com o quadrado
genérico. O painel é uma aba que o operador deixa aberta o dia inteiro, e três
abas genéricas lado a lado são indistinguíveis.

O ícone vai como data URI justamente por isso — não depende de requisição, e
por isso funciona também na página de erro, que é servida antes de qualquer
autenticação.
"""

import importlib
import pathlib
import re
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
# O pacote é o único diretório sob src/ que declara identidade: descobri-lo
# em vez de escrever o nome mantém este teste igual nos três repositórios.
PACOTE = next(
    p for p in sorted((RAIZ / "src").iterdir()) if (p / "identidade.py").is_file()
)
RENDER = PACOTE / "render.py"


class TodaPaginaTemIcone(unittest.TestCase):
    def setUp(self):
        self.fonte = RENDER.read_text(encoding="utf-8")

    def test_o_icone_e_uma_constante_unica(self):
        """Duplicar o SVG em cada página é como as duas cópias divergem.

        A moldura (viewBox, rect, transform) é comum aos três painéis e mora
        aqui; o desenho e a cor vêm de identidade.py. Por isso a constante é
        montada por concatenação, e não escrita de uma vez.
        """
        # re.M porque assertRegex usa re.search sem flags, e `^` sozinho só
        # casaria no primeiro caractere do arquivo.
        self.assertIsNotNone(
            re.search(r'^FAVICON = \(\n\s+"data:image/svg\+xml,', self.fonte, re.M),
            "o ícone tem de ser uma constante no topo do módulo",
        )

    def test_todo_documento_servido_declara_o_icone(self):
        """Conta os <head> e exige um <link rel=icon> para cada um."""
        heads = self.fonte.count("<head>")
        icones = len(re.findall(r'<link rel="icon" href="\{FAVICON\}">', self.fonte))
        self.assertEqual(
            icones,
            heads,
            f"{heads} documentos servidos e {icones} declarações de ícone: "
            "a aba de algum deles fica com o ícone genérico",
        )

    def test_o_icone_nao_depende_de_requisicao(self):
        """Um href para arquivo seria buscado, e /favicon.ico responde 401."""
        modulo = importlib.import_module(f"{PACOTE.name}.render")
        self.assertTrue(
            modulo.FAVICON.startswith("data:"),
            "o ícone precisa ser data URI: qualquer URL seria buscada e levaria 401",
        )


if __name__ == "__main__":
    unittest.main()
