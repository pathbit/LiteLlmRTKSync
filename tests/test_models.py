"""Estado de uma chave virtual e de um modelo cadastrado.

Duas regras herdadas dos projetos irmãos, e que valem igual aqui:

- validade não declarada **não** é "ilimitada" — dizer "ilimitado" afirma algo
  que o dado não sustenta, e foi exatamente a queixa que motivou a regra;
- nenhuma projeção devolve segredo: a chave virtual aparece por apelido ou
  mascarada, e do modelo sai apenas se *tem* chave, nunca qual é.
"""

import unittest
from datetime import datetime, timedelta, timezone

from litellm_rtksync.models import (
    HEALTH_ACTIVE,
    HEALTH_BLOCKED,
    HEALTH_EXPIRED,
    HEALTH_EXPIRING_SOON,
    HEALTH_OVER_BUDGET,
    RegisteredModelRecord,
    VirtualKeyRecord,
    parse_instant,
    summarize,
)

AGORA = datetime.now(timezone.utc)


def daqui(**delta):
    return (AGORA + timedelta(**delta)).isoformat().replace("+00:00", "Z")


class TestLeituraDeInstante(unittest.TestCase):
    def test_iso_with_a_zulu_suffix(self):
        self.assertEqual(parse_instant("2026-09-12T15:20:22.362Z").year, 2026)

    def test_a_numeric_epoch_written_as_text(self):
        """A forma que originou toda esta família de projetos."""
        instante = parse_instant("1789226422362")
        self.assertIsNotNone(instante, "epoch em texto nao pode virar None")
        self.assertEqual(instante.year, 2026)

    def test_seconds_and_milliseconds_land_on_the_same_moment(self):
        self.assertEqual(parse_instant(1789226422), parse_instant(1789226422000))

    def test_a_naive_timestamp_is_assumed_utc(self):
        self.assertEqual(parse_instant("2026-09-12T15:20:22").tzinfo, timezone.utc)

    def test_nonsense_is_none_and_never_raises(self):
        for valor in (None, "", "amanhã", [], {}):
            self.assertIsNone(parse_instant(valor))


class TestSaudeDaChave(unittest.TestCase):
    def test_a_key_well_inside_its_validity_is_active(self):
        chave = VirtualKeyRecord({"key_alias": "k", "expires": daqui(days=30)})
        self.assertEqual(chave.health_status(), HEALTH_ACTIVE)

    def test_a_key_inside_the_margin_is_expiring(self):
        chave = VirtualKeyRecord({"key_alias": "k", "expires": daqui(minutes=5)})
        self.assertEqual(chave.health_status(margin_seconds=900), HEALTH_EXPIRING_SOON)

    def test_a_key_past_its_validity_is_expired(self):
        chave = VirtualKeyRecord({"key_alias": "k", "expires": daqui(days=-1)})
        self.assertEqual(chave.health_status(), HEALTH_EXPIRED)

    def test_a_blocked_key_is_blocked_before_anything_else(self):
        chave = VirtualKeyRecord({"key_alias": "k", "blocked": True, "expires": daqui(days=30)})
        self.assertEqual(chave.health_status(), HEALTH_BLOCKED)

    def test_a_key_at_its_budget_is_over_budget(self):
        chave = VirtualKeyRecord({"key_alias": "k", "spend": 10.0, "max_budget": 10.0})
        self.assertEqual(chave.health_status(), HEALTH_OVER_BUDGET)

    def test_an_expired_key_that_also_burned_its_budget_reads_as_expired(self):
        chave = VirtualKeyRecord({"key_alias": "k", "spend": 99, "max_budget": 10,
                            "expires": daqui(days=-1)})
        self.assertEqual(chave.health_status(), HEALTH_EXPIRED)

    def test_a_key_with_no_declared_expiry_is_active_and_never_unlimited(self):
        chave = VirtualKeyRecord({"key_alias": "k"})
        self.assertEqual(chave.health_status(), HEALTH_ACTIVE)
        self.assertIsNone(chave.remaining_seconds)
        projecao = str(chave.to_dict()).lower()
        self.assertNotIn("unlimited", projecao)
        self.assertNotIn("ilimitad", projecao)

    def test_a_zero_budget_is_no_budget_not_an_exhausted_one(self):
        chave = VirtualKeyRecord({"key_alias": "k", "spend": 0.0, "max_budget": 0})
        self.assertIsNone(chave.max_budget)
        self.assertEqual(chave.health_status(), HEALTH_ACTIVE)


class TestIdentificacaoDaChave(unittest.TestCase):
    def test_the_alias_wins_when_there_is_one(self):
        self.assertEqual(VirtualKeyRecord({"key_alias": "producao"}).alias, "producao")

    def test_without_an_alias_the_token_is_masked(self):
        chave = VirtualKeyRecord({"token": "sk-SEGREDO-QUE-NAO-PODE-VAZAR-123456"})
        self.assertNotIn("SEGREDO", chave.alias)
        self.assertTrue(chave.alias.endswith("123456"))

    def test_the_projection_never_carries_the_token(self):
        chave = VirtualKeyRecord({"token": "sk-SEGREDO-QUE-NAO-PODE-VAZAR-123456",
                            "expires": daqui(days=1)})
        self.assertNotIn("SEGREDO", str(chave.to_dict()))


class TestModeloCadastrado(unittest.TestCase):
    def test_the_provider_comes_from_the_model_prefix(self):
        m = RegisteredModelRecord({"model_name": "gpt-4o", "litellm_params": {"model": "openai/gpt-4o"}})
        self.assertEqual(m.provider, "openai")

    def test_a_key_held_in_the_environment_is_reported_as_such(self):
        m = RegisteredModelRecord({"model_name": "x", "litellm_params": {"api_key": "os.environ/OPENAI_API_KEY"}})
        self.assertTrue(m.key_is_env_reference)

    def test_the_projection_says_whether_there_is_a_key_never_which(self):
        m = RegisteredModelRecord({"model_name": "x", "litellm_params": {"api_key": "sk-SEGREDO-INTEIRO-AQUI"}})
        projetado = m.to_dict()
        self.assertTrue(projetado["hasApiKey"])
        self.assertNotIn("SEGREDO", str(projetado))

    def test_a_named_credential_is_detected(self):
        m = RegisteredModelRecord({"model_name": "x", "litellm_params": {"litellm_credential_name": "azure-prod"}})
        self.assertTrue(m.uses_named_credential)

    def test_malformed_params_never_raise(self):
        m = RegisteredModelRecord({"model_name": "x", "litellm_params": "isto-nao-e-um-dicionario"})
        self.assertEqual(m.params, {})
        self.assertEqual(m.provider, "")
        self.assertFalse(m.to_dict()["hasApiKey"])


class TestResumo(unittest.TestCase):
    def test_the_summary_counts_each_state(self):
        chaves = [
            VirtualKeyRecord({"key_alias": "a", "expires": daqui(days=30)}),
            VirtualKeyRecord({"key_alias": "b", "expires": daqui(minutes=5)}),
            VirtualKeyRecord({"key_alias": "c", "expires": daqui(days=-1)}),
            VirtualKeyRecord({"key_alias": "d", "blocked": True}),
        ]
        resumo = summarize(chaves, margin_seconds=900)
        self.assertEqual(resumo[HEALTH_ACTIVE], 1)
        self.assertEqual(resumo[HEALTH_EXPIRING_SOON], 1)
        self.assertEqual(resumo[HEALTH_EXPIRED], 1)
        self.assertEqual(resumo[HEALTH_BLOCKED], 1)


if __name__ == "__main__":
    unittest.main()
