"""Acesso federado: o caminho feliz e, sobretudo, os caminhos ruins.

Um fluxo de SSO testado só no caminho feliz não está testado. O que derruba um
painel não é o login que funciona — é o `state` que não foi conferido, o `code`
reapresentado, a asserção fora do prazo, a audiência de outro serviço e o
`redirect_uri` derivado do cabeçalho `Host`. Cada uma dessas cinco tem aqui um
teste com entrada ruim.

Sem rede e sem mock: o provedor de identidade é um servidor de verdade no
loopback, e o painel fala com ele por HTTP real. É por isso que
`sso._transporte_seguro` aceita `http://` em `127.0.0.1` e exige TLS em
qualquer outro lugar — um provedor de teste no loopback nunca põe um byte na
rede, e um endereço externo sem TLS entregaria o `code` a quem estiver no
caminho.

O que NÃO é exercitado aqui, e por quê: a validação da asserção SAML
(assinatura XML, `Audience`, `NotOnOrAfter`, defesa contra XML Signature
Wrapping) é feita pela `python3-saml`, que não está instalada nesta imagem. O
que é NOSSO no SAML — o conjunto de pendentes que dá sentido ao `InResponseTo`,
o cache de repetição, a montagem da AuthnRequest a partir de `sso.base_url` e o
dicionário de configuração da biblioteca — está testado logo abaixo.
"""

import base64
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from litellm_rtksync import protecao, sessao, sso
from litellm_rtksync.config import Settings
from litellm_rtksync.render import render_dashboard, render_login_page
from litellm_rtksync.web import DashboardHandler, start_web

PORTA_PAINEL = 19391
PORTA_IDP = 19392
BASE = f"http://127.0.0.1:{PORTA_PAINEL}"
ISSUER = f"http://127.0.0.1:{PORTA_IDP}"
CLIENT_ID = "cliente-do-painel"
SEGREDO = "segredo-do-cliente-que-nunca-volta-para-a-tela"
EMAIL = "chefe@empresa.com"


# ---------------------------------------------------------------------------
# O provedor de identidade de mentira
# ---------------------------------------------------------------------------

def _b64url(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).decode("ascii").rstrip("=")


def monta_id_token(payload: dict) -> str:
    """JWT com assinatura de enfeite.

    A assinatura NÃO é verificada por este painel, e isso é escolha declarada:
    o token chega pelo canal direto ao `token_endpoint`, sobre TLS e com o
    cliente autenticado (OIDC Core 3.1.3.7, item 6). Um JWT de mentira aqui
    prova justamente que o resto das conferências é que faz o trabalho.
    """
    cabecalho = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode("utf-8"))
    corpo = _b64url(json.dumps(payload).encode("utf-8"))
    return f"{cabecalho}.{corpo}.{_b64url(b'assinatura-de-enfeite')}"


class ProvedorFalso(BaseHTTPRequestHandler):
    """Cada teste ajusta estes atributos de classe antes de exercitar o fluxo."""

    nonce = ""
    sub = "identificador-estavel"
    email = EMAIL
    email_verified = True
    sub_do_userinfo = None          # None = igual ao do id_token
    audiencia = CLIENT_ID
    emissor = ISSUER
    validade = 300                  # segundos até o `exp`
    desvio_do_iat = 0               # segundos de desvio do `iat`
    redirect_uri_exigido = ""
    codes_gastos: set = set()
    ultimo_redirect_uri = ""
    ultimo_code_verifier = ""

    protocol_version = "HTTP/1.1"

    @classmethod
    def rearma(cls):
        cls.nonce = ""
        cls.sub = "identificador-estavel"
        cls.email = EMAIL
        cls.email_verified = True
        cls.sub_do_userinfo = None
        cls.audiencia = CLIENT_ID
        cls.emissor = ISSUER
        cls.validade = 300
        cls.desvio_do_iat = 0
        cls.redirect_uri_exigido = f"{BASE}/sso/oidc/callback"
        cls.codes_gastos = set()
        cls.ultimo_redirect_uri = ""
        cls.ultimo_code_verifier = ""

    def log_message(self, *args):
        return

    def responde(self, carga: dict, status: int = 200):
        corpo = json.dumps(carga).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        caminho = urllib.parse.urlparse(self.path).path
        # Endereço diferente servindo o MESMO documento: é assim que se monta a
        # confusão entre provedores. O documento continua declarando o emissor
        # de sempre, e é a divergência entre os dois que tem de ser recusada --
        # um 404 aqui reprovaria pelo motivo errado.
        if caminho == "/outro/.well-known/openid-configuration":
            caminho = "/.well-known/openid-configuration"
        if caminho == "/.well-known/openid-configuration":
            self.responde({
                "issuer": ISSUER,
                "authorization_endpoint": ISSUER + "/authorize",
                "token_endpoint": ISSUER + "/token",
                "userinfo_endpoint": ISSUER + "/userinfo",
                "code_challenge_methods_supported": ["S256"],
            })
            return
        if caminho == "/userinfo":
            if not self.headers.get("Authorization", "").startswith("Bearer "):
                self.responde({"error": "sem access token"}, 401)
                return
            sub = self.sub if self.sub_do_userinfo is None else self.sub_do_userinfo
            self.responde({
                "sub": sub,
                "email": type(self).email,
                "email_verified": type(self).email_verified,
            })
            return
        self.responde({"error": "rota inexistente"}, 404)

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/token":
            self.responde({"error": "rota inexistente"}, 404)
            return
        tamanho = int(self.headers.get("Content-Length") or 0)
        campos = urllib.parse.parse_qs(self.rfile.read(tamanho).decode("utf-8"))
        code = (campos.get("code") or [""])[0]
        redirect_uri = (campos.get("redirect_uri") or [""])[0]
        type(self).ultimo_redirect_uri = redirect_uri
        type(self).ultimo_code_verifier = (campos.get("code_verifier") or [""])[0]

        # Um provedor de verdade invalida o `code` na primeira troca.
        if code in type(self).codes_gastos:
            self.responde({"error": "invalid_grant"}, 400)
            return
        type(self).codes_gastos.add(code)

        # E recusa uma troca cujo `redirect_uri` não é o combinado.
        if type(self).redirect_uri_exigido and redirect_uri != type(self).redirect_uri_exigido:
            self.responde({"error": "invalid_grant"}, 400)
            return

        agora = int(time.time())
        self.responde({
            "access_token": "token-de-acesso",
            "token_type": "Bearer",
            "id_token": monta_id_token({
                "iss": type(self).emissor,
                "aud": type(self).audiencia,
                "sub": type(self).sub,
                "exp": agora + type(self).validade,
                "iat": agora + type(self).desvio_do_iat,
                "nonce": type(self).nonce,
                "email": type(self).email,
                "email_verified": type(self).email_verified,
            }),
        })


