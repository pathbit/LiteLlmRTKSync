"""Este projeto nasceu portando código dos irmãos, e sobrou identidade deles.

O log deste sincronizador se chamava `ominirtksync.log`, o logger respondia por
`ominirtksync` e o diretório de fallback era `~/.ominirtksync/logs` — tudo
copiado do OminiRTkSync e nunca renomeado. Nada disso quebra teste nem aparece
em revisão: aparece quando alguém procura o log deste serviço e encontra o nome
do outro, ou pior, quando os dois rodam na mesma máquina, apontam para o mesmo
`~/.ominirtksync/logs` e escrevem no mesmo arquivo.

A prosa que compara este projeto com os irmãos é bem-vinda e continua valendo —
o que não pode é um **identificador** deste projeto carregar o nome do outro.
"""

import os
import re
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTE = os.path.join(RAIZ, "src", "litellm_rtksync")

# Nome de outro projeto usado como identificador: arquivo, logger, diretório.
SUSPEITOS = re.compile(
    r"""(?ix)
    (["'][^"']*\b(?:omini|nine)rtksync[^"']*["'])   # string com o nome do irmão
    """
)


def arquivos_py():
    for pasta, dirs, arquivos in os.walk(FONTE):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for a in arquivos:
            if a.endswith(".py"):
                yield os.path.join(pasta, a)


class TestNenhumIdentificadorDeOutroProjeto(unittest.TestCase):
    def test_no_sibling_name_is_used_as_an_identifier(self):
        achados = []
        for caminho in arquivos_py():
            with open(caminho, encoding="utf-8") as f:
                for n, linha in enumerate(f, 1):
                    for trecho in SUSPEITOS.findall(linha):
                        achados.append(f"{os.path.relpath(caminho, RAIZ)}:{n}  {trecho}")
        self.assertEqual(
            achados,
            [],
            "identificador com o nome de outro projeto:\n  " + "\n  ".join(achados),
        )

    def test_the_log_file_carries_this_project_name(self):
        from litellm_rtksync.logs import LOG_FILE_NAME

        self.assertEqual(LOG_FILE_NAME, "litellmrtksync.log")

    def test_the_fallback_directory_is_this_project_s(self):
        from litellm_rtksync import logs

        destino = logs._fallback_log_dir() if hasattr(logs, "_fallback_log_dir") else None
        if destino is None:  # nome interno pode mudar; a fonte é o módulo
            with open(logs.__file__, encoding="utf-8") as f:
                conteudo = f.read()
            self.assertIn(".litellmrtksync", conteudo)
        else:
            self.assertIn(".litellmrtksync", destino)


if __name__ == "__main__":
    unittest.main()
