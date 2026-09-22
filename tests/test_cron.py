"""O agendador conta o que este sincronizador realmente faz.

A classe é a mesma dos projetos irmãos, mas o resumo que ela recebe é outro: lá
o ciclo devolve `refreshed`/`total` (tokens renovados), aqui devolve `keys`,
`models`, `limit_findings` e `invalid_credentials`. Se a tradução entre os dois
vocabulários se perder, o painel mostra `0 avaliadas · 0 renovadas` para sempre
e ninguém percebe — nada quebra, o número só para de significar algo.

Estes testes fixam a tradução e o que ela não pode engolir: a falha de um ciclo
precisa chegar ao histórico com o motivo escrito.
"""

import threading
import time
import unittest

from litellm_rtksync.cron import CronScheduler

# Um ciclo típico: 2 chaves e 1 modelo inspecionados, 1 achado de limite e 1
# credencial recusada.
RESUMO = {
    "success": True,
    "keys": 2,
    "teams": 1,
    "models": 1,
    "expiring": 1,
    "invalid_credentials": 1,
    "limit_findings": 1,
    "details": [
        {"kind": "limit", "name": "producao", "status": "incoerente",
         "actions": ["rpm_limit: a chave declara 600, acima do teto de 60 do time"]},
        {"kind": "model", "name": "gpt-4o", "status": "invalid",
         "actions": ["chave RECUSADA pelo provedor (HTTP 401)"]},
        {"kind": "key", "name": "sem-achado", "status": "active", "actions": []},
    ],
    "errors": [],
}


class TestAgendador(unittest.TestCase):
    def test_a_cycle_counts_what_was_inspected_and_what_was_found(self):
        chamadas = []

        def ciclo_falso():
            chamadas.append(1)
            return RESUMO

        cron = CronScheduler(sync_callback=ciclo_falso, interval_seconds=60)
        inicial = cron.get_status()
        self.assertFalse(inicial["active"])
        self.assertEqual(inicial["totalRuns"], 0)

        entrada = cron.trigger_now()
        self.assertEqual(len(chamadas), 1)
        self.assertTrue(entrada["success"])
        # Inspecionado = chaves + modelos; times entram na avaliação de limites,
        # mas não são itens percorridos um a um.
        self.assertEqual(entrada["totalInspected"], 3)
        # Achado = limite incoerente + credencial recusada.
        self.assertEqual(entrada["findingsCount"], 2)
        self.assertIsNone(entrada["error"])

        depois = cron.get_status()
        self.assertEqual(depois["totalRuns"], 1)
        self.assertEqual(depois["totalFindings"], 2)
        self.assertEqual(len(depois["history"]), 1)

    def test_the_log_carries_the_actions_and_names_what_produced_them(self):
        cron = CronScheduler(sync_callback=lambda: RESUMO, interval_seconds=60)
        linhas = cron.trigger_now()["log"]
        # Só o que tem ação entra: a chave sem achado não polui o histórico.
        self.assertEqual(len(linhas), 2)
        self.assertTrue(any("limit · producao" in line for line in linhas), linhas)
        self.assertTrue(any("model · gpt-4o" in line for line in linhas), linhas)
        self.assertTrue(any("acima do teto" in line for line in linhas), linhas)

    def test_a_partial_read_failure_reaches_the_history_with_its_reason(self):
        """`errors` é uma lista: uma rota fora do ar não derruba o ciclo inteiro."""
        resumo = dict(RESUMO, success=False, errors=["modelos: HTTP 404", "times: timeout"])
        cron = CronScheduler(sync_callback=lambda: resumo, interval_seconds=60)
        entrada = cron.trigger_now()

        self.assertFalse(entrada["success"])
        self.assertIn("modelos: HTTP 404", entrada["error"])
        self.assertIn("times: timeout", entrada["error"])
        self.assertTrue(any("ERRO: modelos: HTTP 404" in line for line in entrada["log"]))

    def test_a_callback_that_raises_becomes_a_failed_cycle_not_a_dead_scheduler(self):
        def ciclo_que_explode():
            raise RuntimeError("proxy fora do ar")

        cron = CronScheduler(sync_callback=ciclo_que_explode, interval_seconds=60)
        entrada = cron.trigger_now()

        self.assertFalse(entrada["success"])
        self.assertIn("proxy fora do ar", entrada["error"])
        # E o agendador segue vivo para o próximo ciclo.
        self.assertEqual(cron.get_status()["totalRuns"], 1)

    def test_starting_the_scheduler_fires_a_cycle_on_its_own(self):
        """Sem o ciclo de partida, a tela abre vazia até o primeiro intervalo vencer."""
        disparou = threading.Event()

        def ciclo_falso():
            disparou.set()
            return RESUMO

        cron = CronScheduler(sync_callback=ciclo_falso, interval_seconds=60)
        cron.start()
        try:
            # Espera o evento em vez de dormir: o intervalo mínimo é de 10s, e
            # um sleep fixo tornaria o teste lento ou instável.
            self.assertTrue(disparou.wait(timeout=5), "o agendador não disparou o ciclo")

            # O evento é marcado na primeira linha do callback, mas o contador
            # só sobe quando ele RETORNA. Checar o contador aqui mesmo era uma
            # corrida que a máquina sob carga perdia: o teste via totalRuns=0
            # com o ciclo já em andamento. Espera a contabilização, não a
            # partida.
            limite = time.monotonic() + 5
            while cron.get_status()["totalRuns"] < 1 and time.monotonic() < limite:
                time.sleep(0.01)

            estado = cron.get_status()
            self.assertTrue(estado["active"])
            self.assertGreaterEqual(estado["totalRuns"], 1)
            self.assertTrue(estado["nextRunAt"])
        finally:
            cron.stop()

    def test_the_history_keeps_one_entry_per_run_newest_first(self):
        cron = CronScheduler(sync_callback=lambda: RESUMO, interval_seconds=60)
        for _ in range(3):
            cron.trigger_now()

        estado = cron.get_status()
        self.assertEqual(estado["totalRuns"], 3)
        self.assertEqual(len(estado["history"]), 3)
        self.assertEqual(estado["lastResult"], estado["history"][0])

    def test_a_too_short_interval_is_raised_to_the_floor(self):
        """Intervalo de 1s transformaria o agendador num laço quente sobre o proxy."""
        self.assertEqual(CronScheduler(sync_callback=lambda: RESUMO, interval_seconds=1).interval_seconds, 10)


if __name__ == "__main__":
    unittest.main()