class ClienteFalsoDoProxy:
    """O painel precisa de um motor; o teste é do SSO, não do gateway."""

    def health(self):
        return True

    def list_keys(self):
        return []

    def list_teams(self):
        return []

    def list_models(self):
        return []


class MotorFalso:
    def __init__(self):
        self.client = ClienteFalsoDoProxy()

    def sync_all(self):
        return {"details": []}


# ---------------------------------------------------------------------------
# Partes que não precisam de servidor
# ---------------------------------------------------------------------------

class CookieDeEstado(unittest.TestCase):
    """O estado da ida viaja num cookie assinado com o segredo do processo."""

    def test_o_que_saiu_daqui_volta_igual(self):
        valor = sessao.emitir_estado_sso("s", "n", "v")
        self.assertEqual(
            sessao.ler_estado_sso(valor),
            {"state": "s", "nonce": "n", "verificador": "v"},
        )

    def test_um_cookie_adulterado_nao_vale(self):
        valor = sessao.emitir_estado_sso("s", "n", "v")
        corpo, assinatura = valor.rsplit(".", 1)
        # O separador é "|": os três valores nascem de `secrets.token_urlsafe`,
        # que nunca o produz, então ele não aparece dentro de campo nenhum.
        forjado = "|".join(["outro", "n", "v", str(int(time.time()) + 60)])
        adulterado = base64.urlsafe_b64encode(forjado.encode()).decode() + "." + assinatura
        self.assertIsNone(sessao.ler_estado_sso(adulterado))

    def test_fora_do_prazo_nao_vale(self):
        valor = sessao.emitir_estado_sso("s", "n", "v", agora=1000)
        self.assertIsNone(
            sessao.ler_estado_sso(valor, agora=1000 + sessao.VALIDADE_DO_ESTADO_EM_SEGUNDOS + 1)
        )

    def test_o_cookie_de_estado_nao_e_strict(self):
        """Strict não é enviado na volta cross-site: o login falharia em silêncio."""
        cabecalho = sessao.cabecalho_para_gravar_estado("x")
        self.assertIn("SameSite=Lax", cabecalho)
        self.assertIn("HttpOnly", cabecalho)
        self.assertIn("Path=/sso/", cabecalho)

    def test_apagar_usa_o_mesmo_caminho_de_gravar(self):
        """Path diferente não casa, e o cookie sobreviveria ao consumo."""
        self.assertIn("Path=/sso/", sessao.cabecalho_para_apagar_estado())


class ProvaDeChave(unittest.TestCase):
    def test_o_desafio_e_o_sha256_do_verificador_sem_preenchimento(self):
        verificador = "verificador-qualquer"
        esperado = base64.urlsafe_b64encode(
            hashlib.sha256(verificador.encode()).digest()
        ).decode().rstrip("=")
        self.assertEqual(sso.desafio_de(verificador), esperado)
        self.assertNotIn("=", sso.desafio_de(verificador))


class ListaDeAutorizados(unittest.TestCase):
    def config(self, dominios=(), emails=()):
        return sso.ConfiguracaoSSO(dominios=dominios, emails=emails)

    def test_o_endereco_exato_entra(self):
        self.assertTrue(sso.email_autorizado(EMAIL, self.config(emails=(EMAIL,))))

    def test_o_dominio_inteiro_entra(self):
        self.assertTrue(sso.email_autorizado(EMAIL, self.config(dominios=("empresa.com",))))

    def test_quem_esta_fora_nao_entra(self):
        config = self.config(dominios=("empresa.com",))
        self.assertFalse(sso.email_autorizado("estranho@outra.com", config))

    def test_lista_vazia_nao_deixa_ninguem_entrar(self):
        """Lista vazia não é "sem filtro": é o painel recusando ligar o SSO."""
        self.assertFalse(sso.email_autorizado(EMAIL, self.config()))

    def test_e_a_configuracao_com_lista_vazia_nao_liga(self):
        config = sso.ConfiguracaoSSO(
            provedor="oidc", base_url=BASE, issuer=ISSUER,
            client_id=CLIENT_ID, tem_segredo=True,
        )
        self.assertFalse(config.esta_ligado())


class ConferenciaDoIdToken(unittest.TestCase):
    """A lista fechada de conferências do corpo do id_token."""

    def setUp(self):
        self.config = sso.ConfiguracaoSSO(
            provedor="oidc", base_url=BASE, issuer=ISSUER, client_id=CLIENT_ID,
            dominios=("empresa.com",), tem_segredo=True,
        )
        self.agora = 1_700_000_000

    def payload(self, **troca):
        base = {
            "iss": ISSUER, "aud": CLIENT_ID, "sub": "s",
            "exp": self.agora + 300, "iat": self.agora, "nonce": "o-nonce",
        }
        base.update(troca)
        return base

    def test_o_caminho_feliz_passa(self):
        sso.confere_id_token(self.payload(), self.config, "o-nonce", agora=self.agora)

    def test_emissor_diferente_e_recusado(self):
        with self.assertRaises(sso.FalhaDeSSO):
            sso.confere_id_token(
                self.payload(iss="https://outro-provedor.invalid"),
                self.config, "o-nonce", agora=self.agora,
            )

    def test_audiencia_errada_e_recusada(self):
        """Um token legítimo emitido para OUTRO serviço não vale aqui."""
        with self.assertRaises(sso.FalhaDeSSO):
            sso.confere_id_token(
                self.payload(aud="outro-cliente"), self.config, "o-nonce", agora=self.agora
            )

    def test_varias_audiencias_sem_azp_nosso_e_recusado(self):
        with self.assertRaises(sso.FalhaDeSSO):
            sso.confere_id_token(
                self.payload(aud=[CLIENT_ID, "outro"], azp="outro"),
                self.config, "o-nonce", agora=self.agora,
            )

    def test_token_fora_do_prazo_e_recusado(self):
        with self.assertRaises(sso.FalhaDeSSO):
            sso.confere_id_token(
                self.payload(exp=self.agora - 1), self.config, "o-nonce", agora=self.agora
            )

    def test_iat_fora_da_tolerancia_e_recusado(self):
        with self.assertRaises(sso.FalhaDeSSO):
            sso.confere_id_token(
                self.payload(iat=self.agora - sso.TOLERANCIA_DE_RELOGIO - 60),
                self.config, "o-nonce", agora=self.agora,
            )

    def test_nonce_de_outra_ida_e_recusado(self):
        with self.assertRaises(sso.FalhaDeSSO):
            sso.confere_id_token(
                self.payload(nonce="de-outra-ida"), self.config, "o-nonce", agora=self.agora
            )

    def test_um_state_com_acento_nao_derruba_a_comparacao(self):
        """`hmac.compare_digest` levanta TypeError com str fora de ASCII.

        O `state` da query é escolhido por quem chama: sem comparar bytes, um
        valor hostil viraria erro 500 em vez de recusa.
        """
        self.assertFalse(sso.mesmo_texto("ação", "outro"))


