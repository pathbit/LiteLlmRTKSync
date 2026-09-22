"""Superfície do painel: autenticação, CSRF, cabeçalhos e vazamento.

As mesmas regras dos projetos irmãos, porque foram aprendidas do mesmo jeito:

- o corpo do 401 é o que o navegador exibe ao apertar **ESC** no diálogo do
  Basic Auth, então ele não pode ensinar credencial nenhuma;
- os cabeçalhos de segurança valem em **toda** resposta, não só na página
  principal;
- um POST de outra origem chega com o Basic Auth anexado pelo próprio
  navegador, então precisa ser recusado;
- `unlimited` nunca aparece: validade não declarada é validade não declarada.
"""

import base64
import re
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from litellm_rtksync.config import Settings
from litellm_rtksync.models import VirtualKeyRecord
from litellm_rtksync.render import render_keys_table
from litellm_rtksync.web import DashboardHandler, start_web

PORTA = 19390
BASE = f"http://127.0.0.1:{PORTA}"

TOKEN_SECRETO = "sk-SEGREDO-DE-CHAVE-QUE-NAO-PODE-VAZAR"


class NaoSeguirRedirecionamento(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ClienteFalso:
    """Proxy de mentira: o teste é do painel, não da rede."""

    def health(self):
        return True

    def list_keys(self):
        return [
            {"key_alias": "producao", "team_id": "t1", "rpm_limit": 600, "spend": 1.5},
            {"token": TOKEN_SECRETO, "team_id": "t1", "rpm_limit": 10},
        ]

    def list_teams(self):
        return [{"team_id": "t1", "team_alias": "time-restrito", "rpm_limit": 60}]

    def list_models(self):
        return [{"model_name": "gpt-4o", "litellm_params": {"model": "openai/gpt-4o",
                                                            "api_key": TOKEN_SECRETO}}]


class MotorFalso:
    def __init__(self, settings):
        self.settings = settings
        self.client = ClienteFalso()

    def sync_all(self):
        from litellm_rtksync.gateway import SyncEngine

        real = SyncEngine(self.settings, client=self.client)
        return real.sync_all()


class TestPainel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.settings = Settings(
            litellm_url="http://proxy:4000",
            data_dir=cls.tmp.name,
            web_host="127.0.0.1",
            web_port=PORTA,
            validate_credentials=False,
            platform_rpm_limit=1000,
        )
        cls.recuperacao, _ = cls.settings.ensure_recovery_hash()
        cls.servidor = start_web(cls.settings, MotorFalso(cls.settings))
        time.sleep(0.4)

    @classmethod
    def tearDownClass(cls):
        agendador = getattr(cls.servidor, "cron_scheduler", None)
        if agendador is not None:
            agendador.stop()
        cls.servidor.shutdown()
        cls.servidor.server_close()
        DashboardHandler.last_cycle = {}
        DashboardHandler.cron_scheduler = None
        cls.tmp.cleanup()

    # -- auxiliares ---------------------------------------------------------

    def cabecalho_auth(self, cred=None):
        cred = cred or f"admin:{self.recuperacao}"
        return {"Authorization": "Basic " + base64.b64encode(cred.encode()).decode()}

    def pega(self, caminho, cred=None, autenticado=True):
        req = urllib.request.Request(BASE + caminho)
        if autenticado:
            for k, v in self.cabecalho_auth(cred).items():
                req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status, resp.read().decode(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), e.headers

    def posta(self, caminho, corpo=b"", cabecalhos=None):
        req = urllib.request.Request(BASE + caminho, data=corpo, method="POST")
        for k, v in self.cabecalho_auth().items():
            req.add_header(k, v)
        for k, v in (cabecalhos or {}).items():
            req.add_header(k, v)
        abridor = urllib.request.build_opener(NaoSeguirRedirecionamento)
        try:
            with abridor.open(req, timeout=8) as resp:
                return resp.status, resp.headers.get("Location", "")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location", "")

    # -- autenticação -------------------------------------------------------

    def test_the_panel_requires_credentials(self):
        status, _, _ = self.pega("/", autenticado=False)
        self.assertEqual(status, 401)

    def test_the_401_body_never_teaches_a_credential(self):
        """É exatamente o que o navegador mostra ao apertar ESC."""
        _, corpo, _ = self.pega("/", autenticado=False)
        minusculo = corpo.lower()
        for proibido in ("senha padr", "default password", "admin / ", "admin:"):
            self.assertNotIn(proibido, minusculo)
        self.assertNotIn(self.recuperacao, corpo)

    def test_a_wrong_password_is_rejected(self):
        status, _, _ = self.pega("/", cred="admin:errada-x9")
        self.assertEqual(status, 401)

    def test_the_recovery_credential_opens_the_panel(self):
        status, _, _ = self.pega("/")
        self.assertEqual(status, 200)

    def test_healthz_needs_no_credential(self):
        status, _, _ = self.pega("/healthz", autenticado=False)
        self.assertEqual(status, 200)

    def test_the_credentials_updated_page_is_served_before_auth(self):
        """Senão, trocar a senha terminaria num 401 cru: o navegador ainda manda a antiga."""
        status, _, _ = self.pega("/credenciais-atualizadas", autenticado=False)
        self.assertEqual(status, 200)

    # -- cabeçalhos ---------------------------------------------------------

    OBRIGATORIOS = ("Referrer-Policy", "X-Content-Type-Options",
                    "X-Frame-Options", "Content-Security-Policy")

    def test_every_response_carries_the_security_headers(self):
        for caminho, autenticado in (("/", True), ("/", False),
                                     ("/credenciais-atualizadas", False),
                                     ("/api/status", True), ("/api/cron-status", True),
                                     ("/healthz", False)):
            with self.subTest(caminho=caminho, autenticado=autenticado):
                _, _, cabecalhos = self.pega(caminho, autenticado=autenticado)
                for nome in self.OBRIGATORIOS:
                    self.assertIsNotNone(cabecalhos.get(nome), f"{nome} ausente em {caminho}")

    def test_no_header_is_emitted_twice(self):
        _, _, cabecalhos = self.pega("/")
        for nome in self.OBRIGATORIOS:
            self.assertEqual(len(cabecalhos.get_all(nome) or []), 1, f"{nome} duplicado")

    def test_the_policy_allows_only_what_the_page_loads(self):
        _, _, cabecalhos = self.pega("/")
        csp = cabecalhos.get("Content-Security-Policy")
        self.assertIn("https://cdn.jsdelivr.net", csp)
        self.assertIn("https://fonts.gstatic.com", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("form-action 'self'", csp)

    # -- CSRF ---------------------------------------------------------------

    def test_a_cross_origin_post_is_refused_with_a_notice_not_a_bare_403(self):
        status, destino = self.posta(
            "/acoes/atualizar",
            cabecalhos={"Origin": "https://exemplo-malicioso.invalid",
                        "Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(status, 303)
        self.assertIn("tom=danger", destino)

    def test_a_same_origin_post_is_accepted(self):
        status, _ = self.posta(
            "/acoes/atualizar",
            cabecalhos={"Origin": BASE, "Sec-Fetch-Site": "same-origin"},
        )
        self.assertEqual(status, 303)

    def test_a_matching_origin_wins_over_an_unexpected_fetch_site(self):
        """Um valor inesperado de Sec-Fetch-Site não pode recusar POST legítimo."""
        status, destino = self.posta(
            "/acoes/atualizar",
            cabecalhos={"Origin": BASE, "Sec-Fetch-Site": "valor-que-nao-conhecemos"},
        )
        self.assertEqual(status, 303)
        self.assertNotIn("tom=danger", destino)

    # -- política de senha --------------------------------------------------

    def test_a_weak_password_is_refused_listing_every_broken_rule(self):
        status, destino = self.posta(
            "/acoes/credenciais", b"user=admin&password=abc",
            {"Origin": BASE, "Sec-Fetch-Site": "same-origin",
             "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        self.assertIn("tom=danger", destino)

    # -- vazamento ----------------------------------------------------------

    def test_the_page_never_carries_a_provider_key_or_a_token(self):
        _, corpo, _ = self.pega("/")
        self.assertNotIn(TOKEN_SECRETO, corpo)
        self.assertIsNone(re.search(r"sk-[A-Za-z0-9_-]{20}", corpo))

    def test_the_json_endpoint_projects_counters_only(self):
        _, corpo, _ = self.pega("/api/status")
        self.assertNotIn(TOKEN_SECRETO, corpo)
        for proibido in ("token", "api_key", "apiKey", "master"):
            self.assertNotIn(proibido, corpo)

    def test_the_page_never_claims_an_unlimited_validity(self):
        _, corpo, _ = self.pega("/")
        self.assertNotIn("unlimited", corpo.lower())
        self.assertNotIn("ilimitad", corpo.lower())

    def test_the_page_uses_icon_fonts_and_no_emoji(self):
        _, corpo, _ = self.pega("/")
        self.assertIn("bootstrap-icons", corpo)
        emojis = re.findall(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]", corpo)
        self.assertEqual(emojis, [], f"emojis na página: {emojis}")

    # -- ações do agendador -------------------------------------------------

    MESMA_ORIGEM = {"Origin": BASE, "Sec-Fetch-Site": "same-origin"}

    def test_the_panel_serves_the_same_actions_as_its_siblings(self):
        """A barra do topo tem três controles nos três painéis, nesta ordem.

        `/acoes/atualizar` saiu: recarregar a página e rodar o ciclo viraram um
        botão só ("Sync now"), porque dois botões vizinhos que parecem fazer a
        mesma coisa fazem o operador escolher no escuro. O modal de credenciais
        continua servido, mas a partir do rodapé — trocar a própria senha não é
        uma ação de sincronização e não pertence àquela barra.
        """
        _, corpo, _ = self.pega("/")
        for acao in ("/acoes/idioma", "/acoes/cron", "/logout",
                     "/acoes/testar-gateway", "/acoes/credenciais"):
            self.assertIn(f'action="{acao}"', corpo, f"{acao} não está na página")
        self.assertNotIn(
            'action="/acoes/atualizar"',
            corpo,
            "recarregar virou parte do próprio Sync now",
        )

    def test_there_is_only_one_route_that_runs_a_cycle(self):
        """`/acoes/sincronizar` foi unificada em `/acoes/cron`, como nos irmãos.

        Duas rotas para a mesma ação davam dois botões que faziam a mesma coisa,
        e o ciclo disparado pela primeira não entrava no histórico do agendador —
        a tela mostrava "nenhum ciclo executado" logo depois de executar um.
        """
        _, corpo, _ = self.pega("/")
        self.assertNotIn("/acoes/sincronizar", corpo)
        status, _ = self.posta("/acoes/sincronizar", cabecalhos=self.MESMA_ORIGEM)
        self.assertEqual(status, 404)

    def test_the_header_primary_button_runs_the_scheduler_cycle(self):
        """O botão primário do cabeçalho é o mesmo dos irmãos e aponta para o cron."""
        _, corpo, _ = self.pega("/")
        self.assertIn('<form method="post" action="/acoes/cron" class="m-0">\n'
                      '          <button class="btn btn-primary btn-sm" type="submit">', corpo)

    def test_triggering_the_scheduler_answers_with_a_notice(self):
        status, destino = self.posta("/acoes/cron", cabecalhos=self.MESMA_ORIGEM)
        self.assertEqual(status, 303)
        self.assertIn("tom=success", destino)
        self.assertIn("aviso=", destino)

    def test_refreshing_only_reloads_and_does_not_claim_a_cycle_ran(self):
        """`/acoes/atualizar` recarrega a tela; quem inspeciona é `/acoes/cron`."""
        status, destino = self.posta("/acoes/atualizar", cabecalhos=self.MESMA_ORIGEM)
        self.assertEqual(status, 303)
        self.assertIn("tom=info", destino)

    def test_the_scheduler_history_records_the_runs(self):
        import json

        self.posta("/acoes/cron", cabecalhos=self.MESMA_ORIGEM)
        _, corpo, _ = self.pega("/api/cron-status")
        estado = json.loads(corpo)
        self.assertGreaterEqual(estado["totalRuns"], 1)
        self.assertGreaterEqual(len(estado["history"]), 1)
        # O ciclo do painel inspeciona 2 chaves e 1 modelo do proxy de mentira.
        self.assertEqual(estado["history"][0]["totalInspected"], 3)
        # E encontra a incoerência de limite que o ClienteFalso planta.
        self.assertGreaterEqual(estado["history"][0]["findingsCount"], 1)

    def test_the_scheduler_status_never_carries_a_key(self):
        self.posta("/acoes/cron", cabecalhos=self.MESMA_ORIGEM)
        _, corpo, _ = self.pega("/api/cron-status")
        self.assertNotIn(TOKEN_SECRETO, corpo)
        self.assertIsNone(re.search(r"sk-[A-Za-z0-9_-]{20}", corpo))

    def test_the_history_modal_is_on_the_page(self):
        _, corpo, _ = self.pega("/")
        self.assertIn('id="modalHistorico"', corpo)
        self.assertIn('data-bs-target="#modalHistorico"', corpo)

    # -- conteúdo -----------------------------------------------------------

    def test_each_row_offers_a_detail_modal(self):
        """Mesmo padrão dos irmãos: coluna estreita com (i), detalhe por extenso."""
        _, corpo, _ = self.pega("/")
        for alvo in ("detalhe-chave-0", "detalhe-modelo-0"):
            self.assertIn(f'data-bs-target="#{alvo}"', corpo, f"botão (i) de {alvo} ausente")
            self.assertIn(f'id="{alvo}"', corpo, f"modal {alvo} ausente")

    def test_no_detail_modal_is_nested_inside_a_table(self):
        """Um `<div>` dentro de `<tbody>` é HTML inválido, e o navegador o move sozinho.

        O modal precisa sair DEPOIS de `</table>`; dentro dela, o Bootstrap
        acabaria com o diálogo remontado num lugar que ninguém escreveu.
        """
        _, corpo, _ = self.pega("/")
        for tabela in re.findall(r"<table\b.*?</table>", corpo, re.S):
            self.assertNotIn("modal fade", tabela, "modal declarado dentro da tabela")

    def test_the_detail_modal_never_carries_a_key_or_a_token(self):
        _, corpo, _ = self.pega("/")
        trecho = corpo[corpo.find('id="detalhe-chave-0"'):]
        self.assertNotIn(TOKEN_SECRETO, trecho)

    def test_the_limit_finding_reaches_the_screen(self):
        _, corpo, _ = self.pega("/")
        self.assertIn("acima do teto", corpo)

    def test_a_key_without_an_alias_is_shown_masked(self):
        _, corpo, _ = self.pega("/")
        self.assertIn("…" + TOKEN_SECRETO[-6:], corpo)

    def test_the_team_column_shows_the_alias_not_the_raw_id(self):
        # O achado de limite já citava 'time-restrito'; a tabela ao lado mostrava
        # 't1'. Mesmo dado, dois nomes na mesma tela.
        _, corpo, _ = self.pega("/")
        self.assertIn("time-restrito", corpo)
        self.assertIn('title="t1"', corpo,
                      "o id precisa continuar acessível para casar tela e API")

    def test_login_cookie_with_x_forwarded_proto_includes_secure(self):
        """When behind an HTTPS reverse proxy (X-Forwarded-Proto: https), cookies must be Secure."""
        import http.client
        import urllib.parse
        conn = http.client.HTTPConnection("127.0.0.1", PORTA, timeout=3.0)
        body = urllib.parse.urlencode({"usuario": "admin", "senha": self.recuperacao})
        conn.request("POST", "/login", body=body, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Forwarded-Proto": "https",
        })
        resp = conn.getresponse()
        self.assertEqual(resp.status, 302)
        cookie = resp.headers.get("Set-Cookie", "")
        self.assertIn("; Secure", cookie)
        conn.close()

    def test_login_cookie_without_x_forwarded_proto_no_secure(self):
        """When accessed over local plain HTTP without reverse proxy, cookies omit Secure."""
        import http.client
        import urllib.parse
        conn = http.client.HTTPConnection("127.0.0.1", PORTA, timeout=3.0)
        body = urllib.parse.urlencode({"usuario": "admin", "senha": self.recuperacao})
        conn.request("POST", "/login", body=body, headers={
            "Content-Type": "application/x-www-form-urlencoded",
        })
        resp = conn.getresponse()
        self.assertEqual(resp.status, 302)
        cookie = resp.headers.get("Set-Cookie", "")
        self.assertNotIn("Secure", cookie)
        conn.close()


class TestRotuloDoTime(unittest.TestCase):
    """O apelido do time na tabela de chaves.

    O `/key/list` do LiteLLM devolve `team_alias` sempre nulo — medido contra o
    proxy real, inclusive com `include_team_keys=true`. O apelido só existe no
    `/team/list`, então a tela depende de um join que o painel precisa fazer.
    Sem servidor: é render puro e uma chamada de método com objeto de mentira.
    """

    def chaves(self, *team_ids):
        return [VirtualKeyRecord({"key_alias": f"k{i}", "team_id": t})
                for i, t in enumerate(team_ids)]

    def test_the_alias_replaces_the_id_when_the_map_has_it(self):
        html = render_keys_table(self.chaves("t1"), 900, "en", {"t1": "time-restrito"})
        self.assertIn("time-restrito", html)
        self.assertIn('title="t1"', html)

    def test_an_unknown_team_falls_back_to_the_id(self):
        # Chave de um time que o /team/list não devolveu: mostrar o id é o certo,
        # inventar rótulo não é.
        html = render_keys_table(self.chaves("t-fantasma"), 900, "en", {"t1": "time-restrito"})
        self.assertIn("t-fantasma", html)

    def test_no_map_at_all_keeps_the_previous_behaviour(self):
        html = render_keys_table(self.chaves("t1"), 900, "en")
        self.assertIn("t1", html)

    def test_a_key_without_a_team_says_so_instead_of_showing_nothing(self):
        html = render_keys_table(self.chaves(None), 900, "en")
        self.assertIn("Not declared", html)

    def test_the_alias_also_reaches_the_detail_modal(self):
        html = render_keys_table(self.chaves("t1"), 900, "en", {"t1": "time-restrito"})
        modal = html[html.find('id="detalhe-chave-0"'):]
        self.assertIn("time-restrito", modal)

    def montar_estado(self, cliente):
        """Chama `collect_dashboard_state` com um objeto mínimo no lugar do handler.

        O handler é um `BaseHTTPRequestHandler`: instanciar de verdade exigiria
        socket. O método só usa estes atributos, então basta oferecê-los.
        """
        motor = type("Motor", (), {"client": cliente})()
        stub = type("Stub", (), {
            "engine": motor,
            "settings": None,
            "cron_scheduler": None,
            "run_cycle": lambda self: {"details": []},
            "gateway_url": lambda self: "http://gateway.invalido:4000",
            "probe_gateway": lambda self: True,
        })()
        anterior = DashboardHandler.last_cycle
        DashboardHandler.last_cycle = {"details": []}
        try:
            return DashboardHandler.collect_dashboard_state(stub)
        finally:
            DashboardHandler.last_cycle = anterior

    def test_the_panel_builds_the_map_from_team_list(self):
        estado = self.montar_estado(ClienteFalso())
        self.assertEqual(estado["team_aliases"], {"t1": "time-restrito"})

    def test_a_team_without_an_alias_maps_to_its_own_id(self):
        class SemApelido(ClienteFalso):
            def list_teams(self):
                return [{"team_id": "t9", "team_alias": None}]

        self.assertEqual(self.montar_estado(SemApelido())["team_aliases"], {"t9": "t9"})

    def test_a_broken_team_route_does_not_take_the_page_down(self):
        # Versão de proxy sem /team/list não pode apagar a tabela de chaves.
        class RotaQuebrada(ClienteFalso):
            def list_teams(self):
                raise RuntimeError("/team/list indisponível")

        estado = self.montar_estado(RotaQuebrada())
        self.assertEqual(estado["team_aliases"], {})
        self.assertTrue(estado["keys"], "as chaves precisam sobreviver à rota quebrada")


if __name__ == "__main__":
    unittest.main()
