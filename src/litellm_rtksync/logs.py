"""Log persistente em arquivo com rotação diária e expurgo por idade.

O stdout/stderr de um container é volátil: ele some no `docker rm`, é truncado pelo
driver de log e não sobrevive a um restart. Os eventos que importam para auditoria
(renovação de token, falha de sincronização, acesso ao dashboard) passam a ser
gravados também em arquivo, com rotação diária e retenção configurável.

Variáveis de ambiente:
    LOG_DIR             Diretório dos arquivos de log. Padrão: <dir do DB>/logs,
                        com fallback para ~/.ominirtksync/logs.
    LOG_RETENTION_DAYS  Dias de retenção antes do expurgo. Padrão: 30.
    LOG_LEVEL           Nível mínimo registrado (DEBUG/INFO/WARNING/ERROR). Padrão: INFO.
    LOG_TO_STDOUT       Espelha no stdout (1=sim, 0=não). Padrão: 1.
"""

import logging
import os
import sys
import threading
import time
from logging.handlers import TimedRotatingFileHandler
from typing import Optional

LOG_FILE_NAME = "ominirtksync.log"
DEFAULT_RETENTION_DAYS = 30

_logger: Optional[logging.Logger] = None
_lock = threading.Lock()


def get_retention_days() -> int:
    """Dias de retenção configurados, com piso de 1 dia."""
    try:
        return max(1, int(os.environ.get("LOG_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))))
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS


def resolve_log_dir(db_path: str = "") -> str:
    """Resolve o diretório de logs a partir do ambiente, do banco ou do home."""
    configured = os.environ.get("LOG_DIR", "").strip()
    if configured:
        return configured

    if db_path:
        candidate = os.path.join(os.path.dirname(db_path), "logs")
        parent = os.path.dirname(candidate)
        if parent and os.path.isdir(parent) and os.access(parent, os.W_OK):
            return candidate

    return os.path.join(os.path.expanduser("~"), ".ominirtksync", "logs")


def purge_expired_logs(log_dir: str, retention_days: Optional[int] = None) -> int:
    """Remove arquivos de log rotacionados mais velhos que a retenção. Devolve quantos apagou."""
    if not os.path.isdir(log_dir):
        return 0

    days = get_retention_days() if retention_days is None else max(1, retention_days)
    cutoff = time.time() - (days * 86400)
    removed = 0

    for entry in os.listdir(log_dir):
        # Só mexe nos arquivos rotacionados deste serviço; o arquivo ativo é preservado.
        if not entry.startswith(LOG_FILE_NAME) or entry == LOG_FILE_NAME:
            continue
        path = os.path.join(log_dir, entry)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            continue

    return removed


def setup_logging(db_path: str = "") -> logging.Logger:
    """Configura (uma única vez) o logger com arquivo rotativo e espelho opcional no stdout."""
    global _logger
    with _lock:
        if _logger is not None:
            return _logger

        logger = logging.getLogger("ominirtksync")
        logger.setLevel(getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO))
        logger.propagate = False
        logger.handlers.clear()

        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )

        log_dir = resolve_log_dir(db_path)
        try:
            os.makedirs(log_dir, exist_ok=True)
            # backupCount em rotação diária equivale à retenção em dias.
            file_handler = TimedRotatingFileHandler(
                os.path.join(log_dir, LOG_FILE_NAME),
                when="midnight",
                interval=1,
                backupCount=get_retention_days(),
                encoding="utf-8",
                utc=True,
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            purge_expired_logs(log_dir)
        except OSError as e:
            # Sem permissão de escrita o serviço continua: o log em arquivo é um extra,
            # nunca um motivo para o sincronizador não subir.
            print(f"[LOG] Log em arquivo indisponivel em {log_dir}: {e}", file=sys.stderr, flush=True)

        if os.environ.get("LOG_TO_STDOUT", "1") not in ("0", "false", "no"):
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setFormatter(formatter)
            logger.addHandler(stream_handler)

        _logger = logger
        return logger


def get_logger() -> logging.Logger:
    """Devolve o logger configurado, inicializando com os padrões caso necessário."""
    return _logger if _logger is not None else setup_logging()


def reset_logging() -> None:
    """Descarta a configuração atual. Existe para permitir testes isolados."""
    global _logger
    with _lock:
        if _logger is not None:
            for handler in list(_logger.handlers):
                handler.close()
                _logger.removeHandler(handler)
        _logger = None
