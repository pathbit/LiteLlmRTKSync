"""Autenticação do dashboard com credencial de recuperação (break-glass).

Regra de validação, nesta ordem:

1. Se existem credenciais salvas (trocadas pela tela), elas são a fonte de verdade:
   usuário e senha precisam bater exatamente com o que está salvo.
2. Se nada foi salvo ainda, valem as credenciais de fábrica (padrão ou vindas do
   ambiente) — é o estado de primeiro acesso do container.
3. Independente do que existe salvo, o usuário `admin` com a senha igual ao
   *hash de recuperação* sempre entra. É a saída de emergência para quem esqueceu
   a senha, sem precisar apagar o volume do container.
4. Qualquer outra combinação é inválida.

O hash de recuperação vem de DASHBOARD_RECOVERY_HASH. Quando a variável não é
definida, um hash aleatório é gerado no primeiro boot, gravado em disco com
permissão 0600 e registrado uma única vez no log — é lá que o operador vai buscá-lo.

Todas as comparações usam hmac.compare_digest para não vazar informação por tempo
de resposta.
"""

import hashlib
import hmac
import json
import os
import secrets
from typing import Optional, Tuple

RECOVERY_FILE_NAME = ".dashboard_recovery"
RECOVERY_USER = "admin"

# Politica de senha do painel. Exigida sempre que a senha for definida ou
# trocada pela tela; o ambiente headless nao passa por aqui porque quem opera
# DASHBOARD_PASSWORD ja controla o segredo por fora.
MIN_PASSWORD_LENGTH = 6
SPECIAL_CHARACTERS = "!@#$%^&*()-_=+[]{};:,.<>?/\\|`~\"'"


def validate_password_strength(password: str) -> list:
    """Devolve as chaves de traducao das regras que a senha nao cumpre.

    Lista vazia significa senha aceita. Devolver todas as falhas de uma vez
    evita o vaivem de corrigir um requisito por tentativa.
    """
    problems = []
    if len(password or "") < MIN_PASSWORD_LENGTH:
        problems.append("password.too_short")
    if not any(c.isupper() for c in password or ""):
        problems.append("password.needs_upper")
    if not any(c.islower() for c in password or ""):
        problems.append("password.needs_lower")
    if not any(c.isdigit() for c in password or ""):
        problems.append("password.needs_digit")
    if not any(c in SPECIAL_CHARACTERS for c in password or ""):
        problems.append("password.needs_special")
    return problems



def constant_time_equals(a: str, b: str) -> bool:
    """Compara duas strings em tempo constante."""
    return hmac.compare_digest(str(a or "").encode("utf-8"), str(b or "").encode("utf-8"))


def derive_recovery_hash(secret: str) -> str:
    """Deriva o hash de recuperação exibido ao operador a partir de um segredo."""
    return hashlib.sha256(str(secret).encode("utf-8")).hexdigest()


# --- Armazenamento da senha -------------------------------------------------
#
# A senha do painel passa a viver no SQLite do sincronizador como hash PBKDF2,
# nunca em texto puro. O formato carrega os proprios parametros, entao aumentar
# o custo no futuro nao invalida o que ja esta gravado.
PBKDF2_ITERATIONS = 240_000
PBKDF2_PREFIX = "pbkdf2_sha256"