class UsoUnicoDoEstado(unittest.TestCase):
    def setUp(self):
        sso.esquece_estado_de_fluxo()

    def test_o_mesmo_estado_so_vale_uma_vez(self):
        self.assertFalse(sso.estado_ja_usado("abc"))
        self.assertTrue(sso.estado_ja_usado("abc"))


class PendentesDoSaml(unittest.TestCase):
    """O conjunto de pendentes é o que dá sentido ao `InResponseTo`."""

    def setUp(self):
        sso.esquece_estado_de_fluxo()

    def test_o_que_foi_registrado_e_consumido_uma_vez_so(self):
        sso.registra_pendente("_abc")
        self.assertTrue(sso.consome_pendente("_abc"))
        self.assertFalse(sso.consome_pendente("_abc"))

    def test_id_que_ninguem_registrou_nao_passa(self):
        self.assertFalse(sso.consome_pendente("_inventado"))

    def test_pendente_fora_do_prazo_nao_passa(self):
        sso.registra_pendente("_velho", agora=1000)
        self.assertFalse(
            sso.consome_pendente("_velho", agora=1000 + sso.VALIDADE_DO_PENDENTE + 1)
        )

    def test_o_conjunto_tem_teto_e_descarta_o_mais_antigo(self):
        """A rota é pública: sem teto, ela vira consumo de memória ilimitado."""
        for numero in range(sso.LIMITE_DE_PENDENTES + 10):
            sso.registra_pendente(f"_{numero}", agora=1000 + numero)
        self.assertLessEqual(len(sso._pendentes), sso.LIMITE_DE_PENDENTES)
        self.assertFalse(sso.consome_pendente("_0", agora=1000))

    def test_a_mesma_assercao_nao_vale_duas_vezes(self):
        self.assertFalse(sso.assercao_ja_usada("id-da-assercao"))
        self.assertTrue(sso.assercao_ja_usada("id-da-assercao"))

    def test_resposta_sem_in_response_to_e_recusada(self):
        """Fluxo iniciado pelo IdP é o vetor clássico de CSRF de login."""
        xml = b'<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"/>'
        with self.assertRaises(sso.FalhaDeSSO):
            sso.in_response_to(base64.b64encode(xml).decode())

    def test_o_in_response_to_e_lido_da_resposta(self):
        xml = b'<samlp:Response InResponseTo="_pendente-1"/>'
        self.assertEqual(sso.in_response_to(base64.b64encode(xml).decode()), "_pendente-1")


class EnderecosDoSaml(unittest.TestCase):
    """Tudo sai de `sso.base_url`, e nada do cabeçalho `Host`."""

    def setUp(self):
        self.config = sso.ConfiguracaoSSO(
            provedor="saml", base_url="https://painel.exemplo.com",
            idp_entity_id="https://idp.exemplo.com/metadata",
            idp_sso_url="https://idp.exemplo.com/sso",
            idp_cert="MIIC-de-mentira", dominios=("empresa.com",),
        )

    def test_a_authn_request_aponta_para_o_acs_da_configuracao(self):
        xml = sso.monta_authn_request(self.config, "_id", agora=1_700_000_000)
        self.assertIn('AssertionConsumerServiceURL="https://painel.exemplo.com/sso/saml/acs"', xml)
        self.assertIn('Destination="https://idp.exemplo.com/sso"', xml)
        self.assertIn("<saml:Issuer>https://painel.exemplo.com/sso/saml/metadata</saml:Issuer>", xml)

    def test_o_binding_de_ida_e_por_redirecionamento(self):
        """HTTP-POST binding bateria na CSP `form-action 'self'` e seria bloqueado."""
        xml = sso.monta_authn_request(self.config, "_id", agora=1_700_000_000)
        destino = sso.url_de_ida_saml(self.config, xml)
        self.assertTrue(destino.startswith("https://idp.exemplo.com/sso?SAMLRequest="))
        parametro = urllib.parse.parse_qs(urllib.parse.urlsplit(destino).query)["SAMLRequest"][0]
        recuperado = zlib.decompress(base64.b64decode(parametro), -zlib.MAX_WBITS).decode("utf-8")
        self.assertEqual(recuperado, xml)

    def test_a_biblioteca_recebe_o_destino_da_configuracao_e_nao_da_requisicao(self):
        """O default da `python3-saml` monta a URL do ACS a partir do `http_host`."""
        ajustes = sso.configuracao_da_biblioteca(self.config)
        self.assertTrue(ajustes["strict"])
        self.assertTrue(ajustes["security"]["wantAssertionsSigned"])
        self.assertTrue(ajustes["security"]["wantMessagesSigned"])
        self.assertTrue(ajustes["security"]["rejectUnsolicitedResponsesWithInResponseTo"])
        self.assertEqual(
            ajustes["sp"]["assertionConsumerService"]["url"],
            "https://painel.exemplo.com/sso/saml/acs",
        )
        dados = sso.dados_da_requisicao(self.config, {"SAMLResponse": "x"})
        self.assertEqual(dados["http_host"], "painel.exemplo.com")
        self.assertEqual(dados["https"], "on")

    def test_a_porta_viaja_dentro_do_host_e_nao_num_campo_proprio(self):
        """O campo separado, vazio, montava "https://host:/sso/saml/acs".

        Com dois-pontos e sem número — e a biblioteca recusava a própria
        asserção correta dizendo que o destino não batia. Encontrado na bancada,
        com asserção assinada de verdade; preso aqui para que o defeito não
        volte numa máquina onde a biblioteca nem está instalada.
        """
        com_porta = sso.ConfiguracaoSSO(base_url="http://127.0.0.1:9093")
        dados = sso.dados_da_requisicao(com_porta, {})
        self.assertEqual(dados["http_host"], "127.0.0.1:9093")
        self.assertEqual(dados["https"], "off")
        self.assertNotIn("server_port", dados)

    def test_sem_a_biblioteca_o_painel_recusa_em_vez_de_quebrar(self):
        if sso.saml_disponivel():
            self.skipTest("a biblioteca está instalada nesta máquina")
        with self.assertRaises(sso.FalhaDeSSO):
            sso.processa_resposta_saml(self.config, "qualquer-coisa", "_id")


