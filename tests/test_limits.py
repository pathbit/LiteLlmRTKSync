"""Coerência da hierarquia de limites.

O LiteLLM aceita, sem reclamar, uma chave cujo `rpm_limit` é dez vezes o do time
que a contém — verificado contra um proxy real, não deduzido do código. O limite
que vale na prática é sempre o mais restritivo do caminho, então o número maior
existe só no cadastro. Quem configurou acha que tem 600 e recebe 60.
"""

import unittest

from litellm_rtksync.gateway import (
    CAMPOS_DE_TETO,
    SEVERIDADE_INCOERENTE,
    SEVERIDADE_SEM_TETO,
    avaliar,
)


def chave(alias, team_id=None, **limites):
    return {"key_alias": alias, "team_id": team_id, **limites}


def time(team_id, alias, **limites):
    return {"team_id": team_id, "team_alias": alias, **limites}


class TestChaveContraTime(unittest.TestCase):
    def setUp(self):
        self.times = [time("t1", "time-restrito", rpm_limit=60, tpm_limit=10_000, max_budget=10)]

    def test_a_key_within_the_team_cap_raises_nothing(self):
        rel = avaliar([chave("ok", "t1", rpm_limit=30)], self.times)
        self.assertTrue(rel.coerente)
        self.assertEqual(rel.por_severidade(SEVERIDADE_INCOERENTE), [])

    def test_a_key_above_the_team_cap_is_flagged(self):
        rel = avaliar([chave("demais", "t1", rpm_limit=600)], self.times)
        self.assertFalse(rel.coerente)
        achados = rel.por_severidade(SEVERIDADE_INCOERENTE)
        self.assertEqual(len(achados), 1)
        self.assertEqual(achados[0].campo, "rpm_limit")
        self.assertEqual(achados[0].valor_da_chave, 600)
        self.assertEqual(achados[0].valor_do_teto, 60)

    def test_a_key_equal_to_the_cap_is_coherent(self):
        """Igual não é maior: o limite vale exatamente como está escrito."""
        rel = avaliar([chave("no-limite", "t1", rpm_limit=60)], self.times)
        self.assertTrue(rel.coerente)

    def test_every_capped_field_is_checked_not_just_rpm(self):
        excessiva = chave("tudo-demais", "t1", rpm_limit=61, tpm_limit=10_001, max_budget=11)
        rel = avaliar([excessiva], self.times)
        campos = {a.campo for a in rel.por_severidade(SEVERIDADE_INCOERENTE)}
        self.assertEqual(campos, {"rpm_limit", "tpm_limit", "max_budget"})

    def test_a_key_without_a_limit_inherits_and_is_not_flagged(self):
        rel = avaliar([chave("sem-limite", "t1")], self.times)
        self.assertTrue(rel.coerente)
        self.assertEqual(rel.achados, [])

    def test_the_message_says_what_actually_happens(self):
        rel = avaliar([chave("demais", "t1", rpm_limit=600)], self.times)
        mensagem = rel.por_severidade(SEVERIDADE_INCOERENTE)[0].mensagem
        self.assertIn("600", mensagem)
        self.assertIn("60", mensagem)
        self.assertIn("time-restrito", mensagem)
        # O ponto da mensagem e dizer a consequencia, nao so o numero.
        self.assertIn("vale o teto", mensagem)


class TestChaveSemTime(unittest.TestCase):
    def test_a_teamless_key_is_measured_against_the_platform_default(self):
        rel = avaliar([chave("avulsa", None, rpm_limit=5000)], [], {"rpm_limit": 1000})
        achados = rel.por_severidade(SEVERIDADE_INCOERENTE)
        self.assertEqual(len(achados), 1)
        self.assertEqual(achados[0].nivel_do_teto, "padrão da plataforma")

    def test_with_no_cap_anywhere_the_case_is_reported_but_is_not_an_error(self):
        rel = avaliar([chave("avulsa", None, rpm_limit=5000)], [], {})
        self.assertTrue(rel.coerente, "sem teto e uma escolha, nao um erro")
        self.assertEqual(len(rel.por_severidade(SEVERIDADE_SEM_TETO)), 1)


class TestTimeContraPlataforma(unittest.TestCase):
    def test_a_team_above_the_platform_default_is_flagged(self):
        rel = avaliar([], [time("t1", "time-folgado", rpm_limit=5000)], {"rpm_limit": 1000})
        achados = rel.por_severidade(SEVERIDADE_INCOERENTE)
        self.assertEqual(len(achados), 1)
        self.assertEqual(achados[0].key_alias, "time-folgado")

    def test_a_team_within_the_platform_default_raises_nothing(self):
        rel = avaliar([], [time("t1", "time-ok", rpm_limit=500)], {"rpm_limit": 1000})
        self.assertTrue(rel.coerente)


class TestValoresDegenerados(unittest.TestCase):
    """Zero, vazio e texto aparecem no cadastro real e não podem virar acusação."""

    def test_zero_is_treated_as_no_limit_not_as_a_cap_of_zero(self):
        # Um teto zero acusaria incoerencia em toda chave da instalacao.
        rel = avaliar([chave("k", "t1", rpm_limit=10)], [time("t1", "t", rpm_limit=0)])
        self.assertTrue(rel.coerente)
        self.assertEqual(len(rel.por_severidade(SEVERIDADE_SEM_TETO)), 1)

    def test_an_empty_string_is_not_a_limit(self):
        rel = avaliar([chave("k", "t1", rpm_limit="")], [time("t1", "t", rpm_limit=60)])
        self.assertEqual(rel.achados, [])

    def test_unparseable_text_never_raises(self):
        rel = avaliar([chave("k", "t1", rpm_limit="ilimitado")], [time("t1", "t", rpm_limit=60)])
        self.assertEqual(rel.achados, [])

    def test_a_key_pointing_at_an_unknown_team_falls_back_to_the_platform(self):
        rel = avaliar([chave("k", "time-que-nao-existe", rpm_limit=5000)], [], {"rpm_limit": 100})
        self.assertEqual(len(rel.por_severidade(SEVERIDADE_INCOERENTE)), 1)

    def test_the_field_list_is_the_one_the_schema_actually_has(self):
        self.assertEqual(
            set(CAMPOS_DE_TETO),
            {"tpm_limit", "rpm_limit", "max_parallel_requests", "max_budget"},
        )


class TestRelatorio(unittest.TestCase):
    def test_the_count_covers_every_key_even_the_healthy_ones(self):
        rel = avaliar(
            [chave("a", "t1", rpm_limit=10), chave("b", "t1", rpm_limit=600)],
            [time("t1", "t", rpm_limit=60)],
        )
        self.assertEqual(rel.chaves_avaliadas, 2)
        self.assertEqual(len(rel.por_severidade(SEVERIDADE_INCOERENTE)), 1)

    def test_the_finding_serializes_without_leaking_a_token(self):
        rel = avaliar([{"token": "sk-SEGREDO-INTEIRO", "team_id": "t1", "rpm_limit": 600}],
                      [time("t1", "t", rpm_limit=60)])
        bruto = str(rel.por_severidade(SEVERIDADE_INCOERENTE)[0].to_dict())
        self.assertNotIn("sk-SEGREDO-INTEIRO", bruto)


if __name__ == "__main__":
    unittest.main()
