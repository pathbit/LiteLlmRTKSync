"""Motor de agendamento em background (CronScheduler) deste sincronizador.

A classe é a mesma dos projetos irmãos — recebe um callback e um intervalo, e
não sabe nada sobre o que o ciclo faz. O que muda é o **vocabulário do resumo**,
e essa diferença é o motivo de este arquivo não ser uma cópia literal.

Os irmãos renovam credenciais OAuth, então o resumo deles traz `refreshed` e
`total`. Aqui não há o que renovar: o ciclo inspeciona chaves virtuais, modelos
e coerência de limites. A tradução entre os dois mundos acontece em um só lugar
(`summarize_cycle`, que o painel também usa), para que o resto — cartão,
histórico e `/api/cron-status` — continue estruturalmente idêntico ao dos irmãos.
"""

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .identidade import NOME_DO_PRODUTO
from .logs import get_logger


def _extract_log_lines(res: Any) -> List[str]:
    """Extrai as ações registradas pelo motor de inspeção neste ciclo.

    Guarda só o que explica o resultado — erro, achado de limite, credencial
    recusada. Um ciclo sem nada a relatar devolve lista vazia, e a tela mostra
    isso como tal, em vez de fingir que houve trabalho.
    """
    if not isinstance(res, dict):
        return [f"Resultado inesperado do motor: {res!r}"]

    lines: List[str] = []
    # O resumo daqui acumula falhas de leitura numa LISTA (`errors`), e não num
    # campo único como o dos irmãos: uma rota indisponível não derruba o ciclo,
    # então pode haver mais de uma no mesmo ciclo. Todas vão para o log.
    for failure in res.get("errors", []) or []:
        lines.append(f"ERRO: {failure}")
    if res.get("error"):
        lines.append(f"ERRO: {res['error']}")

    for detail in res.get("details", []) or []:
        actions = detail.get("actions") or []
        if not actions:
            continue
        # Não existe "provider" no resumo daqui; o que identifica um item é o
        # que ele é (chave, modelo, limite) mais o nome.
        label = f"{detail.get('kind', '?')} · {detail.get('name', '?')}"
        for action in actions:
            lines.append(f"{label}: {action}")

    return lines


def summarize_cycle(res: Any) -> Tuple[int, int, Optional[str]]:
    """Traduz o resumo do motor para (inspecionados, achados, erro).

    É aqui que os dois vocabulários se encontram, e o mapeamento é deliberado:

    - o que os irmãos chamam de **renovadas** aqui é **o que o ciclo encontrou**
      (`limit_findings + invalid_credentials`), porque é o número que muda de
      ciclo para ciclo e que o operador precisa ver subir;
    - o que eles chamam de **total** aqui é **o que foi inspecionado**
      (`keys + models`), que é a superfície realmente percorrida — times entram
      na avaliação de limites, mas não são itens inspecionados um a um.

    O erro vira texto único porque o histórico mostra uma linha por ciclo.
    """
    if not isinstance(res, dict):
        return 0, 0, "Resultado inesperado do motor"

    findings = int(res.get("limit_findings", 0) or 0) + int(res.get("invalid_credentials", 0) or 0)
    inspected = int(res.get("keys", 0) or 0) + int(res.get("models", 0) or 0)

    failures = [str(e) for e in (res.get("errors") or [])]
    if res.get("error"):
        failures.append(str(res["error"]))
    return inspected, findings, "; ".join(failures) or None


class CronScheduler:
    """Agendador em background que mantém a inspeção contínua do gateway."""

    def __init__(
        self,
        sync_callback: Callable[[], Dict[str, Any]],
        interval_seconds: int = 300,
        name: str = f"{NOME_DO_PRODUTO}-Cron",
    ):
        self.sync_callback = sync_callback
        self.interval_seconds = max(10, interval_seconds)
        self.name = name
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Métricas
        self.total_runs = 0
        self.total_findings = 0
        self.last_run_at: Optional[str] = None
        self.next_run_at: Optional[str] = None
        self.last_result: Optional[Dict[str, Any]] = None
        self.history: List[Dict[str, Any]] = []

    def start(self):
        with self._lock:
            if self.is_running:
                return
            self.is_running = True
            self._stop_event.clear()
            self._update_next_run(self.interval_seconds)
            self._thread = threading.Thread(target=self._run_loop, name=self.name, daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            self.is_running = False
            self._stop_event.set()

    def trigger_now(self) -> Dict[str, Any]:
        return self._execute_cycle(reason="manual_trigger")

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "active": self.is_running,
                "intervalSeconds": self.interval_seconds,
                "totalRuns": self.total_runs,
                "totalFindings": self.total_findings,
                "lastRunAt": self.last_run_at,
                "nextRunAt": self.next_run_at,
                "lastResult": self.last_result,
                "history": list(reversed(self.history[-10:])),
            }

    def _update_next_run(self, delay_seconds: int):
        nxt = time.time() + delay_seconds
        self.next_run_at = datetime.fromtimestamp(nxt, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    def _execute_cycle(self, reason: str = "scheduled_interval") -> Dict[str, Any]:
        start_ts = time.time()
        start_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        get_logger().info(f"[CRON] Ciclo disparado ({reason}). Inspecionando chaves, modelos e limites...")

        try:
            res = self.sync_callback()
        except Exception as e:
            # Falha do callback é resultado de ciclo, não queda do agendador: o
            # laço precisa sobreviver a um proxy fora do ar.
            res = {"success": False, "errors": [str(e)]}

        duration_ms = int((time.time() - start_ts) * 1000)
        inspected, findings, failure = summarize_cycle(res)

        entry = {
            "timestamp": start_iso,
            "reason": reason,
            "durationMs": duration_ms,
            "totalInspected": inspected,
            "findingsCount": findings,
            "success": res.get("success", True) if isinstance(res, dict) else False,
            "error": failure,
            "log": _extract_log_lines(res),
        }

        with self._lock:
            self.total_runs += 1
            self.total_findings += findings
            self.last_run_at = start_iso
            self.last_result = entry
            self.history.append(entry)
            if len(self.history) > 50:
                self.history.pop(0)
            self._update_next_run(self.interval_seconds)

        get_logger().info(
            f"[CRON] Ciclo concluido em {duration_ms}ms: {inspected} item(ns) inspecionado(s), "
            f"{findings} achado(s)."
        )
        return entry

    def _run_loop(self):
        self._execute_cycle(reason="startup")
        while not self._stop_event.is_set():
            interrupted = self._stop_event.wait(timeout=self.interval_seconds)
            if interrupted:
                break
            if self.is_running:
                self._execute_cycle(reason="scheduled_interval")