class GuardaDoSegredo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.caminho = os.path.join(self.tmp.name, sso.ARQUIVO_DO_SEGREDO)
        os.environ.pop(sso.VARIAVEL_DO_SEGREDO, None)

    def tearDown(self):
        os.environ.pop(sso.VARIAVEL_DO_SEGREDO, None)
        self.tmp.cleanup()

    def test_o_arquivo_nasce_com_permissao_0600(self):
        self.assertTrue(sso.grava_segredo(self.caminho, SEGREDO))
        self.assertEqual(os.stat(self.caminho).st_mode & 0o777, 0o600)
        self.assertEqual(sso.ler_segredo(self.caminho), (SEGREDO, False))

    def test_o_ambiente_vence_o_arquivo(self):
        sso.grava_segredo(self.caminho, SEGREDO)
        os.environ[sso.VARIAVEL_DO_SEGREDO] = "vindo-do-ambiente"
        self.assertEqual(sso.ler_segredo(self.caminho), ("vindo-do-ambiente", True))

    def test_sem_segredo_nenhum_a_configuracao_nao_liga(self):
        config = sso.ConfiguracaoSSO(
            provedor="oidc", base_url=BASE, issuer=ISSUER, client_id=CLIENT_ID,
            dominios=("empresa.com",), tem_segredo=False,
        )
        self.assertFalse(config.esta_ligado())


class InterruptorDeEmergencia(unittest.TestCase):
    def tearDown(self):
        os.environ.pop(sso.VARIAVEL_DE_DESLIGAMENTO, None)

    def test_a_variavel_vence_o_banco(self):
        config = sso.ConfiguracaoSSO(
            provedor="oidc", base_url=BASE, issuer=ISSUER, client_id=CLIENT_ID,
            dominios=("empresa.com",), tem_segredo=True,
        )
        self.assertTrue(config.esta_ligado())
        config.desligado_no_ambiente = True
        self.assertFalse(config.esta_ligado())

    def test_o_padrao_da_variavel_e_ligado(self):
        """`_flag` do config assume "1"; usá-lo aqui desligaria toda instalação."""
        os.environ.pop(sso.VARIAVEL_DE_DESLIGAMENTO, None)
        self.assertFalse(sso.desligado_por_ambiente())
        os.environ[sso.VARIAVEL_DE_DESLIGAMENTO] = "1"
        self.assertTrue(sso.desligado_por_ambiente())

    def test_o_nome_da_constante_e_o_nome_que_o_codigo_le(self):
        """O literal em `os.environ.get` é o que a guarda do .env.example enxerga.

        Escrever o nome duas vezes só é aceitável enquanto as duas formas
        concordarem — e é isto que este teste cobra.
        """
        fonte = (
            __import__("pathlib").Path(sso.__file__).read_text(encoding="utf-8")
        )
        for nome in (sso.VARIAVEL_DE_DESLIGAMENTO, sso.VARIAVEL_DO_SEGREDO):
            self.assertIn(f'os.environ.get("{nome}"', fonte)


class TelaSemConfiguracao(unittest.TestCase):
    """Sem configuração, a tela é exatamente a de hoje."""

    def test_a_pagina_de_login_nao_ganha_botao_nenhum(self):
        html = render_login_page("pt").decode("utf-8")
        self.assertNotIn("/sso/oidc/iniciar", html)
        self.assertIn('action="/login"', html)

    def test_com_configuracao_o_botao_aparece_ao_lado_do_formulario(self):
        config = sso.ConfiguracaoSSO(
            provedor="oidc", base_url=BASE, issuer=ISSUER, client_id=CLIENT_ID,
            dominios=("empresa.com",), tem_segredo=True,
        )
        html = render_login_page("pt", sso=config).decode("utf-8")
        self.assertIn('href="/sso/oidc/iniciar"', html)
        # O formulário de senha NÃO sai da tela em configuração nenhuma.
        self.assertIn('action="/login"', html)
        self.assertIn("127.0.0.1", html)

    def test_o_botao_e_um_link_e_nunca_um_formulario(self):
        """A CSP declara `form-action 'self'`: um <form> para fora é bloqueado."""
        config = sso.ConfiguracaoSSO(
            provedor="oidc", base_url=BASE, issuer=ISSUER, client_id=CLIENT_ID,
            dominios=("empresa.com",), tem_segredo=True,
        )
        html = render_login_page("pt", sso=config).decode("utf-8")
        self.assertNotIn('action="/sso/', html)


class BotaoNoCabecalho(unittest.TestCase):
    def pagina(self):
        return render_dashboard(
            keys=[], models=[], model_states={}, findings=[], counters={},
            cron={"active": False},
            proxy={"url": "http://proxy:4000", "online": True, "latencyMs": 1},
            current_user="admin", is_default_password=False, refresh_margin=900,
            lang="pt",
        )

    def test_configuracoes_vem_antes_de_sair(self):
        """Sair é sempre o último controle da barra."""
        html = self.pagina()
        posicao_sso = html.find('data-bs-target="#modalSSO"')
        posicao_sair = html.find('action="/logout"')
        self.assertGreater(posicao_sso, 0, "o botão de configurações não está na barra")
        self.assertLess(posicao_sso, posicao_sair, "Sair tem de ser o último")

    def test_o_botao_tem_texto_como_os_demais(self):
        self.assertIn("Configurações", self.pagina())

    def test_o_modal_tem_as_duas_abas(self):
        html = self.pagina()
        self.assertIn('id="abaOIDC"', html)
        self.assertIn('id="abaSAML"', html)


# ---------------------------------------------------------------------------
# O fluxo inteiro, por HTTP de verdade
# ---------------------------------------------------------------------------

