"""Validação da imagem Docker do LiteLlmRTKSync usando Testcontainers.

Garante que a imagem Docker gerada:
1. Inicializa sem falhas com as configurações padrão.
2. Expõe o dashboard web na porta 9090.
3. Responde requisições HTTP em /login com código 200 e página HTML válida.
4. Responde no endpoint /healthz com o cabeçalho e corpo documentados.
5. Permite executar a CLI (litellmrtksync --help) com sucesso dentro do container.
"""

from contextlib import closing
import os
import sys
import time
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKER_IMAGE_PADRAO = "litellmrtksync:test"


class TestLiteLlmRTKSyncContainer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        requested_image = os.environ.get("TEST_DOCKER_IMAGE")
        cls.imagem = requested_image if requested_image is not None else DOCKER_IMAGE_PADRAO
        try:
            import docker
            from testcontainers.core.container import DockerContainer

            cls.container_type = DockerContainer
            if not cls.imagem.strip():
                raise ValueError("TEST_DOCKER_IMAGE must name a locally available image")
            with closing(docker.from_env()) as client:
                client.ping()
                try:
                    client.images.get(cls.imagem)
                except docker.errors.ImageNotFound:
                    if requested_image is not None:
                        raise
                    client.images.build(path=RAIZ, tag=cls.imagem, rm=True)
        except Exception as error:
            message = f"Docker test setup failed for {cls.imagem!r}: {error}"
            if requested_image is not None:
                raise RuntimeError(message) from error
            raise unittest.SkipTest(message) from error

    def test_container_web_dashboard_e_cli(self):
        candidate = self.container_type(self.imagem).with_exposed_ports(9090)
        candidate.with_env("LITELLM_URL", "http://127.0.0.1:1")
        candidate.with_env("CREDENTIAL_CHECK_ENABLED", "0")
        with candidate as container:
            host = container.get_container_host_ip()
            porta = container.get_exposed_port(9090)
            url_login = f"http://{host}:{porta}/login"
            url_healthz = f"http://{host}:{porta}/healthz"

            # 1. Aguarda o servidor web aceitar conexões (até 15s)
            conectou = False
            ultimo_erro = None
            for _ in range(15):
                time.sleep(1)
                try:
                    with urllib.request.urlopen(url_login, timeout=2) as resp:
                        if resp.status == 200:
                            conectou = True
                            break
                except Exception as e:
                    ultimo_erro = e

            self.assertTrue(conectou, f"Não foi possível conectar ao dashboard web na porta {porta}: {ultimo_erro}")

            # 2. Valida resposta do endpoint /login
            with urllib.request.urlopen(url_login, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                cabecalhos = dict(resp.getheaders())
                self.assertIn("text/html", cabecalhos.get("Content-Type", ""))
                corpo = resp.read().decode("utf-8")
                self.assertIn("LiteLlmRTKSync", corpo)
                self.assertIn("<!DOCTYPE html>", corpo)

            # 3. Valida resposta do endpoint /healthz
            try:
                with urllib.request.urlopen(url_healthz, timeout=5) as resp:
                    status_healthz = resp.status
                    corpo_healthz = resp.read().decode("utf-8")
                    cabecalho_server = resp.headers.get("Server", "")
            except urllib.error.HTTPError as e:
                with e:
                    status_healthz = e.code
                    corpo_healthz = e.read().decode("utf-8")
                    cabecalho_server = e.headers.get("Server", "")

            self.assertEqual(status_healthz, 503)
            self.assertEqual(cabecalho_server, "LiteLlmRTKSync")
            self.assertIn("LITELLM_UNREACHABLE", corpo_healthz)

            # 4. Valida execução da CLI dentro do container
            codigo_saida, saida = container.exec(["litellmrtksync", "--help"])
            self.assertEqual(codigo_saida, 0, f"Comando litellmrtksync --help falhou com código {codigo_saida}: {saida}")
            texto_saida = saida.decode("utf-8")
            self.assertIn("litellmrtksync", texto_saida.lower())
            self.assertIn("--daemon", texto_saida)


class ContainerPrerequisiteTests(unittest.TestCase):
    def test_optional_run_can_skip_missing_dependencies(self):
        with patch.dict(os.environ, clear=True), patch.dict(sys.modules, {"docker": None}):
            with self.assertRaises(unittest.SkipTest):
                TestLiteLlmRTKSyncContainer.setUpClass()

    def test_requested_image_cannot_skip_missing_dependencies(self):
        with patch.dict(os.environ, {"TEST_DOCKER_IMAGE": "candidate:test"}), patch.dict(sys.modules, {"docker": None}):
            with self.assertRaisesRegex(RuntimeError, "candidate:test"):
                TestLiteLlmRTKSyncContainer.setUpClass()


if __name__ == "__main__":
    unittest.main()
