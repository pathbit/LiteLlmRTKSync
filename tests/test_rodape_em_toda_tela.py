"""A assinatura da Pathbit aparece em TODA tela, e uma vez só.

O pedido foi "nos footers coloque algo assim em tudo". "Em tudo" é literal: o
rodapé existia apenas no painel, e a tela de LOGIN -- a única que alguém vê sem
estar autenticado, e portanto a mais pública das quatro -- não tinha nenhum.

Este teste renderiza as quatro telas e conta a assinatura em cada uma. Conta,
e não procura: quando o rodapé passou a vir de uma função, a versão escrita à
mão continuou no painel por um momento, e as duas apareceram juntas. "Existe"
não era suficiente; "existe uma vez" é.

O ano não é conferido contra um literal de propósito -- ele vem do relógio, e um
teste que fixasse 2026 quebraria sozinho em primeiro de janeiro, que é
exatamente o defeito que o `datetime.now().year` existe para evitar.
"""

import datetime
import os
import re
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "src"))


def pacote():
    origem = os.path.join(RAIZ, "src")
    for nome in sorted(os.listdir(origem)):
        if os.path.isfile(os.path.join(origem, nome, "identidade.py")):
            return nome
    raise AssertionError("este repositório não tem src/<pacote>/identidade.py")


PACOTE = pacote()


class ORodapeApareceEmTodaTela(unittest.TestCase):
    def setUp(self):
        from importlib import import_module
        self.render = import_module(f"{PACOTE}.render")

    def telas(self):
        """As telas do PRODUTO -- que não são todas as páginas servidas.

        `render_notice_page` fica de fora, e a razão é de segurança: ela é o
        corpo das respostas 401 e 429, servidas a quem ainda não se autenticou,
        e a assinatura traz "Pathbit" e o ano na mesma linha -- exatamente os
        dois pedaços da senha deste projeto. Existe um teste
        (`test_the_401_body_never_teaches_the_credentials`) que proíbe essas
        palavras ali, porque um dia houve naquele corpo um banner que ensinava a
        credencial. A saída certa não é afrouxar aquele teste: é não dar a ele
        nada para encontrar.
        """
        return {
            "entrada": lambda: self.render.render_landing_page("pt"),
            "login": lambda: self.render.render_login_page(lang="pt"),
            "painel": lambda: self.render.render_dashboard(lang="pt"),
        }

    def test_a_pagina_de_aviso_nao_traz_a_assinatura(self):
        """O corpo do 401 e do 429 não pode carregar pista da credencial."""
        html = self.html(lambda: self.render.render_notice_page("título", "corpo"))
        self.assertNotIn(
            "Pathbit", html,
            "a assinatura entrou no corpo do 401/429: ela traz o nome e o ano, "
            "que são os dois pedaços da senha do projeto",
        )

    def html(self, desenha):
        saida = desenha()
        return saida.decode("utf-8") if isinstance(saida, bytes) else saida

    def test_cada_tela_traz_a_assinatura_uma_vez(self):
        for nome, desenha in self.telas().items():
            with self.subTest(tela=nome):
                vezes = self.html(desenha).count("by Pathbit")
                self.assertEqual(
                    vezes, 1,
                    f"a tela '{nome}' traz a assinatura {vezes} vez(es). Zero "
                    f"significa que 'em tudo' não valeu para ela; mais de uma "
                    f"significa que a versão escrita à mão sobreviveu ao lado "
                    f"da função.",
                )

    def test_o_ano_vem_do_relogio_e_nao_do_teclado(self):
        html = self.html(self.telas()["login"])
        achado = re.search(r"reserved \(c\) (\d{4})", html)
        self.assertIsNotNone(achado, "não achei o ano no rodapé")
        self.assertEqual(
            achado.group(1), str(datetime.datetime.now().year),
            "o ano do rodapé não é o ano corrente -- se ele estiver escrito à "
            "mão, fica errado em primeiro de janeiro e ninguém revisa rodapé",
        )

    def test_o_coracao_e_icone_e_nunca_emoji(self):
        """O cabeçalho do render fixa "Bootstrap Icons, nunca emoji"."""
        html = self.html(self.telas()["login"])
        self.assertIn("bi-heart-fill", html, "o coração tem de ser Bootstrap Icon")
        for emoji in ("💜", "❤", "♥"):
            self.assertNotIn(
                emoji, html,
                f"emoji {emoji!r} no rodapé: o desenho muda conforme o sistema "
                f"de quem olha, e a regra do projeto é ícone de fonte",
            )

    def test_o_rodape_fica_no_fim_da_tela_de_login(self):
        """O rodape fica no final da pagina (abaixo do cartao), nao ao lado.

        O body precisa ser d-flex flex-column min-vh-100 para que o flexbox
        organize os elementos verticalmente, e o card deve ficar dentro de um
        wrapper com flex-grow-1 empurrando o rodape para a base da tela.
        """
        html = self.html(self.telas()["login"])
        self.assertIn("d-flex flex-column min-vh-100 justify-content-between", html)
        self.assertIn("flex-grow-1", html)
        pos_card = html.find("<main")
        pos_rodape = html.find("<footer")
        self.assertGreater(pos_rodape, pos_card, "O rodapé deve ficar abaixo do cartão de login")

    def test_mensagem_de_logout_aparece_no_login(self):
        """Quando o usuario faz logout, o login deve exibir o banner informativo."""
        html = self.render.render_login_page(mensagem="Sessão encerrada com sucesso.").decode("utf-8")
        self.assertIn("alert-info", html)
        self.assertIn("Sessão encerrada com sucesso.", html)


if __name__ == "__main__":
    unittest.main()
