"""Os seis cartões existem, sempre, e na mesma ordem dos painéis irmãos.

A regra do dono é uma só: "as telas têm cards diferentes entre LiteLlmRTKSync,
OminiRTkSync e etc, tem que ter todos os cards iguais". O que pode mudar é o
CONTEÚDO; a existência e a ordem, não.

Isto já quebrou uma vez em silêncio: este painel listava chaves virtuais e
modelos e não tinha "conexões monitoradas" nem "combos de resiliência", enquanto
os irmãos tinham os dois e nenhum dos outros. Ninguém viu no diff — cada
repositório, sozinho, parecia coerente. Quem descobriu foi quem abriu as três
telas lado a lado.

Quando o gateway não tem o conceito, o cartão continua na tela com o estado
vazio explicando por quê. É por isso que a guarda confere o cartão VAZIO
também: a tentação, ao portar, é esconder o que não tem dado.
"""

import unittest

from litellm_rtksync.i18n import LANGUAGES, translate
from litellm_rtksync.gateway import fallback_combos
from litellm_rtksync.models import RegisteredModelRecord, group_connections
from litellm_rtksync.render import render_combos_table, render_connections_table, render_dashboard

# A ordem acordada para os três painéis. A chave é o título traduzido de cada
# cartão, porque é isso que o operador vê.
ORDEM_DOS_CARTOES = (
    "gateway.title",       # 1. Conexão com o gateway
    "cron.title",          # 2. Agendador
    "connections.title",   # 3. Conexões monitoradas
    "keys.title",          # 4. Chaves virtuais
    "models.title",        # 5. Modelos cadastrados
    "combos.title",        # 6. Combos de resiliência
)


def cabecalho_do_cartao(chave, lang):
    """O título como ele aparece no CABEÇALHO do cartão, e não em qualquer lugar.

    Procurar só pelo texto traduzido acusa o lugar errado: "Chaves virtuais" é
    também o rótulo de um cartão de métrica lá no topo, que vem antes de todos
    os seis e faria a ordem parecer trocada. O que identifica o cabeçalho é o
    ícone imediatamente antes do texto.
    """
    return f'aria-hidden="true"></i>{translate(chave, lang)}'


def pagina(lang="pt", **extra):
    base = dict(
        keys=[],
        models=[],
        model_states={},
        findings=[],
        counters={},
        cron={"active": False},
        proxy={"url": "http://proxy:4000", "online": True, "latencyMs": 3},
        current_user="admin",
        is_default_password=False,
        refresh_margin=900,
        lang=lang,
    )
    base.update(extra)
    return render_dashboard(**base)


class OsSeisCartoes(unittest.TestCase):
    def test_todos_os_seis_aparecem_em_qualquer_idioma(self):
        for idioma in LANGUAGES:
            html = pagina(idioma)
            for chave in ORDEM_DOS_CARTOES:
                with self.subTest(idioma=idioma, cartao=chave):
                    self.assertIn(cabecalho_do_cartao(chave, idioma), html)

    def test_a_ordem_na_pagina_e_a_ordem_acordada(self):
        html = pagina("pt")
        posicoes = [html.find(cabecalho_do_cartao(c, "pt")) for c in ORDEM_DOS_CARTOES]
        self.assertNotIn(-1, posicoes, "algum cartão não foi desenhado")
        self.assertEqual(
            posicoes,
            sorted(posicoes),
            "a ordem dos cartões saiu trocada: "
            + ", ".join(f"{c}@{p}" for c, p in zip(ORDEM_DOS_CARTOES, posicoes)),
        )

    def test_sem_dado_nenhum_o_cartao_fica_vazio_em_vez_de_sumir(self):
        """Estado vazio honesto é melhor que assimetria — e o vazio diz por quê."""
        html = pagina("pt")
        self.assertIn(translate("connections.title", "pt"), html)
        self.assertIn(translate("connections.empty", "pt"), html)
        self.assertIn(translate("connections.empty_hint", "pt"), html)
        self.assertIn(translate("combos.title", "pt"), html)
        self.assertIn(translate("combos.empty", "pt"), html)
        self.assertIn(translate("combos.empty_hint", "pt"), html)


