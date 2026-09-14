"""Formatar um carimbo de tempo duas vezes tem de dar o mesmo resultado.

`format_timestamp` trocava TODO "T" da string por espaço. Aplicada uma vez,
acertava: `2026-09-13T19:08:48Z` virava `2026-09-13 19:08:48 UTC`. Aplicada
sobre o próprio resultado — o que acontece quando um valor já formatado passa
por ela de novo, e isso é fácil de acontecer sem ninguém notar — ela comia o
"T" de "UTC" e a tela exibia `19:08:48 U C`.

Foi assim que apareceu no painel, e o defeito parecia de CSS: dava para jurar
que a coluna era estreita e a palavra estava quebrando.
"""

import unittest

from litellm_rtksync.render import format_timestamp


class CarimboDeTempo(unittest.TestCase):
    def test_converte_o_formato_iso(self):
        self.assertEqual(format_timestamp("2026-09-13T19:08:48Z"), "2026-09-13 19:08:48 UTC")

    def test_formatar_duas_vezes_nao_estraga(self):
        uma = format_timestamp("2026-09-13T19:08:48Z")
        self.assertEqual(format_timestamp(uma), uma, "UTC não pode virar 'U C'")

    def test_nao_come_o_t_de_outras_palavras(self):
        """Só o separador da posição 10 é trocado, não todo T da string."""
        self.assertIn("UTC", format_timestamp("2026-09-13T19:08:48Z"))

    def test_valor_vazio_nao_derruba(self):
        for entrada in (None, "", "—"):
            self.assertIsInstance(format_timestamp(entrada), str)

    def test_valor_sem_z_continua_legivel(self):
        self.assertEqual(format_timestamp("2026-09-13T19:08:48"), "2026-09-13 19:08:48")


if __name__ == "__main__":
    unittest.main()
