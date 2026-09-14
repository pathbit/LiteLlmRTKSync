"""A identidade mora em UM arquivo, e este teste é o que torna a frase verdadeira.

Os três sincronizadores são a mesma aplicação com cor, nome, logo e gateway
diferentes. Isso só se sustenta se o que difere estiver todo num lugar: enquanto
"9Router" aparecer em vinte e três arquivos, qualquer convergência é uma
promessa, e não um fato verificável.

Este teste roda SOZINHO -- não precisa dos irmãos ao lado. É ele que impede a
divergência de voltar por baixo de `test_irmaos_identicos.py`: uma cor
reintroduzida em `render.py` quebraria a comparação nos três repositórios ao
mesmo tempo, e a saída mais fácil seria reintroduzi-la nos três. Aqui a
reintrodução reprova sozinha, no repositório que a cometeu.

A lista de arquivos isentos está vazia de propósito. Se algum dia precisar de
uma exceção, ela vira constante nomeada aqui em cima, com o motivo escrito ao
lado -- para aparecer no diff de quem a criou.
"""

import pathlib
import re
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
# O pacote é o único diretório sob src/ que declara identidade: descobri-lo em
# vez de escrever o nome mantém este teste igual nos três repositórios.
PACOTE = next(
    p for p in sorted((RAIZ / "src").iterdir()) if (p / "identidade.py").is_file()
)

# O único arquivo autorizado a escrever identidade literal.
FRONTEIRA = "identidade.py"

# Nenhum arquivo isento. Uma lista vazia é uma afirmação, não um esquecimento.
ISENTOS: tuple = ()

# A única cor que pode ser escrita fora de `identidade.py`, e o motivo: ela é
# ESTRUTURAL, não identidade. Os três painéis declaram exatamente este valor
# para `--text`, e `test_identidade_visual.py` cobra isso. Se ela mudasse num
# painel só, quem reprovaria seria aquele teste, não este. Está nomeada aqui
# para aparecer no diff de quem um dia quiser uma segunda exceção.
CORES_ESTRUTURAIS = {"#e6e8ee"}

PROIBIDOS = [
    (re.compile(r"#[0-9a-fA-F]{6}\b"), "cor fora de identidade.py"),
    (re.compile(r"9RTKSync|OminiRTKSync|OminiRTkSync|LiteLlmRTKSync"), "nome de produto literal"),
    (re.compile(r"9Router|OmniRoute|LiteLLM"), "nome de gateway literal"),
    (re.compile(r"9rtk-|ominirtk-|litellmrtk-"), "prefixo de container literal"),
    (
        re.compile(r"bi-lightning-charge-fill|bi-signpost-split-fill|bi-speedometer2"),
        "ícone do produto literal",
    ),
]


def arquivos_do_pacote():
    for caminho in sorted(PACOTE.rglob("*.py")):
        if "__pycache__" in caminho.parts:
            continue
        relativo = caminho.relative_to(PACOTE).as_posix()
        if relativo == FRONTEIRA or relativo in ISENTOS:
            continue
        yield relativo, caminho


class IdentidadeNaoVazaDoArquivoDela(unittest.TestCase):
    def test_nenhum_modulo_escreve_identidade_a_mao(self):
        achados = []
        for relativo, caminho in arquivos_do_pacote():
            for numero, linha in enumerate(
                caminho.read_text(encoding="utf-8").splitlines(), 1
            ):
                sem_estruturais = linha
                for cor in CORES_ESTRUTURAIS:
                    sem_estruturais = sem_estruturais.replace(cor, "")
                for padrao, motivo in PROIBIDOS:
                    if padrao.search(sem_estruturais):
                        achados.append(f"{relativo}:{numero}  {motivo}  ->  {linha.strip()[:110]}")
        self.assertEqual(
            achados,
            [],
            "identidade fora de identidade.py (importe de lá em vez de escrever):\n"
            + "\n".join(achados),
        )

    def test_a_fronteira_existe_e_nao_importa_nada_do_pacote(self):
        """Sem import interno, ou o arquivo que todos importam vira um ciclo."""
        fonte = (PACOTE / FRONTEIRA).read_text(encoding="utf-8")
        internos = [
            linha
            for linha in fonte.splitlines()
            if re.match(r"\s*(from\s+\.|import\s+\.)", linha)
        ]
        self.assertEqual(internos, [], f"identidade.py não pode importar do pacote: {internos}")


if __name__ == "__main__":
    unittest.main()
