"""Todo mapeamento de porta do Makefile tem de publicar só no loopback.

O painel lê o banco do gateway e mostra a saúde das credenciais -- a wiki manda
manter a porta dele em `127.0.0.1`, e todos os composes fazem isso. Quem
escapava era o Makefile: `-p 9093:9090` publica em TODA interface, o Wi-Fi do
café, a VLAN do escritório. O teste de portas existente varre apenas
`docker-compose*.yml` e `*.md`, então essa divergência sobreviveu até alguém
achar no olho. Esta guarda fecha o buraco.
"""

import pathlib
import re
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
MAKEFILE = RAIZ / "Makefile"

# -p [IP:]HOSTPORT:CONTAINERPORT
MAPEAMENTO = re.compile(r"-p\s+(?:(\S+):)?(\d+):(\d+)")


class MakefilePublicaNoLoopback(unittest.TestCase):
    def test_todo_mapeamento_de_porta_prende_no_loopback(self):
        self.assertTrue(MAKEFILE.exists(), "o repositório precisa de um Makefile")
        achados = []
        for numero, linha in enumerate(MAKEFILE.read_text(encoding="utf-8").splitlines(), 1):
            if linha.lstrip().startswith("#"):
                continue
            for ip, host, _interno in MAPEAMENTO.findall(linha):
                if ip not in ("127.0.0.1", "localhost"):
                    achados.append(f"Makefile:{numero}: -p {ip or '<sem ip>'}{'' if ip else ''}{host}:… publica em toda interface")
        self.assertEqual(
            achados,
            [],
            "prenda no loopback (-p 127.0.0.1:PORTA:…):\n  " + "\n  ".join(achados),
        )

    def test_a_porta_do_painel_e_a_deste_repositorio(self):
        """Publicar na porta do irmão mostra este painel no endereço do outro produto."""
        texto = MAKEFILE.read_text(encoding="utf-8")
        for ip, host, interno in MAPEAMENTO.findall(texto):
            if interno == "9090":
                self.assertEqual(
                    host,
                    "9093",
                    f"o painel deste repositório é a porta 9093, não a {host}",
                )


if __name__ == "__main__":
    unittest.main()
