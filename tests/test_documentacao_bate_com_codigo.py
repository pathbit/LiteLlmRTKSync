"""A documentação não pode afirmar o que o código não faz.

Documentação envelhece em silêncio. Uma variável renomeada, uma flag que saiu
do CLI, uma rota que mudou de nome — nada disso quebra um teste, nem aparece
num diff de revisão. Quem descobre é o leitor, quando o comando não funciona, e
ele não tem como saber que o errado é o texto.

Este teste roda o verificador de `tools/valida_docs.py` sobre o repositório
inteiro e falha quando uma página cita uma variável de ambiente que ninguém lê,
uma flag que o CLI não tem, ou uma rota que o servidor não serve.

Casos reais que motivaram isto, todos encontrados assim:

- a página de saída de rede do 9RTKSync descrevia o gateway do irmão, com
  campos que não existem aqui;
- a wiki do OminiRTkSync anunciava uma resposta de `/healthz` que o servidor
  nunca devolveu;
- o README do LiteLlmRTKSync citava uma rota `POST /api/sync` inexistente.
"""

import os
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))

from valida_docs import verificar  # noqa: E402


class TestDocumentacaoBateComOCodigo(unittest.TestCase):
    def test_no_page_claims_something_the_code_does_not_do(self):
        divergencias = verificar(RAIZ, os.path.basename(RAIZ))
        self.assertEqual(
            divergencias,
            [],
            "a documentação diverge do código:\n  " + "\n  ".join(divergencias),
        )


if __name__ == "__main__":
    unittest.main()
