"""A bancada de teste e o veredito que vem do próprio gateway.

Dois assuntos, um arquivo, porque são as duas metades do mesmo problema: o
painel mostrava "Expiry unknown", "None declared" e "Not checked" porque o outro
lado não tinha dado — e, no caso do último, porque dado nenhum resolveria.

  * `tools/popula_bancada.py` cria o dado que faltava, e precisa ser idempotente
    e cirúrgico: rodar duas vezes não pode duplicar, e `--limpar` não pode tocar
    no que outra pessoa criou;
  * `engine._verificar_modelos` deixou de responder "não verificada" para todo
    modelo. O `/model/info` do LiteLLM **remove** `api_key` da resposta, então a
    validação local nunca tinha o que validar; agora o veredito vem do `/health`
    do próprio proxy, que é quem tem o segredo.

Sem container e sem rede: a API é mockada por um `opener` injetado, o mesmo
padrão de `test_client.py`.
"""

import contextlib
import io
import json
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
import urllib.error

from litellm_rtksync.gateway import GatewayClient
from litellm_rtksync.gateway import SyncEngine, _classificar_erro_do_gateway
from litellm_rtksync.config import Settings
from litellm_rtksync.models import RegisteredModelRecord

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))

from popula_bancada import (  # noqa: E402
    BENCH_PREFIX,
    DEMO_LEGADO,
    KEYS,
    PROVIDERS,
    TEAMS,
    AdminAPI,
    ephemeral_duration,
    garantir_chaves,
    garantir_credenciais_e_modelos,
    garantir_times,
    limpar,
    needs_rearm,
    refresh_margin,
    valor_da_credencial,
)


