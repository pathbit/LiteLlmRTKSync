"""Testes da politica de senha e da credencial gravada em SQLite."""

import os
import tempfile
import unittest

from litellm_rtksync.auth import (
    hash_password,
    password_matches,
    read_db_credentials,
    validate_password_strength,
    write_db_credentials,
)
from litellm_rtksync.config import Settings
from litellm_rtksync.prefs import resolve_prefs_path


class TestPasswordStrength(unittest.TestCase):
    def test_accepts_a_password_meeting_every_rule(self):
        self.assertEqual(validate_password_strength("Sample1!"), [])

    def test_reports_every_broken_rule_at_once(self):
        """Uma regra por tentativa faria o usuario adivinhar a politica aos poucos."""
        problems = validate_password_strength("abc")
        self.assertIn("password.too_short", problems)
        self.assertIn("password.needs_upper", problems)
        self.assertIn("password.needs_digit", problems)
        self.assertIn("password.needs_special", problems)

    def test_eight_characters_is_the_floor(self):
        self.assertIn("password.too_short", validate_password_strength("Ab1!cde"))
        self.assertEqual(validate_password_strength("Ab1!cdef"), [])

    def test_twenty_characters_is_the_ceiling(self):
        self.assertEqual(validate_password_strength("Ab1!" + "x" * 16), [])
        self.assertIn("password.too_long", validate_password_strength("Ab1!" + "x" * 17))

    def test_each_class_is_required(self):
        self.assertEqual(validate_password_strength("ABC123!@"), ["password.needs_lower"])
        self.assertEqual(validate_password_strength("abc123!@"), ["password.needs_upper"])
        self.assertEqual(validate_password_strength("Abcdef!@"), ["password.needs_digit"])
        self.assertEqual(validate_password_strength("Abcdef12"), ["password.needs_special"])

    def test_the_factory_password_would_be_refused_today(self):
        self.assertTrue(validate_password_strength("pathbit"))


class TestPasswordHashing(unittest.TestCase):
    def test_hash_is_salted_so_two_hashes_never_match(self):
        self.assertNotEqual(hash_password("Sample1!"), hash_password("Sample1!"))

    def test_the_password_never_appears_in_the_stored_value(self):
        self.assertNotIn("Sample1!", hash_password("Sample1!"))

    def test_matching_and_non_matching(self):
        stored = hash_password("Sample1!")
        self.assertTrue(password_matches(stored, "Sample1!"))
        self.assertFalse(password_matches(stored, "Pathbit1"))
        self.assertFalse(password_matches(stored, ""))

    def test_legacy_plaintext_still_authenticates(self):
        """Quem ja tinha senha no arquivo antigo nao pode ficar trancado do lado de fora."""
        self.assertTrue(password_matches("senha-antiga", "senha-antiga"))
        self.assertFalse(password_matches("senha-antiga", "outra"))

    def test_corrupted_stored_value_denies_access(self):
        self.assertFalse(password_matches("pbkdf2_sha256$quebrado", "qualquer"))


