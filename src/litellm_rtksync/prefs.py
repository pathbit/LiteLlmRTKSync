"""Preferências da interface persistidas em SQLite.

Usa um banco próprio do sincronizador, nunca o SQLite do gateway: escrever
tabelas nossas no banco do LiteLLM criaria acoplamento de schema e risco de
conflito com as migrações dele.

O caminho segue o mesmo diretório das demais credenciais locais do painel, então
a preferência sobrevive a troca de navegador, aba anônima e limpeza de cache —
ao contrário do localStorage.
"""

import os
import sqlite3
import threading
from typing import Optional

PREFS_FILE_NAME = "ui_prefs.sqlite"
_lock = threading.Lock()


def resolve_prefs_path(base_dir: str) -> str:
    """Caminho do banco de preferências dentro do diretório informado."""
    return os.path.join(base_dir or ".", PREFS_FILE_NAME)


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10.0)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ui_preferences ("
        "  key TEXT PRIMARY KEY,"
        "  value TEXT NOT NULL,"
        "  updated_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    return conn


def get_preference(path: str, key: str, default: Optional[str] = None) -> Optional[str]:
    """Lê uma preferência. Devolve o padrão quando o banco não existe ou falha."""
    if not path:
        return default
    try:
        with _lock:
            conn = _connect(path)
            try:
                row = conn.execute(
                    "SELECT value FROM ui_preferences WHERE key = ?", (key,)
                ).fetchone()
            finally:
                conn.close()
        return row[0] if row else default
    except sqlite3.Error:
        return default


def set_preference(path: str, key: str, value: str) -> bool:
    """Grava uma preferência. Devolve False quando o disco não permite escrita."""
    if not path:
        return False
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with _lock:
            conn = _connect(path)
            try:
                conn.execute(
                    "INSERT INTO ui_preferences (key, value, updated_at) "
                    "VALUES (?, ?, datetime('now')) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                    "updated_at = excluded.updated_at",
                    (key, str(value)),
                )
                conn.commit()
            finally:
                conn.close()
        return True
    except (sqlite3.Error, OSError):
        return False
