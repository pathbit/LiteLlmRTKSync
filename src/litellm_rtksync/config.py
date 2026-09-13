"""Configuração do LiteLlmRTKSync, toda por variável de ambiente."""

import os
from dataclasses import dataclass, field
from typing import Optional, Tuple

from .auth import (
    AUTH_PASSWORD_KEY,
    AUTH_USER_KEY,
    derive_recovery_hash,
    ensure_recovery_hash,
    hash_password,
    read_db_credentials,
    resolve_recovery_hash,
    validate_password_strength,
    verify_credentials,
    write_db_credentials,
)

RECOVERY_FILE_NAME = ".dashboard_recovery"


def _flag(nome: str, padrao: str = "1") -> bool:
    return os.environ.get(nome, padrao).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    """Tudo que o sincronizador precisa saber, e nada que ele deva adivinhar."""

    # -- proxy --------------------------------------------------------------
    # O default é o nome de serviço do compose deste repositório, e não um nome
    # curto genérico: sem LITELLM_URL definida este valor chega à tela, no cartão
    # de liveness, e um endereço que não resolve em rede nenhuma ensina errado
    # quem só olhou o painel.
    litellm_url: str = "http://litellmrtk-router:4000"
    master_key: str = ""

    # -- ciclo --------------------------------------------------------------
    sync_interval: int = 300
    refresh_margin: int = 900
    cron_enabled: bool = True
    cron_interval: int = 300

    # -- teto da plataforma -------------------------------------------------
    # Nenhum time e nenhuma chave pode declarar limite acima destes. Vazio
    # significa "sem teto declarado", e o painel relata isso como escolha, nao
    # como erro.
    platform_rpm_limit: Optional[int] = None
    platform_tpm_limit: Optional[int] = None
    platform_max_budget: Optional[float] = None

    # -- painel -------------------------------------------------------------
    enable_web: bool = True
    web_host: str = "0.0.0.0"
    web_port: int = 9090
    dashboard_user: str = "admin"
    # Sem senha de fabrica: um valor estatico e, por definicao, uma credencial
    # publica. O primeiro acesso usa a credencial de recuperacao, gerada no
    # primeiro boot e gravada com modo 0600.
    dashboard_password: str = ""
    dashboard_auth_from_env: bool = False
    recovery_hash: str = ""

    # -- validacao de credencial -------------------------------------------
    validate_credentials: bool = True
    validation_timeout: float = 8.0

    # -- armazenamento ------------------------------------------------------
    data_dir: str = "/app/data"

    @classmethod
    def from_env(cls) -> "Settings":
        # Quem manda no modo "credencial gerida pelo ambiente" e a SENHA, nunca
        # o nome de usuario: nome sozinho nao e credencial. O compose de exemplo
        # define DASHBOARD_USER e deixa DASHBOARD_PASSWORD vazia, e tratar isso
        # como autoritativo travaria a definicao de senha pela tela para sempre.
        env_user = os.environ.get("DASHBOARD_USER")
        env_pass = os.environ.get("DASHBOARD_PASSWORD")
        senha = env_pass or ""

        return cls(
            litellm_url=os.environ.get("LITELLM_URL", "http://litellmrtk-router:4000").rstrip("/"),
            master_key=os.environ.get("LITELLM_MASTER_KEY", ""),
            sync_interval=int(os.environ.get("SYNC_INTERVAL", "300")),
            refresh_margin=int(os.environ.get("REFRESH_MARGIN", "900")),
            cron_enabled=_flag("CRON_ENABLED"),
            cron_interval=int(os.environ.get("CRON_INTERVAL", os.environ.get("SYNC_INTERVAL", "300"))),
            platform_rpm_limit=_inteiro_opcional("PLATFORM_RPM_LIMIT"),
            platform_tpm_limit=_inteiro_opcional("PLATFORM_TPM_LIMIT"),
            platform_max_budget=_decimal_opcional("PLATFORM_MAX_BUDGET"),
            enable_web=_flag("ENABLE_WEB_DASHBOARD"),
            web_host=os.environ.get("WEB_HOST", "0.0.0.0"),
            web_port=int(os.environ.get("WEB_PORT", "9090")),
            dashboard_user=env_user or "admin",
            dashboard_password=senha,
            dashboard_auth_from_env=bool(senha),
            recovery_hash=os.environ.get("DASHBOARD_RECOVERY_HASH", ""),
            validate_credentials=_flag("CREDENTIAL_CHECK_ENABLED"),
            validation_timeout=float(os.environ.get("CREDENTIAL_CHECK_TIMEOUT", "8")),
            data_dir=os.environ.get("DATA_DIR", "/app/data"),
        )

    # -- caminhos -----------------------------------------------------------

    def get_data_dir(self) -> str:
        """Diretorio de tudo que este processo escreve, CRIADO se faltar.

        Cair para $HOME quando o diretorio nao existe gravaria o banco de
        preferencias -- com a senha do painel dentro -- fora do volume de dados,
        e a senha sumiria ao recriar o container.
        """
        base = self.data_dir
        if base:
            try:
                os.makedirs(base, exist_ok=True)
            except OSError:
                base = ""
        if not base or not os.path.isdir(base):
            base = os.path.expanduser("~")
        return base

    def get_prefs_path(self) -> str:
        from .prefs import resolve_prefs_path

        return resolve_prefs_path(self.get_data_dir())

    def get_recovery_file_path(self) -> str:
        return os.path.join(self.get_data_dir(), RECOVERY_FILE_NAME)

    def get_sso_secret_path(self) -> str:
        """Segredo do cliente OIDC, no MESMO diretorio da credencial de
        recuperacao e com a mesma permissao 0600.

        Este projeto nao tem SQLite de gateway -- os irmaos derivam o diretorio
        do banco deles. Aqui a unica fonte e DATA_DIR, que e o volume onde tudo
        que este processo escreve ja mora.
        """
        from .sso import ARQUIVO_DO_SEGREDO

        return os.path.join(self.get_data_dir(), ARQUIVO_DO_SEGREDO)

    def get_log_dir(self) -> str:
        return os.environ.get("LOG_DIR") or os.path.join(self.get_data_dir(), "logs")

    # -- credenciais --------------------------------------------------------

    def ensure_recovery_hash(self) -> Tuple[str, bool]:
        if self.recovery_hash:
            return self.recovery_hash, False
        valor, gerado = ensure_recovery_hash(self.get_recovery_file_path())
        self.recovery_hash = valor
        return valor, gerado

    def has_stored_password(self) -> bool:
        if self.dashboard_auth_from_env:
            return True
        return read_db_credentials(self.get_prefs_path()) is not None

    def is_default_password(self) -> bool:
        """O aviso de seguranca aparece enquanto nao houver senha gravada."""
        return not self.has_stored_password()

    def check_password_strength(self, senha: str):
        return validate_password_strength(senha)

    def update_auth_credentials(self, usuario: str, senha: str) -> bool:
        if self.dashboard_auth_from_env:
            return False
        if self.check_password_strength(senha):
            return False
        usuario_final = (usuario or self.dashboard_user or "admin").strip() or "admin"
        if not write_db_credentials(self.get_prefs_path(), usuario_final, senha):
            return False
        self.dashboard_user = usuario_final
        self.dashboard_password = senha
        return True

    def verify_credentials(self, usuario: str, senha: str) -> bool:
        # A credencial gravada no banco de preferências vence; o ambiente só
        # entra quando é ele quem manda; e a de recuperação vale sempre, que é
        # o ponto de ela existir.
        gravada = None if self.dashboard_auth_from_env else read_db_credentials(self.get_prefs_path())
        return verify_credentials(
            usuario,
            senha,
            stored=gravada,
            factory_user=self.dashboard_user if self.dashboard_auth_from_env else "",
            factory_password=self.dashboard_password if self.dashboard_auth_from_env else "",
            recovery_hash=self.recovery_hash or resolve_recovery_hash(self.get_recovery_file_path()),
        )

    def platform_caps(self) -> dict:
        """Teto da plataforma no formato que o avaliador de limites consome."""
        return {
            "rpm_limit": self.platform_rpm_limit,
            "tpm_limit": self.platform_tpm_limit,
            "max_budget": self.platform_max_budget,
        }


def _inteiro_opcional(nome: str) -> Optional[int]:
    valor = os.environ.get(nome, "").strip()
    if not valor:
        return None
    try:
        return int(valor)
    except ValueError:
        return None


def _decimal_opcional(nome: str) -> Optional[float]:
    valor = os.environ.get(nome, "").strip()
    if not valor:
        return None
    try:
        return float(valor)
    except ValueError:
        return None