class SemRedirecionamento(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class FluxoOIDC(unittest.TestCase):
    """Ida, volta e cada uma das formas de a volta estar errada."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.settings = Settings(
            litellm_url="http://proxy:4000",
            data_dir=cls.tmp.name,
            web_host="127.0.0.1",
            web_port=PORTA_PAINEL,
            validate_credentials=False,
        )
        cls.recuperacao, _ = cls.settings.ensure_recovery_hash()

        cls.idp = ThreadingHTTPServer(("127.0.0.1", PORTA_IDP), ProvedorFalso)
        cls.idp.daemon_threads = True
        threading.Thread(target=cls.idp.serve_forever, daemon=True).start()

        cls.painel = start_web(cls.settings, MotorFalso())
        time.sleep(0.4)

    @classmethod
    def tearDownClass(cls):
        agendador = getattr(cls.painel, "cron_scheduler", None)
        if agendador is not None:
            agendador.stop()
        cls.painel.shutdown()
        cls.painel.server_close()
        cls.idp.shutdown()
        cls.idp.server_close()
        DashboardHandler.last_cycle = {}
        DashboardHandler.cron_scheduler = None
        cls.tmp.cleanup()

    def setUp(self):
        ProvedorFalso.rearma()
        sso.esquece_descobertas()
        sso.esquece_estado_de_fluxo()
        # O teto por janela é de dez tentativas por endereço, e todo este
        # arquivo bate do mesmo 127.0.0.1: sem zerar, os últimos casos receberiam
        # 429 e o resto da suíte junto.
        protecao.limpa_apos_sucesso("127.0.0.1")
        os.environ.pop(sso.VARIAVEL_DE_DESLIGAMENTO, None)
        self.liga_sso()

    def tearDown(self):
        os.environ.pop(sso.VARIAVEL_DE_DESLIGAMENTO, None)
        protecao.limpa_apos_sucesso("127.0.0.1")

    # -- auxiliares ---------------------------------------------------------

    def liga_sso(self, **troca):
        campos = {
            sso.CHAVE_PROVEDOR: "oidc",
            sso.CHAVE_BASE_URL: BASE,
            sso.CHAVE_OIDC_ISSUER: ISSUER,
            sso.CHAVE_OIDC_CLIENT_ID: CLIENT_ID,
            sso.CHAVE_OIDC_SCOPES: sso.ESCOPOS_PADRAO,
            sso.CHAVE_DOMINIOS: "empresa.com",
            sso.CHAVE_EMAILS: "",
        }
        campos.update(troca)
        sso.gravar(self.settings.get_prefs_path(), campos)
        sso.grava_segredo(self.settings.get_sso_secret_path(), SEGREDO)

    def pega(self, caminho, cabecalhos=None, seguir=False):
        pedido = urllib.request.Request(BASE + caminho)
        for nome, valor in (cabecalhos or {}).items():
            pedido.add_header(nome, valor)
        abridor = urllib.request.build_opener(
            *( [] if seguir else [SemRedirecionamento] )
        )
        try:
            with abridor.open(pedido, timeout=8) as resposta:
                return resposta.status, resposta.read().decode("utf-8"), resposta.headers
        except urllib.error.HTTPError as erro:
            return erro.code, erro.read().decode("utf-8"), erro.headers

    def posta(self, caminho, corpo=b"", cabecalhos=None):
        pedido = urllib.request.Request(BASE + caminho, data=corpo, method="POST")
        pedido.add_header("Content-Type", "application/x-www-form-urlencoded")
        for nome, valor in (cabecalhos or {}).items():
            pedido.add_header(nome, valor)
        abridor = urllib.request.build_opener(SemRedirecionamento)
        try:
            with abridor.open(pedido, timeout=8) as resposta:
                return resposta.status, resposta.headers
        except urllib.error.HTTPError as erro:
            erro.read()
            return erro.code, erro.headers

    def inicia(self):
        """Faz a ida e devolve (parametros da autorização, cookie de estado)."""
        status, _, cabecalhos = self.pega("/sso/oidc/iniciar")
        self.assertEqual(status, 302, "a ida ao provedor precisa ser um 302")
        destino = cabecalhos.get("Location", "")
        parametros = urllib.parse.parse_qs(urllib.parse.urlsplit(destino).query)
        cookie = cabecalhos.get("Set-Cookie", "").split(";")[0]
        ProvedorFalso.nonce = parametros["nonce"][0]
        return parametros, cookie

    def volta(self, parametros, cookie, code="o-code", state=None, extra=""):
        consulta = urllib.parse.urlencode({
            "code": code,
            "state": parametros["state"][0] if state is None else state,
        })
        return self.pega(
            f"/sso/oidc/callback?{consulta}{extra}", {"Cookie": cookie}
        )

    def sessao_do_cabecalho(self, cabecalhos):
        for valor in cabecalhos.get_all("Set-Cookie") or []:
            if valor.startswith(sessao.NOME_DO_COOKIE + "="):
                return valor.split(";")[0].partition("=")[2]
        return ""

    # -- a ida --------------------------------------------------------------

    def test_a_ida_leva_pkce_s256_e_nunca_plain(self):
        parametros, _ = self.inicia()
        self.assertEqual(parametros["code_challenge_method"], ["S256"])
        self.assertTrue(parametros["code_challenge"][0])
        self.assertEqual(parametros["client_id"], [CLIENT_ID])

    def test_o_redirect_uri_sai_da_configuracao_e_nao_do_host(self):
        """Derivar a URL de retorno do `Host` é a definição de redirect_uri aberto."""
        status, _, cabecalhos = self.pega(
            "/sso/oidc/iniciar", {"Host": "atacante.invalid"}
        )
        self.assertEqual(status, 302)
        destino = cabecalhos.get("Location", "")
        parametros = urllib.parse.parse_qs(urllib.parse.urlsplit(destino).query)
        self.assertEqual(parametros["redirect_uri"], [f"{BASE}/sso/oidc/callback"])
        self.assertNotIn("atacante.invalid", destino)

    def test_a_ida_grava_o_cookie_de_estado_com_samesite_lax(self):
        _, _, cabecalhos = self.pega("/sso/oidc/iniciar")
        cookie = cabecalhos.get("Set-Cookie", "")
        self.assertIn(sessao.NOME_DO_COOKIE_DE_ESTADO, cookie)
        self.assertIn("SameSite=Lax", cookie)

    # -- o caminho feliz ----------------------------------------------------

    def test_a_volta_emite_o_mesmo_cookie_assinado_do_formulario(self):
        parametros, cookie = self.inicia()
        status, corpo, cabecalhos = self.volta(parametros, cookie)
        self.assertEqual(status, 200, "a volta NÃO pode ser 302: ver o pouso no render")
        self.assertIn('<meta http-equiv="refresh" content="0;url=/">', corpo)
        valor = self.sessao_do_cabecalho(cabecalhos)
        self.assertTrue(valor, "a volta precisa emitir o cookie de sessão")
        self.assertEqual(sessao.usuario_da_sessao(valor), "sso:" + EMAIL)

    def test_e_com_esse_cookie_o_painel_abre(self):
        parametros, cookie = self.inicia()
        _, _, cabecalhos = self.volta(parametros, cookie)
        valor = self.sessao_do_cabecalho(cabecalhos)
        status, corpo, _ = self.pega(
            "/", {"Cookie": f"{sessao.NOME_DO_COOKIE}={valor}", "Accept": "text/html"}
        )
        self.assertEqual(status, 200)
        self.assertIn("sso:" + EMAIL, corpo)

    def test_o_pouso_e_sempre_na_raiz_e_nenhum_parametro_vira_destino(self):
        """`next=` usado como destino é redirecionamento aberto autenticado."""
        parametros, cookie = self.inicia()
        _, corpo, _ = self.volta(
            parametros, cookie, extra="&next=https%3A%2F%2Fatacante.invalid"
        )
        self.assertIn('content="0;url=/"', corpo)
        self.assertNotIn("atacante.invalid", corpo)

    # -- as voltas erradas --------------------------------------------------

    def recusou(self, status, corpo, cabecalhos):
        """Toda recusa é igual: a tela de login, sem sessão e sem dizer o motivo."""
        self.assertEqual(status, 200)
        self.assertEqual(self.sessao_do_cabecalho(cabecalhos), "")
        self.assertIn('action="/login"', corpo)

    def test_state_trocado_e_recusado(self):
        parametros, cookie = self.inicia()
        status, corpo, cabecalhos = self.volta(parametros, cookie, state="state-do-atacante")
        self.recusou(status, corpo, cabecalhos)

    def test_um_cookie_hostil_nao_derruba_o_callback(self):
        """O cabeçalho Cookie é decodificado em latin-1, e a rota é pública.

        Um byte acima de 0x7f na assinatura fazia `hmac.compare_digest` levantar
        TypeError — exceção não tratada com entrada escolhida pelo visitante.
        """
        status, corpo, cabecalhos = self.pega(
            "/sso/oidc/callback?code=x&state=y",
            {"Cookie": f"{sessao.NOME_DO_COOKIE_DE_ESTADO}=YWJj.ÿþ"},
        )
        self.recusou(status, corpo, cabecalhos)

    def test_volta_sem_cookie_de_estado_e_recusada(self):
        parametros, _ = self.inicia()
        status, corpo, cabecalhos = self.volta(parametros, "")
        self.recusou(status, corpo, cabecalhos)

    def test_code_reusado_e_recusado(self):
        """A segunda volta com o mesmo estado não vira uma segunda sessão."""
        parametros, cookie = self.inicia()
        _, _, primeira = self.volta(parametros, cookie)
        self.assertTrue(self.sessao_do_cabecalho(primeira))
        status, corpo, segunda = self.volta(parametros, cookie)
        self.recusou(status, corpo, segunda)

    def test_um_code_que_o_provedor_ja_gastou_e_recusado(self):
        parametros, cookie = self.inicia()
        self.volta(parametros, cookie, code="reaproveitado")
        sso.esquece_estado_de_fluxo()
        parametros2, cookie2 = self.inicia()
        status, corpo, cabecalhos = self.volta(parametros2, cookie2, code="reaproveitado")
        self.recusou(status, corpo, cabecalhos)

    def test_redirect_uri_diferente_do_combinado_e_recusado(self):
        """O provedor recusa a troca, e o painel não inventa uma sessão."""
        ProvedorFalso.redirect_uri_exigido = "https://outro-endereco.invalid/callback"
        parametros, cookie = self.inicia()
        status, corpo, cabecalhos = self.volta(parametros, cookie)
        self.recusou(status, corpo, cabecalhos)
        self.assertEqual(ProvedorFalso.ultimo_redirect_uri, f"{BASE}/sso/oidc/callback")

    def test_id_token_fora_do_prazo_e_recusado(self):
        ProvedorFalso.validade = -10
        parametros, cookie = self.inicia()
        self.recusou(*self.volta(parametros, cookie))

    def test_audiencia_de_outro_servico_e_recusada(self):
        ProvedorFalso.audiencia = "outro-servico"
        parametros, cookie = self.inicia()
        self.recusou(*self.volta(parametros, cookie))

    def test_emissor_diferente_do_configurado_e_recusado(self):
        ProvedorFalso.emissor = "https://provedor-hostil.invalid"
        parametros, cookie = self.inicia()
        self.recusou(*self.volta(parametros, cookie))

    def test_relogio_muito_fora_de_hora_e_recusado(self):
        ProvedorFalso.desvio_do_iat = -(sso.TOLERANCIA_DE_RELOGIO + 120)
        parametros, cookie = self.inicia()
        self.recusou(*self.volta(parametros, cookie))

    def test_nonce_de_outra_ida_e_recusado(self):
        parametros, cookie = self.inicia()
        ProvedorFalso.nonce = "nonce-de-outra-ida"
        self.recusou(*self.volta(parametros, cookie))

    def test_sub_do_userinfo_diferente_do_id_token_e_recusado(self):
        """OIDC Core 5.3.2: sem esta amarra o access_token não ancora ninguém."""
        ProvedorFalso.sub_do_userinfo = "outro-sujeito"
        parametros, cookie = self.inicia()
        self.recusou(*self.volta(parametros, cookie))

    def test_email_nao_verificado_e_recusado(self):
        ProvedorFalso.email_verified = False
        parametros, cookie = self.inicia()
        self.recusou(*self.volta(parametros, cookie))

    def test_email_fora_da_lista_e_recusado(self):
        ProvedorFalso.email = "estranho@outra-empresa.com"
        parametros, cookie = self.inicia()
        self.recusou(*self.volta(parametros, cookie))

    def test_toda_recusa_diz_a_mesma_coisa(self):
        """Distinguir os motivos conta ao atacante em que ponto ele parou."""
        parametros, cookie = self.inicia()
        _, com_state_errado, _ = self.volta(parametros, cookie, state="errado")
        sso.esquece_estado_de_fluxo()
        ProvedorFalso.email = "estranho@outra-empresa.com"
        parametros, cookie = self.inicia()
        _, fora_da_lista, _ = self.volta(parametros, cookie)
        self.assertEqual(
            re.findall(r'role="alert".*?</div>', com_state_errado, re.S),
            re.findall(r'role="alert".*?</div>', fora_da_lista, re.S),
        )

    # -- desligar ------------------------------------------------------------

    def test_sem_configuracao_as_rotas_de_sso_nao_existem(self):
        sso.gravar(self.settings.get_prefs_path(), {sso.CHAVE_PROVEDOR: ""})
        for rota in ("/sso/oidc/iniciar", "/sso/oidc/callback", "/sso/saml/iniciar"):
            with self.subTest(rota=rota):
                status, _, _ = self.pega(rota)
                self.assertEqual(status, 404, f"{rota} devia ser 404 com o SSO desligado")

    def test_o_interruptor_de_emergencia_vence_o_banco(self):
        os.environ[sso.VARIAVEL_DE_DESLIGAMENTO] = "1"
        status, _, _ = self.pega("/sso/oidc/iniciar")
        self.assertEqual(status, 404)
        _, corpo, _ = self.pega("/login", {"Accept": "text/html"})
        self.assertNotIn("/sso/oidc/iniciar", corpo)

    def test_o_formulario_local_continua_entrando_com_o_sso_ligado(self):
        """Se o provedor cair, quem tem a senha entra do mesmo jeito."""
        corpo = urllib.parse.urlencode(
            {"usuario": "admin", "senha": self.recuperacao}
        ).encode()
        status, cabecalhos = self.posta("/login", corpo)
        self.assertEqual(status, 302)
        self.assertIn(sessao.NOME_DO_COOKIE, cabecalhos.get("Set-Cookie", ""))

    def test_a_porta_nao_html_continua_sendo_basic_auth(self):
        """curl, cron e monitoramento nunca passam por SSO."""
        credencial = base64.b64encode(f"admin:{self.recuperacao}".encode()).decode()
        status, _, _ = self.pega("/api/status", {"Authorization": "Basic " + credencial})
        self.assertEqual(status, 200)

    def test_a_tela_de_login_ganha_o_botao_do_provedor(self):
        _, corpo, _ = self.pega("/login", {"Accept": "text/html"})
        self.assertIn('href="/sso/oidc/iniciar"', corpo)
        self.assertIn('action="/login"', corpo)

    # -- gravar a configuração ----------------------------------------------

    def sessao_local(self):
        corpo = urllib.parse.urlencode(
            {"usuario": "admin", "senha": self.recuperacao}
        ).encode()
        _, cabecalhos = self.posta("/login", corpo)
        valor = self.sessao_do_cabecalho(cabecalhos)
        return {
            "Cookie": f"{sessao.NOME_DO_COOKIE}={valor}",
            "Origin": BASE,
            "Sec-Fetch-Site": "same-origin",
        }

    def grava_config(self, **campos):
        base = {
            "enabled": "oidc",
            "base_url": BASE,
            "issuer": ISSUER,
            "client_id": CLIENT_ID,
            "allowed_domains": "empresa.com",
            "allowed_emails": "",
            "usuario": "admin",
            "senha": self.recuperacao,
        }
        base.update(campos)
        return self.posta(
            "/acoes/sso", urllib.parse.urlencode(base).encode(), self.sessao_local()
        )

    def test_gravar_exige_a_senha_local_atual(self):
        """Sessão sequestrada não pode apontar o painel para um provedor hostil."""
        status, cabecalhos = self.grava_config(
            senha="senha-errada", issuer="https://provedor-hostil.invalid"
        )
        self.assertEqual(status, 303)
        self.assertIn("tom=danger", cabecalhos.get("Location", ""))
        config = sso.carregar(
            self.settings.get_prefs_path(), self.settings.get_sso_secret_path()
        )
        self.assertEqual(config.issuer, ISSUER, "a configuração não podia ter mudado")

    def test_gravar_sem_sessao_nao_passa(self):
        status, _ = self.posta(
            "/acoes/sso",
            urllib.parse.urlencode({"enabled": "oidc"}).encode(),
            {"Accept": "text/html"},
        )
        self.assertEqual(status, 302, "sem sessão, vai para o formulário")

    def test_lista_vazia_recusa_ligar(self):
        status, cabecalhos = self.grava_config(allowed_domains="", allowed_emails="")
        self.assertEqual(status, 303)
        self.assertIn("tom=danger", cabecalhos.get("Location", ""))

    def test_salvar_o_segredo_em_branco_mantem_o_anterior(self):
        """O campo nunca reexibe o valor; apagá-lo por isso desligaria o SSO."""
        self.grava_config(client_secret="")
        self.assertEqual(
            sso.ler_segredo(self.settings.get_sso_secret_path()), (SEGREDO, False)
        )
        config = sso.carregar(
            self.settings.get_prefs_path(), self.settings.get_sso_secret_path()
        )
        self.assertTrue(config.esta_ligado())

    def test_o_segredo_nunca_volta_para_a_tela(self):
        cabecalhos = self.sessao_local()
        cabecalhos["Accept"] = "text/html"
        _, corpo, _ = self.pega("/", cabecalhos)
        self.assertNotIn(SEGREDO, corpo)
        self.assertIn("••••••••", corpo)

    def test_desligar_pela_tela_pede_a_senha_e_mantem_o_resto(self):
        status, cabecalhos = self.grava_config(desligar="1")
        self.assertEqual(status, 303)
        self.assertIn("tom=success", cabecalhos.get("Location", ""))
        config = sso.carregar(
            self.settings.get_prefs_path(), self.settings.get_sso_secret_path()
        )
        self.assertFalse(config.esta_ligado())
        # O que estava configurado continua lá: religar não pede tudo de novo.
        self.assertEqual(config.issuer, ISSUER)

    def test_saml_sem_a_biblioteca_e_recusado_em_vez_de_ligado(self):
        if sso.saml_disponivel():
            self.skipTest("a biblioteca está instalada nesta máquina")
        status, cabecalhos = self.grava_config(
            enabled="saml", idp_entity_id="https://idp.invalid/metadata",
            idp_sso_url="https://idp.invalid/sso", idp_cert="MIIC",
        )
        self.assertEqual(status, 303)
        self.assertIn("tom=danger", cabecalhos.get("Location", ""))

    # -- freio ---------------------------------------------------------------

    def test_as_rotas_publicas_do_sso_tem_o_mesmo_teto_do_login(self):
        for _ in range(protecao.TENTATIVAS_POR_JANELA + 1):
            status, _, cabecalhos = self.pega("/sso/oidc/iniciar")
        self.assertEqual(status, 429)
        self.assertTrue(cabecalhos.get("Retry-After"), "o 429 precisa dizer quanto esperar")

    # -- descoberta ----------------------------------------------------------

    def test_documento_que_declara_outro_emissor_e_recusado(self):
        """Defesa contra mix-up: só há um emissor legítimo a comparar.

        O endereço responde 200 e entrega um documento bem formado — o que não
        bate é o `issuer` declarado dentro dele.
        """
        sso.esquece_descobertas()
        # O mesmo endereço, pelo caminho combinado, funciona.
        self.assertEqual(sso.descobrir(ISSUER)["issuer"], ISSUER)
        sso.esquece_descobertas()
        with self.assertRaises(sso.FalhaDeSSO):
            sso.descobrir(ISSUER + "/outro")

    def test_um_emissor_sem_tls_fora_do_loopback_e_recusado(self):
        sso.esquece_descobertas()
        with self.assertRaises(sso.FalhaDeSSO):
            sso.descobrir("http://provedor-externo.invalid")


@unittest.skipUnless(
    sso.saml_disponivel(),
    "python3-saml não está nesta imagem: a aba SAML aparece desabilitada, e é "
    "isso que os testes acima cobram. Com a biblioteca instalada (extra `saml` "
    "do pyproject), esta classe monta uma asserção ASSINADA de verdade e "
    "exercita o caminho inteiro.",
)
class AssercaoSamlAssinada(unittest.TestCase):
    """O fluxo SAML com uma asserção assinada por um provedor de mentira.

    Foi esta bancada que encontrou o defeito da porta vazia: sem porta na
    `base_url`, a biblioteca montava `https://painel.exemplo.com:/sso/saml/acs`
    -- com dois-pontos e sem número -- e recusava a própria asserção correta,
    dizendo que o destino não batia.
    """

    BASE_SP = "https://painel.exemplo.com"
    IDP = "https://idp.exemplo.com/metadata"

    @classmethod
    def setUpClass(cls):
        import shutil
        import subprocess

        if not shutil.which("openssl"):
            raise unittest.SkipTest("sem openssl para gerar o par de chaves")
        cls.tmp = tempfile.TemporaryDirectory()
        chave = os.path.join(cls.tmp.name, "idp.key")
        certificado = os.path.join(cls.tmp.name, "idp.crt")
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", chave, "-out", certificado, "-days", "2",
             "-subj", "/CN=idp.exemplo.com"],
            check=True, capture_output=True,
        )
        cls.chave = open(chave).read()
        cls.certificado = open(certificado).read()
        cls.certificado_nu = "".join(
            linha for linha in cls.certificado.splitlines()
            if "BEGIN" not in linha and "END" not in linha
        )
        cls.config = sso.ConfiguracaoSSO(
            provedor="saml", base_url=cls.BASE_SP, idp_entity_id=cls.IDP,
            idp_sso_url="https://idp.exemplo.com/sso",
            idp_cert=cls.certificado_nu, dominios=("empresa.com",),
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        sso.esquece_estado_de_fluxo()

    def instante(self, desvio=0):
        from datetime import datetime, timedelta, timezone

        return (datetime.now(timezone.utc) + timedelta(seconds=desvio)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    def resposta(self, pendente, *, audiencia=None, nao_depois=300, chave=None,
                 certificado=None):
        from onelogin.saml2.utils import OneLogin_Saml2_Utils

        chave = chave or self.chave
        certificado = certificado or self.certificado
        audiencia = audiencia or self.config.entity_id()
        acs = self.config.url_do_acs()
        identificador = "_a" + base64.b16encode(os.urandom(8)).decode()

        assercao = (
            f'<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"'
            f' ID="{identificador}" Version="2.0" IssueInstant="{self.instante()}">'
            f'<saml:Issuer>{self.IDP}</saml:Issuer>'
            f'<saml:Subject>'
            f'<saml:NameID Format="urn:oasis:names:tc:SAML:2.0:nameid-format:emailAddress">{EMAIL}</saml:NameID>'
            f'<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
            f'<saml:SubjectConfirmationData NotOnOrAfter="{self.instante(nao_depois)}"'
            f' Recipient="{acs}" InResponseTo="{pendente}"/>'
            f'</saml:SubjectConfirmation></saml:Subject>'
            f'<saml:Conditions NotBefore="{self.instante(-60)}"'
            f' NotOnOrAfter="{self.instante(nao_depois)}">'
            f'<saml:AudienceRestriction><saml:Audience>{audiencia}</saml:Audience>'
            f'</saml:AudienceRestriction></saml:Conditions>'
            f'<saml:AuthnStatement AuthnInstant="{self.instante()}" SessionIndex="{identificador}">'
            f'<saml:AuthnContext><saml:AuthnContextClassRef>'
            f'urn:oasis:names:tc:SAML:2.0:ac:classes:Password'
            f'</saml:AuthnContextClassRef></saml:AuthnContext></saml:AuthnStatement>'
            f'</saml:Assertion>'
        )
        assinada = OneLogin_Saml2_Utils.add_sign(assercao, chave, certificado)
        if isinstance(assinada, bytes):
            assinada = assinada.decode("utf-8")
        assinada = assinada.replace('<?xml version="1.0"?>', "").strip()

        envelope = (
            f'<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"'
            f' xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"'
            f' ID="_r{base64.b16encode(os.urandom(8)).decode()}" Version="2.0"'
            f' IssueInstant="{self.instante()}" Destination="{acs}"'
            f' InResponseTo="{pendente}">'
            f'<saml:Issuer>{self.IDP}</saml:Issuer>'
            f'<samlp:Status><samlp:StatusCode'
            f' Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>'
            f'{assinada}</samlp:Response>'
        )
        final = OneLogin_Saml2_Utils.add_sign(envelope, chave, certificado)
        if isinstance(final, bytes):
            final = final.decode("utf-8")
        return base64.b64encode(final.encode("utf-8")).decode("ascii")

    def pendente(self):
        identificador = sso.novo_id_de_requisicao()
        sso.registra_pendente(identificador)
        return identificador

    def test_o_caminho_feliz_devolve_o_email(self):
        pendente = self.pendente()
        carga = self.resposta(pendente)
        self.assertEqual(sso.processa_resposta_saml(self.config, carga, pendente), EMAIL)

    def test_assercao_fora_do_prazo_e_recusada(self):
        pendente = self.pendente()
        carga = self.resposta(pendente, nao_depois=-120)
        with self.assertRaises(sso.FalhaDeSSO):
            sso.processa_resposta_saml(self.config, carga, pendente)

    def test_audiencia_de_outro_servico_e_recusada(self):
        """Uma asserção legítima emitida para OUTRO serviço não vale aqui."""
        pendente = self.pendente()
        carga = self.resposta(pendente, audiencia="https://outro-servico.exemplo/metadata")
        with self.assertRaises(sso.FalhaDeSSO):
            sso.processa_resposta_saml(self.config, carga, pendente)

    def test_assinatura_de_outra_chave_e_recusada(self):
        import subprocess

        outra = os.path.join(self.tmp.name, "outra.key")
        outro_cert = os.path.join(self.tmp.name, "outra.crt")
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", outra, "-out", outro_cert, "-days", "2",
             "-subj", "/CN=impostor.exemplo.com"],
            check=True, capture_output=True,
        )
        pendente = self.pendente()
        carga = self.resposta(
            pendente, chave=open(outra).read(), certificado=open(outro_cert).read()
        )
        with self.assertRaises(sso.FalhaDeSSO):
            sso.processa_resposta_saml(self.config, carga, pendente)

    def test_a_mesma_assercao_nao_entra_duas_vezes(self):
        """Este controle é NOSSO: a biblioteca não guarda IDs já consumidos."""
        pendente = self.pendente()
        carga = self.resposta(pendente)
        self.assertEqual(sso.processa_resposta_saml(self.config, carga, pendente), EMAIL)
        sso.registra_pendente(pendente)
        with self.assertRaises(sso.FalhaDeSSO):
            sso.processa_resposta_saml(self.config, carga, pendente)

    def test_o_metadata_do_sp_sai_sem_quebrar(self):
        """A biblioteca devolve str ou bytes conforme o SP tenha certificado."""
        self.assertIn("EntityDescriptor", sso.metadata_do_sp(self.config))


if __name__ == "__main__":
    unittest.main()
