"""Nenhuma chave de tradução pode chegar crua à tela.

`translate()` devolve a própria chave quando não encontra o texto. O efeito é
silencioso: nada quebra, nenhum teste falha, e o operador lê `egress.title` no
lugar de "Saída de rede" — foi exatamente o que aconteceu aqui, cinco vezes no
mesmo painel, até alguém abrir a tela e reparar.

A guarda cobre as chaves escritas literalmente. As montadas em tempo de execução
(`translate(f"health.{status}")`) ficam de fora de propósito: varrê-las por
regex acusaria como órfãs as nove `health.*` que existem e são usadas, e uma
guarda que mente perde a autoridade de reprovar.
"""

import pathlib
import re
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
FONTE = RAIZ / "src" / "litellm_rtksync"
CATALOGO = FONTE / "i18n.py"

# translate("chave.literal", ...) — só o primeiro argumento, e só quando é
# string literal. f-strings e variáveis não casam, que é o que se quer.
USO_LITERAL = re.compile(r'translate\(\s*"([a-z0-9_]+(?:\.[a-z0-9_]+)+)"')
DECLARACAO = re.compile(r'^\s*"([a-z0-9_]+(?:\.[a-z0-9_]+)+)"\s*:', re.M)


def chaves_declaradas():
    return set(DECLARACAO.findall(CATALOGO.read_text(encoding="utf-8")))


def chaves_usadas():
    usadas = {}
    for arquivo in sorted(FONTE.rglob("*.py")):
        if arquivo.name == "i18n.py":
            continue
        texto = arquivo.read_text(encoding="utf-8")
        for numero, linha in enumerate(texto.splitlines(), 1):
            for chave in USO_LITERAL.findall(linha):
                usadas.setdefault(chave, f"{arquivo.relative_to(RAIZ)}:{numero}")
    return usadas


class NenhumaChaveCruaNaTela(unittest.TestCase):
    def test_toda_chave_usada_existe_no_catalogo(self):
        declaradas = chaves_declaradas()
        orfas = {k: onde for k, onde in chaves_usadas().items() if k not in declaradas}
        self.assertEqual(
            orfas,
            {},
            "estas chaves chegariam cruas à tela:\n  "
            + "\n  ".join(f"{k}  (usada em {onde})" for k, onde in sorted(orfas.items())),
        )

    def test_os_tres_idiomas_declaram_o_mesmo_conjunto(self):
        """Faltar num idioma é o mesmo defeito, visível só para quem usa aquele idioma."""
        texto = CATALOGO.read_text(encoding="utf-8")
        # Cada bloco de idioma começa numa linha do tipo `"en": {`
        blocos = re.split(r'^\s*"(?:en|pt|es)"\s*:\s*\{', texto, flags=re.M)[1:]
        self.assertEqual(len(blocos), 3, "esperava três blocos de idioma no catálogo")
        conjuntos = [set(DECLARACAO.findall(b)) for b in blocos]
        for i, outro in enumerate(conjuntos[1:], start=1):
            faltando = conjuntos[0] - outro
            sobrando = outro - conjuntos[0]
            self.assertEqual(faltando, set(), f"bloco {i} não declara: {sorted(faltando)}")
            self.assertEqual(sobrando, set(), f"bloco {i} declara a mais: {sorted(sobrando)}")


if __name__ == "__main__":
    unittest.main()
