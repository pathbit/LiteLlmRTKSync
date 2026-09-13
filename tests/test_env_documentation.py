"""O .env.example precisa cobrir toda variavel que o codigo realmente le.

Documentacao defasada nao e detalhe: quem implanta copia o exemplo, nao le o
codigo. Uma variavel que o programa consulta e o exemplo nao cita e uma opcao
que so existe para quem leu a fonte -- e foi exatamente o que o usuario
encontrou ao abrir o arquivo.

Este teste compara os dois conjuntos e falha nomeando a diferenca, entao a
defasagem aparece no CI em vez de aparecer em producao.
"""

import os
import pathlib
import re
import unittest

import litellm_rtksync

RAIZ_PACOTE = pathlib.Path(litellm_rtksync.__file__).parent
RAIZ_REPO = RAIZ_PACOTE.parent.parent
ENV_EXAMPLE = RAIZ_REPO / ".env.example"

# Variaveis da stack de teste (docker-compose.test.yml), lidas pelo Postgres e
# pelo proprio LiteLLM -- nao por este programa.
#
# As chaves de provedor entram na mesma categoria: o compose as repassa ao
# container do PROXY, que e quem resolve `os.environ/NOME` quando um modelo
# aponta para o ambiente. O sincronizador nunca as le -- e nao deve: ele relata
# de onde vem a credencial, nunca qual e ela. Quem tambem as le e a bancada de
# teste (tools/popula_bancada.py), que roda fora do pacote.
DO_GATEWAY = {
    "POSTGRES_PASSWORD",
    "LITELLM_SALT_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "OPENROUTER_API_KEY",
}

# Lidas pelo COMPOSE, não pelo código Python: alimentam os serviços opcionais de
# acesso remoto (perfis `tunel` e `tailnet`). Precisam estar anunciadas no
# exemplo -- é lá que o operador descobre que existem -- mas nenhum os.environ
# daqui as procura, e é isso que a varredura acima mede.
DO_COMPOSE = {"TUNNEL_TOKEN", "TS_AUTHKEY"}

# Credenciais que este proxy APRESENTA aos gateways irmaos (9Router e
# OmniRoute) quando usa um deles como provedor. Mesma categoria das chaves de
# provedor acima, e pelo mesmo motivo: quem as le e a ferramenta de
# `tools/registra_gateways.py`, que roda fora do pacote e as entrega ao proxy
# uma unica vez como credencial nomeada. O sincronizador nunca as le -- ele
# relata de onde vem a credencial, nunca qual e ela.
DOS_GATEWAYS_IRMAOS = {"NINEROUTER_API_KEY", "OMNIROUTE_API_KEY"}

LEITURA = re.compile(r'os\.(?:environ\.get|getenv)\(\s*["\']([A-Z0-9_]+)["\']')
INDICE = re.compile(r'os\.environ\[\s*["\']([A-Z0-9_]+)["\']')
# O config nao chama os.environ diretamente em todo lugar: ele passa por
# auxiliares que fazem a conversao de tipo. Sem enxergar essas chamadas, o teste
# acusaria como "documentada e nao lida" toda variavel que passa por elas.
AUXILIARES = re.compile(r'\b_(?:flag|inteiro_opcional|decimal_opcional)\(\s*["\']([A-Z0-9_]+)["\']')
DECLARACAO = re.compile(r'^#?\s*([A-Z0-9_]+)=', re.M)


def variaveis_lidas():
    encontradas = set()
    for caminho in RAIZ_PACOTE.rglob("*.py"):
        texto = caminho.read_text(encoding="utf-8")
        encontradas |= set(LEITURA.findall(texto))
        encontradas |= set(INDICE.findall(texto))
        encontradas |= set(AUXILIARES.findall(texto))
    return encontradas


def variaveis_documentadas():
    return set(DECLARACAO.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))


class TestDocumentacaoDeAmbiente(unittest.TestCase):
    def test_the_example_file_exists(self):
        self.assertTrue(ENV_EXAMPLE.is_file(), f"esperado em {ENV_EXAMPLE}")

    def test_every_variable_the_code_reads_is_documented(self):
        faltando = sorted(variaveis_lidas() - variaveis_documentadas())
        self.assertEqual(
            faltando, [],
            "variaveis lidas pelo codigo e ausentes do .env.example: " + ", ".join(faltando),
        )

    def test_the_example_documents_nothing_the_code_ignores(self):
        sobrando = sorted(
            variaveis_documentadas() - variaveis_lidas()
            - DO_GATEWAY - DO_COMPOSE - DOS_GATEWAYS_IRMAOS
        )
        self.assertEqual(
            sobrando, [],
            "variaveis no .env.example que programa nenhum le: " + ", ".join(sobrando),
        )

    def test_the_example_carries_no_credential_value(self):
        """Valor publicado em arquivo de exemplo e credencial publica."""
        texto = ENV_EXAMPLE.read_text(encoding="utf-8")
        suspeitos = re.findall(
            r'^(?!#)\s*([A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|KEY)[A-Z0-9_]*)=(.+)$',
            texto, re.M,
        )
        # Interruptor nao e segredo: um nome que casa com KEY so por conter a
        # palavra, e cujo valor e liga/desliga, diz o que o programa deve fazer
        # -- nao qual e a chave. Sem esta excecao o teste acusaria de credencial
        # publicada qualquer interruptor assim que ele aparecesse no exemplo.
        INTERRUPTORES = {"true", "false", "0", "1", "yes", "no", "on", "off"}
        com_valor = [
            f"{nome}={valor.strip()}"
            for nome, valor in suspeitos
            if valor.strip() and valor.strip().lower() not in INTERRUPTORES
        ]
        self.assertEqual(com_valor, [], "campo de segredo preenchido no exemplo: " + ", ".join(com_valor))


if __name__ == "__main__":
    unittest.main()
