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

# Senhas de fábrica conhecidas dos gateways que este projeto acompanha. A senha
# REAL da instalação não entra aqui: ela é lida do .env em tempo de execução,
# logo abaixo. A primeira versão deste arquivo trazia o valor real escrito à
# mão -- um teste criado para impedir credencial em arquivo versionado
# carregando uma, que é o tipo de ironia que passa despercebida por meses.
CREDENCIAIS_DE_FABRICA = ("pathbit", "123456", "changeme", "admin123")


def senhas_reais_desta_instalacao():
    """Lê do .env (gitignored) o que NUNCA pode aparecer em arquivo versionado.

    Mais forte do que uma lista fixa: protege a senha que o operador escolheu,
    qualquer que seja ela, e não só as que alguém lembrou de escrever aqui.
    """
    env = RAIZ / ".env"
    if not env.exists():
        return set()
    achadas = set()
    for linha in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        valor = valor.strip().strip('"').strip("'")
        # Só o que é credencial, e só se for específico o bastante para uma
        # busca não dar falso positivo em texto comum.
        if len(valor) >= 8 and any(
            marca in chave.upper()
            for marca in ("PASSWORD", "SECRET", "TOKEN", "KEY", "PASSWD")
        ):
            achadas.add(valor)
    return achadas


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


    def test_nenhuma_senha_real_aparece_em_arquivo_versionado(self):
        """A senha escolhida pelo operador não pode estar em lugar nenhum do repo.

        Varre o que o git rastreia -- não o disco -- porque é o que sai daqui
        quando alguém clona ou publica.
        """
        import subprocess

        senhas = senhas_reais_desta_instalacao()
        if not senhas:
            self.skipTest("sem .env nesta máquina: nada a comparar")

        try:
            saida = subprocess.run(
                ["git", "-C", str(RAIZ), "ls-files"],
                capture_output=True,
                text=True,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            # O contêiner de teste do LiteLlm não traz git. Sem a lista do que
            # é rastreado, a varredura mediria o disco -- onde o .env mora de
            # propósito -- e acusaria justamente o arquivo que deve conter a
            # senha. Pular é mais honesto do que medir a coisa errada.
            self.skipTest("git indisponível: não dá para saber o que é versionado")
        rastreados = saida.stdout.split()

        achados = []
        for relativo in rastreados:
            caminho = RAIZ / relativo
            try:
                texto = caminho.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue
            for senha in senhas:
                if senha in texto:
                    achados.append(f"{relativo}: contém uma credencial do .env")
        self.assertEqual(achados, [], "credencial real em arquivo versionado:\n  " + "\n  ".join(achados))


if __name__ == "__main__":
    unittest.main()
