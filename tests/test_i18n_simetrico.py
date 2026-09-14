"""Os três catálogos de tradução têm de ter exatamente as mesmas chaves.

Uma chave acrescentada só em inglês não quebra nada: `translate` cai no inglês
em silêncio, e a tela aparece meio traduzida para quem escolheu português ou
espanhol. Ninguém revisa isso num diff — o catálogo é longo e a falta é uma
linha que não existe.

Também confere os marcadores de interpolação: `cron.result_line` recebe
`inspected`, `findings` e `duration`, e uma tradução que troque um nome desses
devolve o texto sem substituir, sem erro nenhum.
"""

import re
import unittest

from litellm_rtksync.i18n import DEFAULT_LANGUAGE, LANGUAGES, TRANSLATIONS

RX_MARCADOR = re.compile(r"\{([a-z_]+)\}")


class TestCatalogosSimetricos(unittest.TestCase):
    def test_every_language_declares_the_same_keys(self):
        referencia = set(TRANSLATIONS[DEFAULT_LANGUAGE])
        for idioma in LANGUAGES:
            with self.subTest(idioma=idioma):
                faltando = sorted(referencia - set(TRANSLATIONS[idioma]))
                sobrando = sorted(set(TRANSLATIONS[idioma]) - referencia)
                self.assertEqual(faltando, [], f"chaves ausentes em {idioma}")
                self.assertEqual(sobrando, [], f"chaves só em {idioma}")

    def test_every_translation_keeps_the_same_placeholders(self):
        for chave, texto in TRANSLATIONS[DEFAULT_LANGUAGE].items():
            esperado = set(RX_MARCADOR.findall(texto))
            for idioma in LANGUAGES:
                if idioma == DEFAULT_LANGUAGE:
                    continue
                with self.subTest(chave=chave, idioma=idioma):
                    self.assertEqual(
                        set(RX_MARCADOR.findall(TRANSLATIONS[idioma][chave])),
                        esperado,
                        f"marcadores diferentes em {idioma}:{chave}",
                    )


if __name__ == "__main__":
    unittest.main()
