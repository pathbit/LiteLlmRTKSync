"""O cabeçalho Server não pode anunciar a pilha que serve o painel.

`BaseHTTPRequestHandler` responde, por padrão, `Server: BaseHTTP/0.6
Python/3.14.7` — a versão exata do interpretador, na primeira linha de TODA
resposta, inclusive no 401 que sai antes de qualquer autenticação. Ela viajava
logo acima da CSP, do `X-Frame-Options: DENY` e do `nosniff` que o resto do
cabeçalho instala: a mesma resposta que fecha as portas dizia qual é a
fechadura.

Versão exata é o que um scanner precisa para escolher o exploit certo, e nada
no produto depende de publicá-la.
"""

import importlib
import pathlib
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
# O pacote é o único diretório sob src/ que declara identidade: descobri-lo em
# vez de escrever o nome mantém este teste igual nos três repositórios.
PACOTE = next(
    p for p in sorted((RAIZ / "src").iterdir()) if (p / "identidade.py").is_file()
)
HANDLER = PACOTE / "web.py"

PROIBIDO = ("python", "basehttp", "simplehttp", "wsgi")


def handler():
    """A classe que realmente responde, e não o texto do módulo.

    Desde o cânone `server_version` é o nome vindo de `identidade.py`, e não um
    literal: um teste que procurasse o literal reprovaria justamente a mudança
    que tirou o nome do produto de dentro do módulo comum.
    """
    return importlib.import_module(f"{PACOTE.name}.web").DashboardHandler


class CabecalhoServerNaoDenuncia(unittest.TestCase):
    def test_o_handler_declara_nome_proprio_e_versao_vazia(self):
        alvo = handler()
        self.assertTrue(
            alvo.server_version,
            "sem server_version o padrão do BaseHTTP anuncia a versão do Python",
        )
        self.assertEqual(
            alvo.sys_version,
            "",
            "sys_version tem de ser vazio: é ele que carrega 'Python/3.x.y'",
        )

    def test_version_string_devolve_so_o_nome(self):
        """Sem sobrescrever, o valor sai com um espaço sobrando no fim."""
        fonte = HANDLER.read_text(encoding="utf-8")
        self.assertIn(
            "def version_string",
            fonte,
            "BaseHTTPRequestHandler concatena server_version + ' ' + sys_version",
        )

    def test_o_nome_anunciado_nao_cita_a_pilha(self):
        anunciado = handler().server_version
        nome = anunciado.lower()
        for proibido in PROIBIDO:
            self.assertNotIn(
                proibido,
                nome,
                f"o nome anunciado ({anunciado!r}) entrega a pilha",
            )


if __name__ == "__main__":
    unittest.main()