def hash_password(password: str, *, salt: Optional[bytes] = None,
                  iterations: int = PBKDF2_ITERATIONS) -> str:
    """Deriva o hash armazenavel de uma senha."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, iterations)
    return f"{PBKDF2_PREFIX}${iterations}${salt.hex()}${digest.hex()}"


def password_matches(stored: str, candidate: str) -> bool:
    """Compara uma senha com o valor gravado.

    Aceita tambem o texto puro herdado do .dashboard_auth.json antigo, para que
    uma instalacao existente continue entrando enquanto nao troca a senha.
    """
    if not stored:
        return False

    if not stored.startswith(PBKDF2_PREFIX + "$"):
        return constant_time_equals(stored, candidate)

    try:
        _, iterations, salt_hex, digest_hex = stored.split("$", 3)
        expected = hashlib.pbkdf2_hmac(
            "sha256", (candidate or "").encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected.hex(), digest_hex)


def read_stored_credentials(auth_file: str) -> Optional[Tuple[str, str]]:
    """Lê as credenciais gravadas pela tela. Devolve None quando ainda não houve troca."""
    if not auth_file or not os.path.exists(auth_file):
        return None
    try:
        with open(auth_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            # Arquivo sintaticamente valido mas com forma errada -- "[]", um
            # numero, uma string. Sem esta guarda o .get abaixo levantava
            # AttributeError, que subia ate o handler HTTP e trancava para fora
            # tanto a credencial configurada quanto a de recuperacao.
            return None
        user = data.get("user")
        password = data.get("password")
        if user and password:
            return str(user), str(password)
    except (OSError, ValueError):
        # Arquivo ausente, ilegivel ou com JSON quebrado equivale a "sem
        # credencial gravada": quem chama cai para o ambiente ou para a
        # credencial de recuperacao.
        pass
    return None


# Chaves de credencial no banco de preferencias do painel.
AUTH_USER_KEY = "auth.user"
AUTH_PASSWORD_KEY = "auth.password_hash"


def read_db_credentials(prefs_path: str) -> Optional[Tuple[str, str]]:
    """Credenciais gravadas no SQLite do painel, ou None se nunca definidas."""
    from .prefs import get_preference

    user = get_preference(prefs_path, AUTH_USER_KEY)
    stored = get_preference(prefs_path, AUTH_PASSWORD_KEY)
    if user and stored:
        return user, stored
    return None


def write_db_credentials(prefs_path: str, user: str, password: str) -> bool:
    """Grava usuario e hash da senha no SQLite do painel."""
    from .prefs import set_preference

    ok_user = set_preference(prefs_path, AUTH_USER_KEY, user)
    ok_pass = set_preference(prefs_path, AUTH_PASSWORD_KEY, hash_password(password))
    return bool(ok_user and ok_pass)


def resolve_recovery_hash(recovery_file: str) -> str:
    """Obtém o hash de recuperação: ambiente primeiro, senão o gerado/salvo localmente."""
    from_env = os.environ.get("DASHBOARD_RECOVERY_HASH", "").strip()
    if from_env:
        return from_env

    if recovery_file and os.path.exists(recovery_file):
        try:
            with open(recovery_file, "r", encoding="utf-8") as f:
                saved = f.read().strip()
            if saved:
                return saved
        except OSError:
            # Disco cheio, permissao negada, arquivo removido no meio do
            # caminho: tratado como "nao ha credencial de recuperacao", que e
            # exatamente o estado em que a autenticacao normal decide sozinha.
            pass

    return ""


def ensure_recovery_hash(recovery_file: str) -> Tuple[str, bool]:
    """Garante que existe um hash de recuperação. Devolve (hash, foi_gerado_agora)."""
    existing = resolve_recovery_hash(recovery_file)
    if existing:
        return existing, False

    generated = derive_recovery_hash(secrets.token_hex(32))
    if recovery_file:
        try:
            os.makedirs(os.path.dirname(recovery_file) or ".", exist_ok=True)
            # 0600: apenas o dono do processo lê o segredo de emergência.
            fd = os.open(recovery_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(generated)
        except OSError:
            # Sem disco gravável o hash vira efêmero (válido só nesta execução),
            # mas o serviço continua subindo.
            pass
    return generated, True


def verify_credentials(
    user: str,
    password: str,
    *,
    stored: Optional[Tuple[str, str]],
    factory_user: str,
    factory_password: str,
    recovery_hash: str = "",
) -> bool:
    """Aplica a regra de validação descrita no topo do módulo."""
    if not user or not password:
        return False

    # 3. Saída de emergência: admin + hash de recuperação entra sempre.
    if recovery_hash and constant_time_equals(user, RECOVERY_USER):
        if constant_time_equals(password, recovery_hash):
            return True

    if stored is not None:
        # 1. Já houve troca de senha: só as credenciais salvas valem.
        # password_matches entende tanto o hash PBKDF2 gravado no SQLite quanto
        # o texto puro herdado do arquivo antigo.
        stored_user, stored_password = stored
        return constant_time_equals(user, stored_user) and password_matches(
            stored_password, password
        )

    # 2. Primeiro acesso: valem as credenciais de fábrica.
    return constant_time_equals(user, factory_user) and constant_time_equals(
        password, factory_password
    )