class ConexoesMonitoradas(unittest.TestCase):
    """A conexão do LiteLLM é o destino por trás dos modelos cadastrados."""

    def modelos(self):
        return [
            RegisteredModelRecord({"model_name": "gateway-a-gemini", "litellm_params": {
                "model": "openai/gemini/gemini-3.8-flash",
                "api_base": "http://um-gateway:20128/v1",
                "litellm_credential_name": "cred-um"}}),
            RegisteredModelRecord({"model_name": "gateway-a-gpt", "litellm_params": {
                "model": "openai/gpt-4o",
                "api_base": "http://um-gateway:20128/v1",
                "litellm_credential_name": "cred-um"}}),
            RegisteredModelRecord({"model_name": "gateway-b-granite", "litellm_params": {
                "model": "openai/openrouter/granite",
                "api_base": "http://outro-gateway:20128/v1",
                "litellm_credential_name": "cred-dois"}}),
        ]

    def test_modelos_do_mesmo_destino_viram_uma_conexao_so(self):
        conexoes = group_connections(self.modelos())
        self.assertEqual([c.name for c in conexoes], ["cred-um", "cred-dois"])
        self.assertEqual([len(c.models) for c in conexoes], [2, 1])

    def test_a_tabela_tem_as_mesmas_sete_colunas_dos_irmaos(self):
        html = render_connections_table(group_connections(self.modelos()), {}, "pt")
        self.assertEqual(html.count("<th "), 7)

    def test_o_pior_veredito_do_destino_e_o_que_aparece(self):
        """Nove modelos aceitos e um recusado é um destino com problema."""
        estados = {"gateway-a-gemini": "valid", "gateway-a-gpt": "invalid"}
        html = render_connections_table(group_connections(self.modelos()), estados, "pt")
        self.assertIn(translate("health.invalid", "pt"), html)

    def test_nenhuma_chave_de_api_chega_a_tela(self):
        segredo = "sk-SEGREDO-DE-DESTINO-QUE-NAO-PODE-VAZAR"
        modelos = [RegisteredModelRecord({"model_name": "m", "litellm_params": {
            "model": "openai/gpt-4o", "api_base": "http://d/v1", "api_key": segredo}})]
        html = render_connections_table(group_connections(modelos), {}, "pt")
        self.assertNotIn(segredo, html)


class CombosDeResiliencia(unittest.TestCase):
    """No LiteLLM o combo é o fallback do roteador — o conceito existe."""

    def test_le_os_tres_tipos_de_fallback(self):
        combos = fallback_combos({
            "fallbacks": [{"gpt-4o": ["gpt-4o-mini", "claude"]}],
            "context_window_fallbacks": [{"gpt-4o": ["gpt-4o-longo"]}],
            "content_policy_fallbacks": [{"gpt-4o": ["modelo-permissivo"]}],
        })
        self.assertEqual([c["kind"] for c in combos],
                         ["general", "context_window", "content_policy"])
        self.assertEqual(combos[0]["models"], ["gpt-4o-mini", "claude"])

    def test_roteador_sem_fallback_nenhum_nao_inventa_linha(self):
        self.assertEqual(fallback_combos({"fallbacks": None, "num_retries": 2}), [])
        self.assertEqual(fallback_combos({}), [])

    def test_o_tipo_nao_geral_aparece_marcado_na_linha(self):
        combos = fallback_combos({"context_window_fallbacks": [{"gpt-4o": ["longo"]}]})
        html = render_combos_table(combos, "pt")
        self.assertIn(translate("combos.kind_context_window", "pt"), html)


if __name__ == "__main__":
    unittest.main()
