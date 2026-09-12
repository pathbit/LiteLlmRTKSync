"""Live validation of the credentials stored in the gateway.

Until now a connection was reported healthy just for carrying an API key -- the
panel painted every row green without ever asking the provider whether the key
still worked. This module actually calls each provider and reports what came
back.

What counts as a valid credential: the provider accepted the authentication.
A 404 for a missing model or a 400 for an empty body still means the key was
accepted, so only 401/403 (and the idiomatic 400 that Google AI Studio returns
for a bad key) are treated as a rejection.

Endpoint choices are deliberate, and were measured rather than assumed:

- OpenRouter's ``/api/v1/models`` answers 200 with no credential at all, so it
  cannot validate anything. ``/api/v1/key`` answers 401.
- Ollama Cloud's catalog is public for the same reason; a chat completion is
  the cheapest call that requires the key.
- Google AI Studio authenticates with the ``x-goog-api-key`` header and answers
  400 -- not 401 -- for a bad key.
- OAuth access tokens are checked with Google's ``tokeninfo``, which answers 400
  once the token dies.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

DEFAULT_TIMEOUT_SECONDS = 8.0
USER_AGENT = "LiteLlmRTKSync-CredentialCheck/1.0"

# States a probe can conclude. "not_checked" is the absence of a probe.
STATE_VALID = "valid"
STATE_INVALID = "invalid"
STATE_RATE_LIMITED = "rate_limited"
STATE_UNREACHABLE = "unreachable"
STATE_UNSUPPORTED = "unsupported"


@dataclass
class CheckResult:
    """Outcome of a single credential probe."""

    state: str
    detail: str = ""
    http_status: int = 0
    latency_ms: int = 0
    checked_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "credentialState": self.state,
            "credentialDetail": self.detail,
            "credentialHttpStatus": self.http_status,
            "credentialLatencyMs": self.latency_ms,
            "credentialCheckedAt": self.checked_at,
        }


@dataclass
class ProbeSpec:
    """How to ask one provider whether a credential is still accepted."""

    url: str
    auth_header: str = "Authorization"
    auth_template: str = "Bearer {key}"
    method: str = "GET"
    body: Optional[bytes] = None
    content_type: str = ""
    # Statuses that mean "credential rejected" beyond the usual 401/403.
    invalid_statuses: tuple = ()


# Matched by substring against the provider name, longest marker first so
# "openai-compatible-chat-ollama-local" never matches the bare "ollama" entry.
API_KEY_PROBES: Dict[str, ProbeSpec] = {
    "groq": ProbeSpec("https://api.groq.com/openai/v1/models"),
    "mistral": ProbeSpec("https://api.mistral.ai/v1/models"),
    "openai": ProbeSpec("https://api.openai.com/v1/models"),
    "anthropic": ProbeSpec(
        "https://api.anthropic.com/v1/models",
        auth_header="x-api-key",
        auth_template="{key}",
    ),
    "openrouter": ProbeSpec("https://openrouter.ai/api/v1/key"),
    "gemini": ProbeSpec(
        "https://generativelanguage.googleapis.com/v1beta/models",
        auth_header="x-goog-api-key",
        auth_template="{key}",
        invalid_statuses=(400,),
    ),
    "ollama": ProbeSpec(
        "https://ollama.com/v1/chat/completions",
        method="POST",
        body=json.dumps({"model": "gpt-oss:20b", "messages": [], "max_tokens": 1}).encode(),
        content_type="application/json",
    ),
}

GOOGLE_TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _classify(status: int, spec_invalid: tuple = ()) -> str:
    if status in (401, 403) or status in spec_invalid:
        return STATE_INVALID
    if status == 429:
        return STATE_RATE_LIMITED
    # 5xx nao prova nada sobre a credencial: o provedor e que esta com problema.
    # Tratar como valido carimbava "ativo" numa chave que nunca foi verificada.
    if status >= 500:
        return STATE_UNREACHABLE
    return STATE_VALID


def _execute(
    request: urllib.request.Request,
    timeout: float,
    opener: Optional[Callable] = None,
    spec_invalid: tuple = (),
) -> CheckResult:
    """Run one probe and turn whatever happened into a CheckResult."""
    send = opener or urllib.request.urlopen
    started = time.time()
    try:
        with send(request, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            return CheckResult(
                state=_classify(status, spec_invalid),
                detail=f"HTTP {status}",
                http_status=status,
                latency_ms=int((time.time() - started) * 1000),
                checked_at=_now_iso(),
            )
    except urllib.error.HTTPError as e:
        # The provider answered -- that answer is exactly the signal we want.
        state = _classify(e.code, spec_invalid)
        return CheckResult(
            state=state,
            detail=f"HTTP {e.code}",
            http_status=e.code,
            latency_ms=int((time.time() - started) * 1000),
            checked_at=_now_iso(),
        )
    except Exception as e:
        # No answer at all: the credential is unproven, not proven bad.
        return CheckResult(
            state=STATE_UNREACHABLE,
            detail=str(e)[:200],
            latency_ms=int((time.time() - started) * 1000),
            checked_at=_now_iso(),
        )


# Markers that describe a self-hosted endpoint and must never resolve to a
# vendor URL. "openai-compatible-chat-ollama-local" contains both "openai" and
# "ollama"; without this guard it would be probed against api.openai.com.
SELF_HOSTED_MARKERS = ("openai-compatible", "-local", "localai")


def _same_host(a: str, b: str) -> bool:
    """Whether two URLs point at the same host (port included)."""
    try:
        return urllib.parse.urlparse(a).netloc.lower() == urllib.parse.urlparse(b).netloc.lower()
    except Exception:
        return False


def select_probe(provider: str) -> Optional[ProbeSpec]:
    """Pick the probe for a provider name, preferring the most specific marker.

    Returns None when the credential belongs to a self-hosted endpoint or to a
    provider with no known probe; the caller then falls back to base_url.
    """
    name = (provider or "").lower()
    if any(marker in name for marker in SELF_HOSTED_MARKERS):
        return None

    matches = [marker for marker in API_KEY_PROBES if marker in name]
    if not matches:
        return None
    # Sort by length then alphabetically so the choice never depends on dict order.
    return API_KEY_PROBES[sorted(matches, key=lambda m: (-len(m), m))[0]]


def check_api_key(
    provider: str,
    api_key: str,
    base_url: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    opener: Optional[Callable] = None,
) -> CheckResult:
    """Ask the provider whether this API key is still accepted."""
    if not api_key:
        return CheckResult(state=STATE_UNSUPPORTED, detail="No API key", checked_at=_now_iso())

    spec = select_probe(provider)

    # Um endereco declarado na conexao manda mais do que o nome do provedor.
    # "azure-openai" casa com "openai" por substring, e um "anthropic" atras de
    # proxy tambem casa: sem esta checagem a chave do cliente sairia daqui para
    # api.openai.com ou api.anthropic.com, que nao e para onde ela deveria ir.
    if spec is not None and base_url and not _same_host(base_url, spec.url):
        spec = ProbeSpec(base_url.rstrip("/") + "/models")

    if spec is None:
        if not base_url:
            return CheckResult(
                state=STATE_UNSUPPORTED,
                detail=f"No known probe for '{provider}'",
                checked_at=_now_iso(),
            )
        # Unknown provider with a declared address: the OpenAI-compatible
        # catalog is the convention every one of them follows.
        spec = ProbeSpec(base_url.rstrip("/") + "/models")

    request = urllib.request.Request(spec.url, method=spec.method, data=spec.body)
    request.add_header(spec.auth_header, spec.auth_template.format(key=api_key))
    request.add_header("User-Agent", USER_AGENT)
    if spec.content_type:
        request.add_header("Content-Type", spec.content_type)

    return _execute(request, timeout, opener, spec.invalid_statuses)


def check_oauth_token(
    access_token: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    opener: Optional[Callable] = None,
) -> CheckResult:
    """Ask Google whether this access token is still alive.

    Answers the question directly instead of inferring liveness from the stored
    expiry, which is what the panel used to do.
    """
    if not access_token:
        return CheckResult(state=STATE_UNSUPPORTED, detail="No access token", checked_at=_now_iso())

    query = urllib.parse.urlencode({"access_token": access_token})
    request = urllib.request.Request(f"{GOOGLE_TOKENINFO}?{query}")
    request.add_header("User-Agent", USER_AGENT)
    # tokeninfo reports a dead token as 400, not 401.
    return _execute(request, timeout, opener, spec_invalid=(400,))


def check_connection(
    conn: Any,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    opener: Optional[Callable] = None,
) -> CheckResult:
    """Validate whichever credential the connection actually carries."""
    if getattr(conn, "is_local", False):
        # Local instances are proven by their model catalog, not by a cloud API.
        return CheckResult(
            state=STATE_UNSUPPORTED,
            detail="Local instance: validated by model discovery",
            checked_at=_now_iso(),
        )

    if getattr(conn, "is_oauth", False) and getattr(conn, "access_token", None):
        return check_oauth_token(conn.access_token, timeout=timeout, opener=opener)

    if getattr(conn, "has_api_key", False):
        return check_api_key(
            conn.provider,
            conn.api_key or "",
            base_url=getattr(conn, "base_url", None),
            timeout=timeout,
            opener=opener,
        )

    return CheckResult(
        state=STATE_UNSUPPORTED, detail="No credential to validate", checked_at=_now_iso()
    )