class TestCredentialsInSqlite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(data_dir=self.tmp.name)

    def test_no_password_stored_means_the_banner_shows(self):
        self.assertTrue(self.settings.is_default_password())
        self.assertFalse(self.settings.has_stored_password())

    def test_the_banner_disappears_once_a_password_is_stored(self):
        self.assertTrue(self.settings.update_auth_credentials("admin", "Sample1!"))
        self.assertTrue(self.settings.has_stored_password())
        self.assertFalse(self.settings.is_default_password())

    def test_a_weak_password_is_refused_and_changes_nothing(self):
        self.assertFalse(self.settings.update_auth_credentials("admin", "fraca"))
        self.assertFalse(self.settings.has_stored_password())
        self.assertTrue(self.settings.is_default_password())

    def test_the_stored_password_authenticates_and_the_old_one_stops(self):
        self.settings.update_auth_credentials("admin", "Sample1!")
        self.assertTrue(self.settings.verify_credentials("admin", "Sample1!"))
        self.assertFalse(self.settings.verify_credentials("admin", "pathbit"))

    def test_the_password_is_never_written_in_clear_text(self):
        self.settings.update_auth_credentials("admin", "Sample1!")
        prefs = resolve_prefs_path(self.tmp.name)
        with open(prefs, "rb") as handle:
            raw = handle.read()
        self.assertNotIn(b"Sample1!", raw)

    def test_headless_mode_refuses_to_write(self):
        """Com a senha vindo do ambiente, gravar aqui criaria estado que ninguem le."""
        headless = Settings(data_dir=self.tmp.name, dashboard_auth_from_env=True)
        self.assertFalse(headless.update_auth_credentials("admin", "Sample1!"))
        # E o banner nao aparece: quem opera o ambiente ja controla o segredo.
        self.assertFalse(headless.is_default_password())

    def test_credentials_round_trip_through_the_database(self):
        prefs = resolve_prefs_path(self.tmp.name)
        self.assertIsNone(read_db_credentials(prefs))
        self.assertTrue(write_db_credentials(prefs, "operador", "Sample1!"))
        user, stored = read_db_credentials(prefs)
        self.assertEqual(user, "operador")
        self.assertTrue(password_matches(stored, "Sample1!"))


class TestNoFactoryPassword(unittest.TestCase):
    """Uma senha padrao estatica e, por definicao, uma credencial publica."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_the_settings_default_carries_no_password(self):
        self.assertEqual(Settings(data_dir=self.tmp.name).dashboard_password, "")

    def test_no_guessable_password_opens_the_panel(self):
        s = Settings(data_dir=self.tmp.name)
        s.ensure_recovery_hash()
        for guess in ("pathbit", "admin", "", "password", "123456", "litellmrtksync"):
            self.assertFalse(s.verify_credentials("admin", guess), guess)

    def test_the_recovery_credential_is_the_only_way_in_before_a_password_is_set(self):
        s = Settings(data_dir=self.tmp.name)
        recovery, _ = s.ensure_recovery_hash()
        self.assertTrue(s.verify_credentials("admin", recovery))

    def test_the_recovery_credential_is_long_enough_to_resist_guessing(self):
        s = Settings(data_dir=self.tmp.name)
        recovery, _ = s.ensure_recovery_hash()
        self.assertGreaterEqual(len(recovery), 32)


class TestEnvManagedAuthNeedsAPassword(unittest.TestCase):
    def setUp(self):
        self.original = {
            chave: os.environ.get(chave)
            for chave in ("DASHBOARD_USER", "DASHBOARD_PASSWORD")
        }
        self.addCleanup(self.restaurar)
        for chave in self.original:
            os.environ.pop(chave, None)

    def settings(self):
        return Settings.from_env()

    def restaurar(self):
        for chave, valor in self.original.items():
            if valor is None:
                os.environ.pop(chave, None)
            else:
                os.environ[chave] = valor

    def test_only_the_user_set_is_not_env_managed(self):
        os.environ["DASHBOARD_USER"] = "admin"
        self.assertFalse(self.settings().dashboard_auth_from_env)

    def test_the_shape_shipped_in_the_compose_example_is_not_env_managed(self):
        os.environ["DASHBOARD_USER"] = "admin"
        os.environ["DASHBOARD_PASSWORD"] = ""
        settings = self.settings()
        self.assertFalse(settings.dashboard_auth_from_env)
        self.assertEqual(settings.dashboard_user, "admin")

    def test_a_real_password_is_env_managed(self):
        os.environ["DASHBOARD_PASSWORD"] = "Sample1!"
        self.assertTrue(self.settings().dashboard_auth_from_env)

    def test_nothing_set_is_not_env_managed(self):
        self.assertFalse(self.settings().dashboard_auth_from_env)


if __name__ == "__main__":
    unittest.main()
