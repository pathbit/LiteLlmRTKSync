"""As portas citadas na documentação têm de ser as que o compose publica.

Um exemplo de compose embutido no README que diverge do arquivo real é pior que
exemplo nenhum: o leitor copia, sobe, e fica com duas verdades — a que está no
texto e a que está na stack. Foi o que aconteceu quando as portas mudaram e os
exemplos ficaram para trás.

Este teste lê as portas que os composes do repositório realmente publicam e
exige que todo bloco `- "127.0.0.1:X:Y"` citado na documentação seja um deles.
"""

import os
import re
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RX_PORTA = re.compile(r'-\s*"127\.0\.0\.1:(\d+):(\d+)"')


def composes():
    for nome in sorted(os.listdir(RAIZ)):
        if nome.startswith("docker-compose") and nome.endswith((".yml", ".yaml")):
            yield os.path.join(RAIZ, nome)


def documentacao():
    for base in (RAIZ, os.path.join(RAIZ, "docs", "wiki")):
        if not os.path.isdir(base):
            continue
        for nome in sorted(os.listdir(base)):
            if nome.endswith(".md"):
                yield os.path.join(base, nome)


def mapeamentos(caminho):
    with open(caminho, encoding="utf-8") as f:
        return set(RX_PORTA.findall(f.read()))


class TestPortasDocumentadasBatem(unittest.TestCase):
    def test_every_port_mapping_in_the_docs_matches_a_compose(self):
        reais = set()
        for c in composes():
            reais |= mapeamentos(c)
        self.assertTrue(reais, "nenhum mapeamento de porta encontrado nos composes")

        divergentes = []
        for doc in documentacao():
            for host, interna in mapeamentos(doc):
                if (host, interna) not in reais:
                    divergentes.append(
                        f"{os.path.relpath(doc, RAIZ)}: 127.0.0.1:{host}:{interna} "
                        f"não corresponde a nenhum compose deste repositório"
                    )
        self.assertEqual(
            divergentes,
            [],
            "documentação e compose discordam:\n  " + "\n  ".join(divergentes),
        )


if __name__ == "__main__":
    unittest.main()
