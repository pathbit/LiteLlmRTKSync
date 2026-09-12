"""A documentação do /healthz tem de citar as respostas que o código produz.

A wiki anunciava `OMNIROUTE_SERVICE_UNREACHABLE` e o servidor respondia
`GATEWAY_SERVICE_UNREACHABLE`. Nada quebra: o painel funciona, o healthcheck
funciona, os testes passam. Quem perde é quem monta um alerta sobre a string
documentada — ele nunca dispara, e o silêncio parece saúde.

Este teste lê as respostas direto do fonte do servidor e exige que cada uma
apareça na documentação.
"""

import os
import re
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVIDOR = os.path.join(RAIZ, "src", "litellm_rtksync", "web.py")


def respostas_do_healthz() -> set:
    """Os literais que o /healthz devolve, lidos do fonte."""
    with open(SERVIDOR, encoding="utf-8") as f:
        fonte = f.read()
    inicio = fonte.find("def serve_healthz")
    assert inicio > 0, "serve_healthz nao encontrada"
    trecho = fonte[inicio:inicio + 1500]
    return set(re.findall(r'b"([A-Z][A-Z0-9_]+)"', trecho))


def paginas_de_documentacao():
    for pasta, dirs, arquivos in os.walk(RAIZ):
        dirs[:] = [d for d in dirs if d not in (".git", "tmp", "node_modules", "__pycache__")]
        for a in arquivos:
            if a.endswith(".md"):
                yield os.path.join(pasta, a)


class TestHealthzDocumentado(unittest.TestCase):
    def test_every_answer_the_code_returns_is_documented(self):
        respostas = respostas_do_healthz()
        self.assertTrue(respostas, "nenhuma resposta encontrada no fonte")

        documentado = ""
        for p in paginas_de_documentacao():
            with open(p, encoding="utf-8") as f:
                documentado += f.read()

        faltando = sorted(r for r in respostas if r not in documentado)
        self.assertEqual(
            faltando,
            [],
            "o /healthz responde isto e a documentacao nao cita: " + ", ".join(faltando),
        )

    def test_no_page_invents_an_answer_the_code_never_returns(self):
        respostas = respostas_do_healthz()
        inventadas = []
        # O sublinhado precisa ser aceito no meio: OMNIROUTE_SERVICE_UNREACHABLE
        # tem um segmento entre o prefixo e o sufixo, e um padrao que pare no
        # primeiro sublinhado deixa passar justamente o caso que motivou o teste.
        padrao = re.compile(r"`([A-Z][A-Z0-9_]*_(?:NOT_READY|UNREACHABLE|UNAVAILABLE))`")
        for p in paginas_de_documentacao():
            with open(p, encoding="utf-8") as f:
                for n, linha in enumerate(f, 1):
                    for citado in padrao.findall(linha):
                        if citado not in respostas:
                            inventadas.append(os.path.relpath(p, RAIZ) + ":" + str(n) + " " + citado)
        self.assertEqual(
            inventadas,
            [],
            "documentacao cita resposta que o codigo nunca devolve: " + ", ".join(inventadas),
        )


if __name__ == "__main__":
    unittest.main()
