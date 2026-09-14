"""Cliente da API administrativa: paginação, erros e o que ele nunca faz.

Sem rede: cada teste injeta um `opener` que devolve a resposta combinada. O que
importa aqui é o comportamento nas bordas — página parcial, rota ausente numa
versão antiga, master key recusada, proxy fora do ar — porque é onde um cliente
mal escrito perde dado em silêncio.
"""

import io
import json
import unittest
import urllib.error

from litellm_rtksync.gateway import GatewayClient, GatewayError


class RespostaFalsa(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def opener_de(mapa, registro=None):
    """Devolve um opener que responde conforme o caminho pedido."""

    def abrir(request, timeout=None):
        caminho = request.full_url.split("://", 1)[1].split("/", 1)[1]
        caminho = "/" + caminho
        if registro is not None:
            registro.append((request.full_url, dict(request.header_items())))
        for prefixo, resposta in mapa.items():
            if caminho.startswith(prefixo.lstrip("/")) or caminho.startswith(prefixo):
                if isinstance(resposta, int):
                    raise urllib.error.HTTPError(request.full_url, resposta, "erro", {}, None)
                return RespostaFalsa(json.dumps(resposta).encode())
        raise urllib.error.HTTPError(request.full_url, 404, "nao encontrado", {}, None)

    return abrir


class TestPaginacao(unittest.TestCase):
    def test_every_page_is_collected_not_only_the_first(self):
        paginas = {
            1: {"keys": [{"key_alias": "a"}, {"key_alias": "b"}], "total_pages": 2},
            2: {"keys": [{"key_alias": "c"}], "total_pages": 2},
        }

        def abrir(request, timeout=None):
            numero = int(request.full_url.split("page=")[1].split("&")[0])
            return RespostaFalsa(json.dumps(paginas[numero]).encode())

        cliente = GatewayClient("http://proxy:4000", "mk", opener=abrir)
        chaves = cliente.list_keys()
        self.assertEqual([k["key_alias"] for k in chaves], ["a", "b", "c"])

    def test_an_empty_page_ends_the_walk(self):
        cliente = GatewayClient("http://proxy:4000", "mk",
                                opener=opener_de({"/key/list": {"keys": [], "total_pages": 9}}))
        self.assertEqual(cliente.list_keys(), [])

    def test_string_entries_are_dropped_instead_of_crashing(self):
        """Sem return_full_object a API devolve strings; nao sao chaves utilizaveis."""
        cliente = GatewayClient(
            "http://proxy:4000", "mk",
            opener=opener_de({"/key/list": {"keys": ["sk-abc", {"key_alias": "a"}], "total_pages": 1}}),
        )
        self.assertEqual(cliente.list_keys(), [{"key_alias": "a"}])


class TestErros(unittest.TestCase):
    def test_a_rejected_master_key_says_so(self):
        cliente = GatewayClient("http://proxy:4000", "errada", opener=opener_de({"/key/list": 401}))
        with self.assertRaises(GatewayError) as ctx:
            cliente.list_keys()
        self.assertIn("master key", str(ctx.exception))
        self.assertEqual(ctx.exception.status, 401)

    def test_an_unreachable_proxy_is_reported_as_such(self):
        def abrir(request, timeout=None):
            raise urllib.error.URLError("conexao recusada")

        cliente = GatewayClient("http://proxy:4000", "mk", opener=abrir)
        with self.assertRaises(GatewayError) as ctx:
            cliente.list_keys()
        self.assertIn("inacessível", str(ctx.exception))

    def test_health_answers_false_instead_of_raising(self):
        def abrir(request, timeout=None):
            raise urllib.error.URLError("fora do ar")

        self.assertFalse(GatewayClient("http://proxy:4000", opener=abrir).health())

    def test_an_older_version_without_the_credentials_route_returns_empty(self):
        """404 em /credentials significa versao anterior a tabela, nao falha."""
        cliente = GatewayClient("http://proxy:4000", "mk", opener=opener_de({"/credentials": 404}))
        self.assertEqual(cliente.list_credentials(), [])

    def test_a_non_json_body_is_reported_clearly(self):
        def abrir(request, timeout=None):
            return RespostaFalsa(b"<html>gateway timeout</html>")

        cliente = GatewayClient("http://proxy:4000", "mk", opener=abrir)
        with self.assertRaises(GatewayError) as ctx:
            cliente.list_teams()
        self.assertIn("não é JSON", str(ctx.exception))


class TestContrato(unittest.TestCase):
    def test_the_master_key_travels_as_a_bearer_header_never_in_the_url(self):
        registro = []
        cliente = GatewayClient("http://proxy:4000", "sk-master-secreta",
                                opener=opener_de({"/team/list": []}, registro))
        cliente.list_teams()
        url, cabecalhos = registro[0]
        self.assertNotIn("sk-master-secreta", url)
        self.assertIn("sk-master-secreta", str(cabecalhos))

    def test_a_trailing_slash_in_the_base_url_does_not_double_up(self):
        registro = []
        cliente = GatewayClient("http://proxy:4000/", "mk",
                                opener=opener_de({"/team/list": []}, registro))
        cliente.list_teams()
        self.assertNotIn("//team", registro[0][0].replace("http://", ""))

    def test_the_client_only_ever_issues_get(self):
        """Este sincronizador relata e valida; alterar limite e do operador."""
        metodos = []

        def abrir(request, timeout=None):
            metodos.append(request.get_method())
            return RespostaFalsa(b"{}")

        cliente = GatewayClient("http://proxy:4000", "mk", opener=abrir)
        cliente.health()
        cliente.list_teams()
        cliente.list_models()
        self.assertEqual(set(metodos), {"GET"})


if __name__ == "__main__":
    unittest.main()
