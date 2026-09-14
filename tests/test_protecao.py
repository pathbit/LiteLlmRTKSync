"""O freio contra força bruta: teto, espera que cresce e prova de trabalho.

Um painel preso ao loopback não precisa disso. Um painel atrás de um túnel
precisa — e o túnel é um botão que o operador aperta quando quiser, então o
freio tem de já estar instalado quando ele apertar.
"""

import hashlib
import time
import unittest

from litellm_rtksync import protecao


class TetoPorJanela(unittest.TestCase):
    def setUp(self):
        protecao.limpa_apos_sucesso("10.0.0.1")

    def test_ate_o_teto_passa(self):
        for _ in range(protecao.TENTATIVAS_POR_JANELA):
            pode, _ = protecao.registra_tentativa("10.0.0.1")
            self.assertTrue(pode)

    def test_passar_do_teto_manda_esperar(self):
        for _ in range(protecao.TENTATIVAS_POR_JANELA):
            protecao.registra_tentativa("10.0.0.1")
        pode, espere = protecao.registra_tentativa("10.0.0.1")
        self.assertFalse(pode)
        self.assertGreater(espere, 0, "o 429 precisa dizer QUANTO esperar")

    def test_um_endereco_nao_bloqueia_o_outro(self):
        """Senão um único atacante derruba o acesso de todo mundo."""
        for _ in range(protecao.TENTATIVAS_POR_JANELA + 5):
            protecao.registra_tentativa("10.0.0.1")
        pode, _ = protecao.registra_tentativa("10.0.0.2")
        self.assertTrue(pode)
        protecao.limpa_apos_sucesso("10.0.0.2")


class EsperaQueCresce(unittest.TestCase):
    def setUp(self):
        protecao.limpa_apos_sucesso("10.0.0.3")

    def test_sem_falha_nao_ha_espera(self):
        self.assertEqual(protecao.espera_por_falhas("10.0.0.3"), 0.0)

    def test_cada_falha_dobra_a_espera(self):
        protecao.anota_falha("10.0.0.3")
        uma = protecao.espera_por_falhas("10.0.0.3")
        protecao.anota_falha("10.0.0.3")
        duas = protecao.espera_por_falhas("10.0.0.3")
        self.assertGreater(duas, uma)

    def test_a_espera_tem_teto(self):
        """Sem teto, o processo ficaria preso segurando a resposta."""
        for _ in range(40):
            protecao.anota_falha("10.0.0.3")
        self.assertLessEqual(
            protecao.espera_por_falhas("10.0.0.3"), protecao.ESPERA_MAXIMA_EM_SEGUNDOS
        )

    def test_acertar_a_senha_limpa_a_suspeita(self):
        for _ in range(5):
            protecao.anota_falha("10.0.0.3")
        protecao.limpa_apos_sucesso("10.0.0.3")
        self.assertEqual(protecao.espera_por_falhas("10.0.0.3"), 0.0)


class ProvaDeTrabalho(unittest.TestCase):
    def setUp(self):
        protecao.limpa_apos_sucesso("10.0.0.4")

    def test_so_e_exigido_depois_de_algumas_falhas(self):
        self.assertFalse(protecao.precisa_de_desafio("10.0.0.4"))
        for _ in range(protecao.FALHAS_ATE_DESAFIO):
            protecao.anota_falha("10.0.0.4")
        self.assertTrue(protecao.precisa_de_desafio("10.0.0.4"))

    def test_resposta_correta_passa(self):
        desafio = protecao.novo_desafio()
        detalhes = protecao.detalhes_do_desafio(desafio)
        self.assertIsNotNone(detalhes)
        self.assertTrue(protecao.resposta_confere(desafio, detalhes["alvo"]))

    def test_resposta_errada_nao_passa(self):
        desafio = protecao.novo_desafio()
        self.assertFalse(protecao.resposta_confere(desafio, "opcao_invalida_xyz"))

    def test_a_mesma_resposta_nao_serve_duas_vezes(self):
        """Sem consumo, um bot resolveria uma vez e repetiria para sempre."""
        desafio = protecao.novo_desafio()
        detalhes = protecao.detalhes_do_desafio(desafio)
        self.assertTrue(protecao.resposta_confere(desafio, detalhes["alvo"]))
        self.assertFalse(protecao.resposta_confere(desafio, detalhes["alvo"]))

    def test_desafio_inventado_nao_passa(self):
        self.assertFalse(protecao.resposta_confere("desafio-que-nunca-emiti", "key"))

    def test_entrada_vazia_nao_derruba(self):
        for desafio, resposta in (("", ""), ("x", ""), ("", "y")):
            self.assertFalse(protecao.resposta_confere(desafio, resposta))

    def test_detalhes_do_desafio_traz_opcoes_e_alvo_valido(self):
        desafio = protecao.novo_desafio()
        detalhes = protecao.detalhes_do_desafio(desafio)
        self.assertEqual(len(detalhes["opcoes"]), protecao.DIFICULDADE)
        self.assertIn(detalhes["alvo"], detalhes["opcoes"])


class DificuldadeQueCresce(unittest.TestCase):
    """Mais falhas aumentam o número de opções para reduzir chance de acerto ao acaso."""

    def setUp(self):
        protecao.limpa_apos_sucesso("10.0.0.9")

    def test_quem_nunca_errou_paga_o_minimo(self):
        self.assertEqual(protecao.dificuldade_para("10.0.0.9"), protecao.DIFICULDADE)

    def test_insistir_encarece(self):
        for _ in range(protecao.FALHAS_ATE_DESAFIO + 6):
            protecao.anota_falha("10.0.0.9")
        self.assertGreater(
            protecao.dificuldade_para("10.0.0.9"),
            protecao.DIFICULDADE,
            "quem insiste tem de pagar mais caro a cada bloco de falhas",
        )

    def test_a_dificuldade_tem_teto(self):
        for _ in range(200):
            protecao.anota_falha("10.0.0.9")
        self.assertLessEqual(
            protecao.dificuldade_para("10.0.0.9"), protecao.DIFICULDADE_MAXIMA
        )

    def test_novo_desafio_respeita_quantidade(self):
        desafio = protecao.novo_desafio(quantidade=6)
        detalhes = protecao.detalhes_do_desafio(desafio)
        self.assertEqual(len(detalhes["opcoes"]), 6)
        self.assertIn(detalhes["alvo"], detalhes["opcoes"])


class EnderecoDoCliente(unittest.TestCase):
    def test_ignora_a_porta_de_origem(self):
        """A porta muda a cada conexão; contar por ela não limitaria nada."""
        self.assertEqual(protecao.endereco_do_cliente(("192.168.0.7", 54321)), "192.168.0.7")

    def test_entrada_estranha_nao_derruba(self):
        self.assertEqual(protecao.endereco_do_cliente(None), "desconhecido")


if __name__ == "__main__":
    unittest.main()