class RespostaFalsa(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def expires_de(duration):
    """Traduz a `duration` do LiteLLM ("14m", "720h", "1s") em instante ISO.

    Sem `duration` o proxy real grava `expires = null`; o fake copia isso, que é
    o caso que faz o painel dizer "validade não declarada".
    """
    if not duration:
        return None
    unidades = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    fator = unidades.get(str(duration)[-1])
    if fator is None:
        return None
    segundos = int(str(duration)[:-1]) * fator
    return (datetime.now(timezone.utc) + timedelta(seconds=segundos)).isoformat()


class ProxyFalso:
    """Um LiteLLM de mentira com o mínimo de memória para provar idempotência.

    Guarda o que foi criado e devolve nas listagens seguintes — sem isso, o
    teste de "rodar duas vezes não duplica" não teria como falhar.
    """

    def __init__(self):
        self.teams = []
        self.keys = []
        self.models = []
        self.credentials = []
        self.escritas = []

    def opener(self, request, timeout=None):
        caminho = "/" + request.full_url.split("://", 1)[1].split("/", 1)[1]
        corpo = json.loads(request.data.decode()) if request.data else None
        metodo = request.get_method()
        if metodo != "GET":
            self.escritas.append((metodo, caminho.split("?")[0], corpo))
        return RespostaFalsa(json.dumps(self._responder(metodo, caminho, corpo)).encode())

    def _responder(self, metodo, caminho, corpo):
        if caminho.startswith("/team/list"):
            return {"teams": self.teams}
        if caminho.startswith("/key/list"):
            return {"keys": self.keys, "total_pages": 1}
        if caminho.startswith("/model/info"):
            return {"data": self.models}
        if caminho.startswith("/credentials") and metodo == "GET":
            return {"credentials": self.credentials}

        if caminho == "/team/new":
            ident = f"id-{corpo['team_alias']}"
            self.teams.append({**corpo, "team_id": ident})
            return {"team_id": ident}
        if caminho == "/key/generate":
            # `expires` calculado a partir de `duration`, como o proxy real faz.
            # Devolver validade fixa aqui esconderia justamente o que a bancada
            # precisa provar: que a chave efêmera nasce dentro da margem.
            self.keys.append({**corpo, "expires": expires_de(corpo.get("duration"))})
            return {"key": "sk-nao-usado"}
        if caminho == "/credentials":
            self.credentials.append(corpo)
            return {}
        if caminho == "/model/new":
            self.models.append({
                "model_name": corpo["model_name"],
                "litellm_params": corpo["litellm_params"],
                "model_info": corpo.get("model_info") or {},
            })
            return {}
        if caminho == "/model/delete":
            self.models = [m for m in self.models
                           if (m.get("model_info") or {}).get("id") != corpo["id"]]
            return {}
        if caminho == "/key/delete":
            self.keys = [k for k in self.keys if k["key_alias"] not in corpo["key_aliases"]]
            return {}
        if caminho == "/team/delete":
            self.teams = [t for t in self.teams if t["team_id"] not in corpo["team_ids"]]
            return {}
        if caminho.startswith("/credentials/"):
            nome = caminho.rsplit("/", 1)[1]
            self.credentials = [c for c in self.credentials if c["credential_name"] != nome]
            return {}
        raise urllib.error.HTTPError(caminho, 404, "nao encontrado", {}, None)


def popular(proxy, api):
    """Roda a bancada inteira contra o proxy falso.

    A saída do script é engolida de propósito: ela é feita para o operador, e
    despejá-la no meio da suíte esconde o resultado dos testes.
    """
    with contextlib.redirect_stdout(io.StringIO()):
        times = garantir_times(api)
        garantir_chaves(api, times)
        garantir_credenciais_e_modelos(api, referencia_de_ambiente=False)


class TestBancadaIdempotente(unittest.TestCase):
    def setUp(self):
        self.proxy = ProxyFalso()
        self.api = AdminAPI("http://proxy:4000", "mk", opener=self.proxy.opener)
        # A bancada só cria modelo para o provedor cuja chave está no ambiente.
        self._env_original = {p["env_var"]: os.environ.get(p["env_var"]) for p in PROVIDERS}
        for p in PROVIDERS:
            os.environ[p["env_var"]] = "chave-de-teste"

    def tearDown(self):
        for nome, valor in self._env_original.items():
            if valor is None:
                os.environ.pop(nome, None)
            else:
                os.environ[nome] = valor

    def test_running_twice_creates_nothing_the_second_time(self):
        popular(self.proxy, self.api)
        primeira = len(self.proxy.escritas)
        self.assertGreater(primeira, 0, "a primeira execução precisa criar alguma coisa")

        popular(self.proxy, self.api)
        self.assertEqual(
            len(self.proxy.escritas), primeira,
            "a segunda execução escreveu de novo: a bancada não é idempotente",
        )
        self.assertEqual(len(self.proxy.teams), len(TEAMS))
        self.assertEqual(len(self.proxy.keys), len(KEYS))
        self.assertEqual(len(self.proxy.models), len(PROVIDERS))

    def test_the_ephemeral_key_is_rearmed_once_it_leaves_the_margin(self):
        """A única célula da bancada que decai sozinha precisa se recuperar.

        `bancada-chave-renovando` dura 14 min contra uma margem de 15: passado
        esse tempo ela vira "Expired" e a tela perde o estado intermediário.
        Idempotência por alias sozinha pularia a chave para sempre, e a bancada
        ficaria muda exatamente onde deveria falar.
        """
        popular(self.proxy, self.api)
        efemeras = [d["key_alias"] for d in KEYS if d.get("ephemeral")]
        self.assertTrue(efemeras, "a bancada precisa de ao menos uma chave efêmera")

        # Envelhece a chave efêmera no proxy de mentira, como o relógio faria.
        vencida = datetime.now(timezone.utc) - timedelta(minutes=1)
        for k in self.proxy.keys:
            if k.get("key_alias") in efemeras:
                k["expires"] = vencida.isoformat()

        antes = len(self.proxy.escritas)
        popular(self.proxy, self.api)
        novas = self.proxy.escritas[antes:]

        self.assertIn(("POST", "/key/delete", {"key_aliases": efemeras[:1]}), novas,
                      "a chave vencida não foi removida para rearmar")
        regeradas = [c["key_alias"] for m, cam, c in novas if cam == "/key/generate"]
        self.assertEqual(regeradas, efemeras, "a chave efêmera não foi recriada")
        self.assertEqual(len(self.proxy.keys), len(KEYS), "rearmar duplicou a chave")
        # E a chave nova nasce de novo dentro da janela.
        viva = next(k for k in self.proxy.keys if k["key_alias"] in efemeras)
        self.assertFalse(needs_rearm(viva), "a chave rearmada já nasceu fora da margem")

    def test_a_healthy_ephemeral_key_is_left_alone(self):
        """Rearmar é para chave decaída; mexer numa chave boa é escrita à toa.

        Roda em várias margens de propósito: com a duração escrita à mão a
        bancada deixava de ser idempotente para qualquer `REFRESH_MARGIN` menor
        que ela — a chave nascia fora da janela e era recriada toda execução.
        """
        for margem in (None, "600", "120", "3600"):
            with self.subTest(REFRESH_MARGIN=margem):
                anterior = os.environ.get("REFRESH_MARGIN")
                if margem is None:
                    os.environ.pop("REFRESH_MARGIN", None)
                else:
                    os.environ["REFRESH_MARGIN"] = margem
                try:
                    self.proxy = ProxyFalso()
                    self.api = AdminAPI("http://proxy:4000", "mk", opener=self.proxy.opener)
                    popular(self.proxy, self.api)
                    antes = len(self.proxy.escritas)
                    popular(self.proxy, self.api)
                    self.assertEqual(
                        len(self.proxy.escritas), antes,
                        "rearmou uma chave que ainda estava dentro da margem",
                    )
                finally:
                    if anterior is None:
                        os.environ.pop("REFRESH_MARGIN", None)
                    else:
                        os.environ["REFRESH_MARGIN"] = anterior

    def test_the_ephemeral_key_is_born_inside_the_current_margin(self):
        # A duração sai da margem vigente; fixá-la em "14m" mentiria sobre a
        # janela assim que alguém baixasse REFRESH_MARGIN.
        for margem, esperado in ((900, "840s"), (600, "540s"), (120, "60s"), (30, "60s")):
            with self.subTest(margem=margem):
                self.assertEqual(ephemeral_duration(margem), esperado)
                segundos = int(ephemeral_duration(margem)[:-1])
                self.assertLessEqual(segundos, max(margem, 60))

    def test_the_margin_is_read_at_call_time_not_at_import(self):
        # O .env do repositório só entra no ambiente depois do import; uma
        # constante de módulo faria o script e o container discordarem calados.
        anterior = os.environ.get("REFRESH_MARGIN")
        os.environ["REFRESH_MARGIN"] = "1800"
        try:
            self.assertEqual(refresh_margin(), 1800)
        finally:
            if anterior is None:
                os.environ.pop("REFRESH_MARGIN", None)
            else:
                os.environ["REFRESH_MARGIN"] = anterior

    def test_a_broken_margin_falls_back_instead_of_crashing(self):
        anterior = os.environ.get("REFRESH_MARGIN")
        try:
            for ruim in ("", "abc", "0", "-5"):
                with self.subTest(valor=ruim):
                    os.environ["REFRESH_MARGIN"] = ruim
                    self.assertEqual(refresh_margin(), 900)
        finally:
            if anterior is None:
                os.environ.pop("REFRESH_MARGIN", None)
            else:
                os.environ["REFRESH_MARGIN"] = anterior

    def test_a_key_with_no_expiry_at_all_counts_as_stale(self):
        # `expires = null` é validade não declarada — é o estado que a bancada
        # existe para tirar da tela, nunca um estado aceitável para ela mesma.
        self.assertTrue(needs_rearm({"expires": None}))
        self.assertTrue(needs_rearm({}))

    def test_an_expiry_written_as_a_numeric_epoch_is_still_understood(self):
        # A classe de bug que originou estes projetos: epoch gravado como texto.
        futuro = datetime.now(timezone.utc) + timedelta(minutes=10)
        epoch = int(futuro.timestamp())
        self.assertFalse(needs_rearm({"expires": epoch}))
        self.assertFalse(needs_rearm({"expires": str(epoch)}))
        self.assertFalse(needs_rearm({"expires": epoch * 1000}))

    def test_every_key_is_created_with_an_expiry(self):
        # É a razão de existir da bancada: sem `duration`, o LiteLLM grava
        # `expires = null` e a tela não tem validade nenhuma para mostrar.
        popular(self.proxy, self.api)
        criadas = [c for m, cam, c in self.proxy.escritas if cam == "/key/generate"]
        self.assertEqual(len(criadas), len(KEYS))
        for chave in criadas:
            self.assertTrue(chave.get("duration"),
                            f"chave '{chave.get('key_alias')}' criada sem duration")

    def test_every_model_is_bound_to_a_named_credential(self):
        # Chave declarada direto no modelo é removida da resposta do
        # `/model/info`; só a credencial nomeada sobrevive e dá procedência.
        popular(self.proxy, self.api)
        for modelo in self.proxy.models:
            self.assertTrue(
                modelo["litellm_params"].get("litellm_credential_name"),
                f"modelo '{modelo['model_name']}' sem credencial nomeada",
            )

    def test_no_secret_is_written_in_the_script_file(self):
        # O valor da credencial vem do ambiente; nenhuma chave literal pode
        # estar no arquivo, que é versionado. Por forma, não por lista: uma
        # lista de segredos conhecidos só pega o segredo que alguém já vazou.
        caminho = os.path.join(RAIZ, "tools", "popula_bancada.py")
        with open(caminho, encoding="utf-8") as f:
            fonte = f.read()
        formas_de_chave = re.compile(
            r"\b(sk-[A-Za-z0-9_\-]{12,}"       # OpenAI, LiteLLM virtual key
            r"|gsk_[A-Za-z0-9_\-]{20,}"        # Groq
            r"|AIza[A-Za-z0-9_\-]{30,}"        # Google
            r"|xai-[A-Za-z0-9_\-]{20,}"        # xAI
            r"|[0-9a-f]{48,})\b"               # hex longo: salt, token, hash
        )
        achado = formas_de_chave.search(fonte)
        self.assertIsNone(
            achado,
            f"o script da bancada tem algo com forma de segredo: {achado.group()[:12] if achado else ''}…",
        )
        # E a leitura tem de ser do ambiente, nomeando a variável — nunca um
        # valor embutido.
        for provedor in PROVIDERS:
            self.assertIn(provedor["env_var"], fonte)

    def test_the_credential_value_comes_from_the_environment(self):
        os.environ["GROQ_API_KEY"] = "valor-vindo-do-ambiente"
        self.assertEqual(valor_da_credencial("GROQ_API_KEY", False), "valor-vindo-do-ambiente")
        self.assertEqual(valor_da_credencial("GROQ_API_KEY", True), "os.environ/GROQ_API_KEY")

    def test_cleanup_only_touches_the_bench_prefix(self):
        popular(self.proxy, self.api)
        # Um objeto de outra pessoa, sem o prefixo, no meio do caminho.
        self.proxy.keys.append({"key_alias": "chave-do-operador", "expires": None})
        self.proxy.teams.append({"team_alias": "time-do-operador", "team_id": "id-alheio"})
        self.proxy.models.append({"model_name": "modelo-do-operador",
                                  "litellm_params": {}, "model_info": {"id": "id-alheio"}})

        with contextlib.redirect_stdout(io.StringIO()):
            limpar(self.api)

        self.assertEqual([k["key_alias"] for k in self.proxy.keys], ["chave-do-operador"])
        self.assertEqual([t["team_alias"] for t in self.proxy.teams], ["time-do-operador"])
        self.assertEqual([m["model_name"] for m in self.proxy.models], ["modelo-do-operador"])

    def test_the_legacy_demo_is_named_not_guessed(self):
        # A aposentadoria do demo feito à mão apaga objetos que este script não
        # criou; por isso ela é por nome, nunca por regra ou prefixo.
        for nome in DEMO_LEGADO["key_aliases"] + DEMO_LEGADO["model_names"]:
            self.assertFalse(nome.startswith(BENCH_PREFIX))


class TestVereditoDoGateway(unittest.TestCase):
    """O `/health` do proxy no lugar de uma sonda que nunca teve o que sondar."""

    def _engine(self, saude, modelos):
        def opener(request, timeout=None):
            caminho = "/" + request.full_url.split("://", 1)[1].split("/", 1)[1]
            if caminho.startswith("/health"):
                return RespostaFalsa(json.dumps(saude).encode())
            raise urllib.error.HTTPError(caminho, 404, "nao encontrado", {}, None)

        settings = Settings(litellm_url="http://proxy:4000", master_key="mk",
                            validate_credentials=True, validation_timeout=2.0)
        motor = SyncEngine(settings)
        motor.client = GatewayClient("http://proxy:4000", "mk", opener=opener)
        resumo = {"details": [], "invalid_credentials": 0}
        motor._verificar_modelos([RegisteredModelRecord(m) for m in modelos], resumo)
        return resumo

    @staticmethod
    def _modelo(nome, ident):
        # Exatamente como o LiteLLM responde: sem `api_key`, porque ele faz
        # `pop("api_key", None)` antes de devolver o cadastro.
        return {"model_name": nome, "model_info": {"id": ident},
                "litellm_params": {"model": "groq/openai/gpt-oss-120b"}}

    def test_a_healthy_endpoint_becomes_an_accepted_credential(self):
        resumo = self._engine(
            {"healthy_endpoints": [{"model_id": "m1"}], "unhealthy_endpoints": []},
            [self._modelo("groq-oss", "m1")],
        )
        self.assertEqual(resumo["details"][0]["status"], "valid")
        self.assertEqual(resumo["invalid_credentials"], 0)

    def test_an_authentication_error_becomes_a_rejected_credential(self):
        resumo = self._engine(
            {"healthy_endpoints": [],
             "unhealthy_endpoints": [{"model_id": "m1",
                                      "error": 'GroqException - {"code":"invalid_api_key"}'}]},
            [self._modelo("groq-oss", "m1")],
        )
        self.assertEqual(resumo["details"][0]["status"], "invalid")
        self.assertEqual(resumo["invalid_credentials"], 1)

    def test_a_model_the_gateway_did_not_judge_stays_not_checked(self):
        # Sem veredito de ninguém, inventar um seria pior que admitir a lacuna.
        resumo = self._engine(
            {"healthy_endpoints": [{"model_id": "outro"}], "unhealthy_endpoints": []},
            [self._modelo("groq-oss", "m1")],
        )
        self.assertEqual(resumo["details"][0]["status"], "not_checked")

    def test_the_verdict_is_matched_by_model_id_not_by_name(self):
        # Dois deployments podem ter o mesmo `model_name`; o `/health` só
        # identifica por `model_id`, e casar por nome trocaria os vereditos.
        resumo = self._engine(
            {"healthy_endpoints": [{"model_id": "m1"}],
             "unhealthy_endpoints": [{"model_id": "m2", "error": "AuthenticationError"}]},
            [self._modelo("mesmo-nome", "m1"), self._modelo("mesmo-nome", "m2")],
        )
        estados = [d["status"] for d in resumo["details"]]
        self.assertEqual(estados, ["valid", "invalid"])

    def test_an_unrelated_failure_is_not_called_an_invalid_credential(self):
        # Marcar de inválida uma chave boa manda o operador trocar a credencial
        # errada; na dúvida, o estado é desconhecido.
        resumo = self._engine(
            {"healthy_endpoints": [],
             "unhealthy_endpoints": [{"model_id": "m1", "error": "ModelNotFoundError"}]},
            [self._modelo("groq-oss", "m1")],
        )
        self.assertEqual(resumo["details"][0]["status"], "unknown")
        self.assertEqual(resumo["invalid_credentials"], 0)


class TestClassificacaoDoErroDoGateway(unittest.TestCase):
    """O `/health` devolve o traceback do proxy junto; a tela recebe uma linha."""

    def test_the_error_states_are_read_conservatively(self):
        casos = [
            ("litellm.AuthenticationError: MistralException", "invalid"),
            ('GroqException - {"code":"invalid_api_key"}', "invalid"),
            ("GeminiException - API key not valid.", "invalid"),
            ("litellm.RateLimitError: 429 too many requests", "rate_limited"),
            ("litellm.APIConnectionError: connection refused", "unreachable"),
            ("litellm.Timeout: request timed out", "unreachable"),
            ("algo completamente diferente", "unknown"),
            (None, "unknown"),
        ]
        for erro, esperado in casos:
            with self.subTest(erro=erro):
                self.assertEqual(_classificar_erro_do_gateway(erro)["state"], esperado)

    def test_a_line_number_in_the_traceback_is_not_read_as_an_http_status(self):
        # O `/health` cola o traceback do proxy depois da mensagem. Um rastro
        # que passe por `line 401` ou `line 429` não pode virar "chave
        # recusada" nem "rate limit" — seria mandar trocar credencial boa.
        erro = (
            "litellm.NotFoundError: ModelNotFoundError\n\n"
            'stack trace: Traceback (most recent call last):\n'
            '  File "/app/.venv/lib/python3.13/site-packages/litellm/main.py", line 401, in completion\n'
            '  File "/app/.venv/lib/python3.13/site-packages/litellm/router.py", line 429, in acompletion\n'
        )
        self.assertEqual(_classificar_erro_do_gateway(erro)["state"], "unknown")

    def test_only_the_first_line_of_the_stack_trace_reaches_the_screen(self):
        erro = "litellm.AuthenticationError: chave recusada\n\nstack trace:\n  File ...\n  File ..."
        detalhe = _classificar_erro_do_gateway(erro)["detail"]
        self.assertNotIn("\n", detalhe)
        self.assertIn("chave recusada", detalhe)


if __name__ == "__main__":
    unittest.main()
