"""A sessão do painel: cookie assinado, com prazo, que não se pode forjar.

O painel nasceu só com Basic Auth. O diálogo que o navegador abre para isso é
janela DELE, não página nossa: não se traduz, não se estiliza, não tem logout, e
qualquer ferramenta que dirija um navegador para ali, porque não há campo HTML
para preencher. O formulário resolve os quatro — e o Basic Auth continua aceito,
porque é ele que faz `curl` e monitoramento funcionarem sem sessão.
"""

import time
import unittest

from litellm_rtksync import sessao


class CookieDeSessao(unittest.TestCase):
    def test_o_cookie_emitido_identifica_quem_entrou(self):
        valor = sessao.emitir("admin")
        self.assertEqual(sessao.usuario_da_sessao(valor), "admin")

    def test_cookie_adulterado_nao_vale(self):
        """Trocar o usuário dentro do cookie tem de invalidar a assinatura."""
        valor = sessao.emitir("admin")
        corpo, _, assinatura = valor.rpartition(".")
        forjado = corpo[:-4] + "AAAA." + assinatura
        self.assertIsNone(sessao.usuario_da_sessao(forjado))

    def test_assinatura_trocada_nao_vale(self):
        valor = sessao.emitir("admin")
        corpo, _, _ = valor.rpartition(".")
        self.assertIsNone(sessao.usuario_da_sessao(corpo + "." + "0" * 64))

    def test_cookie_vencido_nao_vale(self):
        """Emitido no passado, com prazo já corrido."""
        antigo = sessao.emitir("admin", agora=time.time() - sessao.VALIDADE_EM_SEGUNDOS - 60)
        self.assertIsNone(sessao.usuario_da_sessao(antigo))

    def test_lixo_nao_derruba_a_verificacao(self):
        for entrada in ("", "sem-ponto", "a.b", "...", "x" * 200):
            self.assertIsNone(sessao.usuario_da_sessao(entrada))

    def test_o_cookie_e_inacessivel_ao_script_da_pagina(self):
        """HttpOnly e SameSite são o que impedem roubo por XSS e por outro site."""
        cabecalho = sessao.cabecalho_para_gravar("qualquer")
        self.assertIn("HttpOnly", cabecalho)
        self.assertIn("SameSite=Strict", cabecalho)
        self.assertIn("Path=/", cabecalho)

    def test_sair_apaga_o_cookie(self):
        self.assertIn("Max-Age=0", sessao.cabecalho_para_apagar())

    def test_le_o_cookie_no_meio_de_outros(self):
        valor = sessao.emitir("admin")
        cru = f"tema=escuro; {sessao.NOME_DO_COOKIE}={valor}; idioma=pt"
        self.assertEqual(sessao.ler_do_cabecalho(cru), valor)
        self.assertEqual(sessao.ler_do_cabecalho("outro=1"), "")

    def test_o_nome_do_cookie_e_proprio_deste_produto(self):
        """Três painéis no mesmo 127.0.0.1 compartilham o espaço de cookies.

        Cookies não se separam por porta: um nome genérico faria o login num
        painel derrubar a sessão dos outros dois.
        """
        self.assertTrue(sessao.NOME_DO_COOKIE.startswith("litellmrtksync"))

    def test_sair_apaga_cookie_com_data_no_passado(self):
        cabecalho = sessao.cabecalho_para_apagar()
        self.assertIn("Max-Age=0", cabecalho)
        self.assertIn("Expires=Thu, 01 Jan 1970 00:00:00 GMT", cabecalho)

    def test_sair_apaga_cookie_de_estado_com_data_no_passado(self):
        cabecalho = sessao.cabecalho_para_apagar_estado()
        self.assertIn("Max-Age=0", cabecalho)
        self.assertIn("Expires=Thu, 01 Jan 1970 00:00:00 GMT", cabecalho)

    def test_cabecalho_seguro_inclui_secure(self):
        cabecalho = sessao.cabecalho_para_gravar("qualquer", seguro=True)
        self.assertIn("; Secure", cabecalho)
        cabecalho_inseguro = sessao.cabecalho_para_gravar("qualquer", seguro=False)
        self.assertNotIn("Secure", cabecalho_inseguro)

    def test_apagar_seguro_inclui_secure(self):
        cabecalho = sessao.cabecalho_para_apagar(seguro=True)
        self.assertIn("; Secure", cabecalho)
        cabecalho_inseguro = sessao.cabecalho_para_apagar(seguro=False)
        self.assertNotIn("Secure", cabecalho_inseguro)

    def test_estado_seguro_inclui_secure(self):
        cabecalho = sessao.cabecalho_para_gravar_estado("qualquer", seguro=True)
        self.assertIn("; Secure", cabecalho)
        cabecalho_apagar = sessao.cabecalho_para_apagar_estado(seguro=True)
        self.assertIn("; Secure", cabecalho_apagar)
        cabecalho_inseguro = sessao.cabecalho_para_gravar_estado("qualquer", seguro=False)
        self.assertNotIn("Secure", cabecalho_inseguro)

    def test_assinatura_vinculada_ao_nome_do_cookie(self):
        """Um cookie emitido para um produto nao pode ser aceito por outro."""
        import hmac
        valor = sessao.emitir("admin")
        self.assertEqual(sessao.usuario_da_sessao(valor), "admin")
        corpo, _, _ = valor.rpartition(".")
        assinatura_outro = hmac.new(
            sessao._SEGREDO,
            b"sessao|outro_cookie_sessao|" + corpo.encode("utf-8"),
            "sha256",
        ).hexdigest()
        token_outro = f"{corpo}.{assinatura_outro}"
        self.assertIsNone(sessao.usuario_da_sessao(token_outro))


if __name__ == "__main__":
    unittest.main()
