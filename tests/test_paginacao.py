"""A paginação dos grids: dez por página, e o total continua sendo o total.

Um catálogo de gateway chega a centenas de modelos — 550 numa medição — e sem
paginar a tela rola por minutos. O que estes testes protegem não é o corte em
si, que é trivial, mas as três decisões em volta dele, cada uma com uma
alternativa óbvia e pior.
"""

import unittest

from litellm_rtksync import paginacao


class RecorteDaPagina(unittest.TestCase):
    def setUp(self):
        self.itens = list(range(1, 556))  # 555 itens: 55 páginas cheias e 5 na última

    def test_a_primeira_pagina_traz_dez(self):
        fatia, estado = paginacao.recortar(self.itens, "modelos")
        self.assertEqual(len(fatia), 10)
        self.assertEqual(fatia[0], 1)
        self.assertEqual(estado["pagina"], 1)

    def test_o_total_nao_e_o_tamanho_da_pagina(self):
        """O cabeçalho mostra quantos existem, não quantos couberam."""
        _, estado = paginacao.recortar(self.itens, "modelos")
        self.assertEqual(estado["total"], 555)
        self.assertEqual(estado["ultima"], 56)

    def test_cada_grid_le_o_proprio_parametro(self):
        """Avançar os modelos não pode mover a tabela de conexões."""
        consulta = {"pag_modelos": ["3"]}
        fatia_modelos, _ = paginacao.recortar(self.itens, "modelos", consulta)
        fatia_conexoes, estado_conexoes = paginacao.recortar(self.itens, "conexoes", consulta)
        self.assertEqual(fatia_modelos[0], 21)
        self.assertEqual(fatia_conexoes[0], 1, "o outro grid ficou onde estava")
        self.assertEqual(estado_conexoes["pagina"], 1)

    def test_pagina_alem_do_fim_vira_a_ultima(self):
        """Acontece sozinho quando o catálogo encolhe e o link antigo sobrevive."""
        fatia, estado = paginacao.recortar(self.itens, "modelos", {"pag_modelos": ["999"]})
        self.assertEqual(estado["pagina"], estado["ultima"])
        self.assertTrue(fatia, "a última página não pode vir vazia")

    def test_lixo_na_query_nao_derruba_a_pagina(self):
        for ruim in ("abc", "", "-3", "0", "1.5"):
            _, estado = paginacao.recortar(self.itens, "modelos", {"pag_modelos": [ruim]})
            self.assertGreaterEqual(estado["pagina"], 1)

    def test_lista_vazia_nao_quebra(self):
        fatia, estado = paginacao.recortar([], "modelos")
        self.assertEqual(fatia, [])
        self.assertEqual(estado["total"], 0)
        self.assertEqual(estado["ultima"], 1)

    def test_a_ultima_pagina_traz_o_resto(self):
        fatia, _ = paginacao.recortar(self.itens, "modelos", {"pag_modelos": ["56"]})
        self.assertEqual(len(fatia), 5)
        self.assertEqual(fatia[-1], 555)


class BarraDePaginas(unittest.TestCase):
    def traduzir(self, chave, lang, **kwargs):
        return f"{chave}:{kwargs}"

    def test_some_quando_ha_uma_pagina_so(self):
        """Uma barra de paginação com uma página é ruído."""
        _, estado = paginacao.recortar([1, 2, 3], "modelos")
        self.assertEqual(paginacao.render_paginacao(estado, {}, self.traduzir, "pt"), "")

    def test_os_links_preservam_os_outros_parametros(self):
        """Sem isso, avançar os modelos zeraria a página das conexões."""
        consulta = {"pag_conexoes": ["4"], "lang": ["pt"]}
        _, estado = paginacao.recortar(list(range(50)), "modelos", consulta)
        html = paginacao.render_paginacao(estado, consulta, self.traduzir, "pt")
        self.assertIn("pag_conexoes=4", html)
        self.assertIn("lang=pt", html)

    def test_o_aviso_da_ultima_acao_nao_e_repetido(self):
        """Ele é de uso único: repeti-lo faria a mensagem reaparecer sem fim."""
        consulta = {"aviso": ["Ciclo executado"], "tom": ["success"]}
        _, estado = paginacao.recortar(list(range(50)), "modelos", consulta)
        html = paginacao.render_paginacao(estado, consulta, self.traduzir, "pt")
        self.assertNotIn("aviso=", html)
        self.assertNotIn("tom=", html)

    def test_a_barra_nao_lista_todas_as_paginas(self):
        """Com 56 páginas, listar todas daria uma barra maior que a tabela."""
        _, estado = paginacao.recortar(list(range(555)), "modelos", {"pag_modelos": ["28"]})
        html = paginacao.render_paginacao(estado, {}, self.traduzir, "pt")
        self.assertLessEqual(html.count("page-item"), 12)
        self.assertIn("…", html, "a janela precisa indicar que há mais páginas")

    def test_os_links_sao_links_de_verdade(self):
        """A página é montada no servidor e funciona com o script desligado."""
        _, estado = paginacao.recortar(list(range(50)), "modelos")
        html = paginacao.render_paginacao(estado, {}, self.traduzir, "pt")
        self.assertIn('<a class="page-link" href="?', html)
        self.assertNotIn("onclick", html)


if __name__ == "__main__":
    unittest.main()
