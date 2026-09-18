"""Validação da imagem Docker do LiteLlmRTKSync usando Testcontainers.

Garante que a imagem Docker gerada:
1. Inicializa sem falhas com as configurações padrão.
2. Expõe o dashboard web na porta 9090.
3. Responde requisições HTTP em /login com código 200 e página HTML válida.
4. Responde no endpoint /healthz com o cabeçalho e corpo documentados.
5. Permite executar a CLI (litellmrtksync --help) com sucesso dentro do container.
"""

import os
import sys
import time
import unittest
import urllib.request
import urllib.error

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKER_IMAGE_PADRAO = "litellmrtksync:test"


def docker_disponivel() -> bool:
    try:
        import docker
        cliente = docker.from_env()
        cliente.ping()
        return True
    except Exception:
        return False


def obter_ou_construir_imagem(nome_imagem: str) -> str:
    import docker

    cliente = docker.from_env()
    imagens = cliente.images.list(name=nome_imagem)
    if imagens:
        return nome_imagem

    # Constrói localmente se não existir pré-construída
    dockerfile = os.path.join(RAIZ, "Dockerfile")
    if os.path.exists(dockerfile):
        imagem, _ = cliente.images.build(path=RAIZ, tag=nome_imagem, rm=True)
        return nome_imagem

    raise RuntimeError(f"Imagem {nome_imagem} não encontrada e Dockerfile não localizado em {RAIZ}")


class TestLiteLlmRTKSyncContainer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import testcontainers
            import docker
        except ImportError:
            raise unittest.SkipTest("testcontainers ou docker não estão instalados")

        if not docker_disponivel():
            raise unittest.SkipTest("Docker daemon não está disponível ou acessível")

        cls.imagem = os.environ.get("TEST_DOCKER_IMAGE", DOCKER_IMAGE_PADRAO)
        try:
            cls.imagem = obter_ou_construir_imagem(cls.imagem)
        except Exception as e:
            raise unittest.SkipTest(f"Falha ao obter ou construir imagem Docker: {e}")

    def test_container_web_dashboard_e_cli(self):
        from testcontainers.core.container import DockerContainer

        with DockerContainer(self.imagem).with_exposed_ports(9090) as container:
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
                status_healthz = e.code
                corpo_healthz = e.read().decode("utf-8")
                cabecalho_server = e.headers.get("Server", "")

            # Sem LiteLLM proxy rodando no host do container, retorna 503 LITELLM_UNREACHABLE ou 200 OK
            self.assertIn(status_healthz, (200, 503))
            self.assertEqual(cabecalho_server, "LiteLlmRTKSync")
            self.assertTrue(
                "OK" in corpo_healthz or "LITELLM_UNREACHABLE" in corpo_healthz,
                f"Corpo inesperado do healthz: {corpo_healthz}",
            )

            # 4. Valida execução da CLI dentro do container
            codigo_saida, saida = container.exec(["litellmrtksync", "--help"])
            self.assertEqual(codigo_saida, 0, f"Comando litellmrtksync --help falhou com código {codigo_saida}: {saida}")
            texto_saida = saida.decode("utf-8")
            self.assertIn("litellmrtksync", texto_saida.lower())
            self.assertIn("--daemon", texto_saida)


if __name__ == "__main__":
    unittest.main()
