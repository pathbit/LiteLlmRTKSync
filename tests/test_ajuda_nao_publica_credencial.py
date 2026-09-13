"""O texto de --help não pode anunciar credencial de fábrica.

O repositório já garantia que a tela não mostra "admin / pathbit"
(test_web_security), mas o `--help` ficou de fora da guarda e continuava
imprimindo "(padrão: pathbit)" -- em arquivo versionado, numa linha que todo
operador lê antes de subir o serviço. Uma senha de fábrica anunciada vira a
senha real de toda instalação que copiou e colou.

Agravante do caso original: o valor também era falso. O padrão real é string
vazia, e o painel gera uma credencial de recuperação no primeiro boot.
"""

import pathlib
import re
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
FONTE = RAIZ / "src" / "litellm_rtksync"

# Valores que já foram, em algum momento, senha de fábrica deste projeto ou
# dos gateways que ele acompanha.
CREDENCIAIS_DE_FABRICA = ("pathbit", "123456", "changeme", "admin123", "Pathbit@2026")


class AjudaNaoPublicaCredencial(unittest.TestCase):
    def test_nenhum_texto_de_ajuda_cita_credencial_de_fabrica(self):
        achados = []
        for arquivo in sorted(FONTE.rglob("*.py")):
            for numero, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), 1):
                if "help=" not in linha and "add_argument" not in linha:
                    continue
                for valor in CREDENCIAIS_DE_FABRICA:
                    if re.search(rf"\b{re.escape(valor)}\b", linha, re.IGNORECASE):
                        achados.append(f"{arquivo.relative_to(RAIZ)}:{numero}: {linha.strip()[:90]}")
        self.assertEqual(
            achados,
            [],
            "texto de ajuda anunciando credencial:\n  " + "\n  ".join(achados),
        )

    def test_a_senha_padrao_do_codigo_continua_vazia(self):
        """Se alguém reintroduzir um padrão, o texto de ajuda volta a mentir."""
        config = (FONTE / "config.py").read_text(encoding="utf-8")
        self.assertRegex(
            config,
            r'dashboard_password:\s*str\s*=\s*""',
            "o padrão tem de ser vazio: qualquer valor aqui é credencial de fábrica",
        )


if __name__ == "__main__":
    unittest.main()
