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

import pathlib
import re
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
HANDLER = RAIZ / "src" / "litellm_rtksync" / "web.py"

PROIBIDO = ("python", "basehttp", "simplehttp", "wsgi")


class CabecalhoServerNaoDenuncia(unittest.TestCase):
    def test_o_handler_declara_nome_proprio_e_versao_vazia(self):
        fonte = HANDLER.read_text(encoding="utf-8")
        self.assertRegex(
            fonte,
            r'server_version\s*=\s*"[^"]+"',
            "sem server_version o padrão do BaseHTTP anuncia a versão do Python",
        )
        self.assertRegex(
            fonte,
            r'sys_version\s*=\s*""',
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
        fonte = HANDLER.read_text(encoding="utf-8")
        achado = re.search(r'server_version\s*=\s*"([^"]+)"', fonte)
        self.assertIsNotNone(achado)
        nome = achado.group(1).lower()
        for proibido in PROIBIDO:
            self.assertNotIn(
                proibido,
                nome,
                f"o nome anunciado ({achado.group(1)!r}) entrega a pilha",
            )


if __name__ == "__main__":
    unittest.main()
