"""Cliente da API administrativa do LiteLLM.

Por que API e não banco: o LiteLLM guarda o estado em Postgres via Prisma, e o
schema muda entre versões — `LiteLLM_VerificationToken`, `LiteLLM_TeamTable`,
`LiteLLM_CredentialsTable` e `LiteLLM_ProxyModelTable` já mudaram de forma mais
de uma vez. Escrever direto na tabela ignoraria os invariantes que o proxy
aplica e quebraria em cada atualização. A API administrativa é o contrato
público, e é ela que este sincronizador usa.

É também a diferença estrutural para os projetos irmãos: o 9RTKSync e o
OminiRTkSync leem um SQLite no disco, porque o gateway deles é o dono daquele
arquivo. Aqui não há arquivo para ler.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_TIMEOUT_SECONDS = 10.0
USER_AGENT = "LiteLlmRTKSync/1.0"


class LiteLLMError(RuntimeError):
    """Falha ao falar com o proxy. Carrega o status para o chamador decidir."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class LiteLLMClient:
    """Leitura do estado administrativo de um proxy LiteLLM.

    Só faz GET. Este sincronizador relata e valida; quem altera limite, chave ou
    modelo é o operador, pelas telas do próprio LiteLLM.
    """

    def __init__(
        self,
        base_url: str,
        master_key: str = "",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        opener: Optional[Any] = None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.master_key = master_key or ""
        self.timeout = timeout
        self.opener = opener

    # -- transporte ---------------------------------------------------------

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, method="GET")
        request.add_header("User-Agent", USER_AGENT)
        if self.master_key:
            request.add_header("Authorization", f"Bearer {self.master_key}")

        send = self.opener or urllib.request.urlopen
        try:
            with send(request, timeout=self.timeout) as resposta:
                corpo = resposta.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            # 401 aqui quase sempre significa master key errada, e dizer isso
            # poupa o operador de procurar o erro no lugar errado.
            detalhe = "master key recusada" if e.code in (401, 403) else str(e)
            raise LiteLLMError(f"{path}: {detalhe}", status=e.code) from e
        except (urllib.error.URLError, OSError) as e:
            raise LiteLLMError(f"{path}: proxy inacessível ({e})") from e

        if not corpo.strip():
            return {}
        try:
            return json.loads(corpo)
        except ValueError as e:
            raise LiteLLMError(f"{path}: resposta não é JSON ({e})") from e

    # -- leitura ------------------------------------------------------------

    def health(self) -> bool:
        """Se o proxy responde. Não exige credencial."""
        try:
            self._get("/health/liveliness")
            return True
        except LiteLLMError:
            return False

    def list_keys(self, page_size: int = 100) -> List[Dict[str, Any]]:
        """Chaves virtuais. Pagina até o fim, porque o padrão da API é parcial."""
        chaves: List[Dict[str, Any]] = []
        pagina = 1
        while True:
            dados = self._get(
                "/key/list",
                {"page": pagina, "size": page_size, "return_full_object": "true"},
            )
            lote = dados.get("keys") if isinstance(dados, dict) else None
            if not lote:
                break
            # Com return_full_object a API devolve objetos; sem ele, strings.
            chaves.extend(k for k in lote if isinstance(k, dict))
            total_paginas = (dados or {}).get("total_pages") or 1
            if pagina >= int(total_paginas):
                break
            pagina += 1
        return chaves

    def list_teams(self) -> List[Dict[str, Any]]:
        dados = self._get("/team/list")
        if isinstance(dados, list):
            return [t for t in dados if isinstance(t, dict)]
        return [t for t in (dados or {}).get("teams", []) if isinstance(t, dict)]

    def list_models(self) -> List[Dict[str, Any]]:
        """Modelos cadastrados, com `litellm_params` (onde mora a chave real).

        Uma instalação sem modelo nenhum é estado normal — e o LiteLLM responde
        **500** a esta rota nesse caso, não 200 com lista vazia. Tratar isso
        como falha derrubaria o ciclo inteiro de uma instalação recém-criada,
        então a ausência de modelos vira exatamente o que é: nenhum modelo.
        """
        try:
            dados = self._get("/model/info")
        except LiteLLMError as e:
            if e.status in (404, 500):
                return []
            raise
        return [m for m in (dados or {}).get("data", []) if isinstance(m, dict)]

    def list_credentials(self) -> List[Dict[str, Any]]:
        """Credenciais nomeadas e reutilizáveis (LiteLLM_CredentialsTable)."""
        try:
            dados = self._get("/credentials")
        except LiteLLMError as e:
            # Versões anteriores à tabela de credenciais não expõem a rota.
            if e.status == 404:
                return []
            raise
        return [c for c in (dados or {}).get("credentials", []) if isinstance(c, dict)]
