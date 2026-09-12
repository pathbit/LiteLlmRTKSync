"""Toda variável que um compose exige precisa existir no .env.example.

O fluxo documentado é `cp .env.example .env`, preencher e subir. Se o compose
usa uma variável que o exemplo não menciona, esse fluxo produz um `.env`
incompleto: ou a stack recusa subir com `variable is not set`, ou — pior — sobe
com um default silencioso que ninguém escolheu.

Foi exatamente o que aconteceu com o painel: o compose de teste não passava
`DASHBOARD_PASSWORD`, e não havia como definir a senha pelo `.env`.
"""

import os
import re
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ${VAR}, ${VAR:-default}, ${VAR:?mensagem}
RX_USO = re.compile(r"\$\{([A-Z][A-Z0-9_]*)[:}?-]")
RX_DECLARA = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)=", re.M)


def composes():
    for nome in os.listdir(RAIZ):
        if nome.startswith("docker-compose") and nome.endswith((".yml", ".yaml")):
            yield os.path.join(RAIZ, nome)


def declaradas_no_exemplo():
    caminho = os.path.join(RAIZ, ".env.example")
    with open(caminho, encoding="utf-8") as f:
        return set(RX_DECLARA.findall(f.read()))


class TestExemploCobreOsComposes(unittest.TestCase):
    def test_every_variable_a_compose_needs_exists_in_the_example(self):
        declaradas = declaradas_no_exemplo()
        faltando = {}
        for c in composes():
            with open(c, encoding="utf-8") as f:
                usadas = set(RX_USO.findall(f.read()))
            ausentes = sorted(usadas - declaradas - {"HOME", "PWD", "USER"})
            if ausentes:
                faltando[os.path.basename(c)] = ausentes
        self.assertEqual(
            faltando,
            {},
            "variáveis exigidas por um compose e ausentes do .env.example: " + str(faltando),
        )

    def test_the_panel_credentials_are_offered_by_the_example(self):
        # O caminho de entrada no painel tem de estar no exemplo, senão quem
        # copia o arquivo não descobre que essas variáveis existem.
        declaradas = declaradas_no_exemplo()
        for v in ("DASHBOARD_USER", "DASHBOARD_PASSWORD"):
            self.assertIn(v, declaradas, f"{v} precisa aparecer no .env.example")


if __name__ == "__main__":
    unittest.main()
