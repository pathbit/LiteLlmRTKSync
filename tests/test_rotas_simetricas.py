"""Os três painéis servem exatamente as mesmas rotas.

Uma rota que existe num irmão e não nos outros é o jeito mais silencioso de os
três se separarem: nada quebra, a suíte fica verde, e a diferença só aparece
quando alguém segue a documentação de um produto usando o outro.

Aconteceu de verdade com o SAML: a implementação foi portada para o `sso.py` dos
três -- as funções `saml_disponivel`, `url_de_ida_saml` e `processa_resposta_saml`
existem em todos -- mas as rotas `/sso/saml/*` ficaram só no LiteLlmRTKSync.
Resultado: dois produtos com o código de federação inteiro e nenhuma porta para
entrar nele. O `sso.py` convergiu, o `web.py` não, e nenhum teste percebeu.

Este teste lê o conjunto de rotas declarado por cada `web.py` e exige que os três
sejam iguais. Ele não roda o servidor: compara o contrato, que é o que diverge.
"""

import pathlib
import re
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
IRMAOS = ("9RTKSync", "OminiRTkSync", "LiteLlmRTKSync")

# Rotas que o produto TEM de servir, independentemente do gateway por trás.
# Entram aqui as que algum pedido explícito criou -- assim o teste também
# documenta por que cada uma existe.
ROTAS_OBRIGATORIAS = {
    "/": "a tela inicial",
    "/login": "o formulário próprio, que substituiu o Basic Auth",
    "/logout": "sair de verdade, limpando a sessão",
    "/healthz": "a sonda que o compose usa",
    "/robots.txt": "inibir indexação",
    "/favicon.ico": "o ícone da aba",
    "/logs": "o log persistente",
    "/sso/oidc/iniciar": "a ida ao provedor OIDC",
    "/sso/oidc/callback": "a volta do provedor OIDC",
    "/sso/saml/iniciar": "a ida ao provedor SAML2",
    "/sso/saml/acs": "o Assertion Consumer Service, a volta do SAML2",
    "/sso/saml/metadata": "o metadata que o provedor SAML2 consome",
}


def pacote_de(raiz):
    origem = raiz / "src"
    if not origem.is_dir():
        return None
    for candidato in sorted(origem.iterdir()):
        if (candidato / "identidade.py").is_file():
            return candidato
    return None


def rotas_de(caminho_do_web):
    """As rotas que o servidor declara, sem rodá-lo.

    Lê só onde rota é DECLARADA -- o conjunto `ROTAS_CONHECIDAS` e as comparações
    de caminho no despacho. Pegar qualquer literal `"/..."` do arquivo era
    frágil: uma URL de CDN numa docstring ou um `log("acesso a /logs negado")`
    viraria rota, e o teste acusaria divergência que não existe.
    """
    texto = caminho_do_web.read_text(encoding="utf-8")
    rotas = set()

    # 1. O conjunto declarado, que é o que decide entre servir e devolver 404.
    bloco = re.search(r"ROTAS_CONHECIDAS\s*=\s*\{(.*?)\}", texto, re.S)
    if bloco:
        rotas |= set(re.findall(r'"(/[a-z0-9/_.-]*)"', bloco.group(1)))

    # 2. O despacho: `if caminho == "/x"` / `elif self.path == "/x"` e o
    #    startswith que trata uma família inteira.
    rotas |= set(re.findall(r'(?:path|caminho|rota)\s*==\s*"(/[a-z0-9/_.-]*)"', texto))
    rotas |= set(re.findall(r'startswith\(\s*"(/[a-z0-9/_.-]*)"', texto))

    # 3. Os prefixos declarados cobrem tudo abaixo deles.
    bloco = re.search(r"PREFIXOS_CONHECIDOS\s*=\s*\((.*?)\)", texto, re.S)
    if bloco:
        rotas |= set(re.findall(r'"(/[a-z0-9/_.-]*)"', bloco.group(1)))

    return rotas


def coberta_por_prefixo(rota, rotas):
    """Uma rota servida por um prefixo declarado conta como servida."""
    return any(p.endswith("/") and rota.startswith(p) for p in rotas)


class OsTresServemAsMesmasRotas(unittest.TestCase):
    def setUp(self):
        self.meu_pacote = pacote_de(RAIZ)
        self.assertIsNotNone(self.meu_pacote, "este repositório não tem src/<pacote>/identidade.py")
        self.minhas_rotas = rotas_de(self.meu_pacote / "web.py")

    def test_serve_todas_as_rotas_obrigatorias(self):
        for rota, motivo in sorted(ROTAS_OBRIGATORIAS.items()):
            with self.subTest(rota=rota):
                self.assertTrue(
                    rota in self.minhas_rotas or coberta_por_prefixo(rota, self.minhas_rotas),
                    f"{rota} não é servida aqui -- ela existe para {motivo}. Se o "
                    f"código por trás dela já está no repositório e só falta a "
                    f"rota, o produto tem a funcionalidade e nenhuma porta para "
                    f"ela: foi o que aconteceu com o SAML2.",
                )

    def test_nenhum_irmao_serve_rota_que_os_outros_nao_servem(self):
        """Simetria nos dois sentidos: sobrar rota também é divergir."""
        for irmao in IRMAOS:
            raiz_do_irmao = RAIZ.parent / irmao
            if raiz_do_irmao.resolve() == RAIZ or not raiz_do_irmao.is_dir():
                continue
            pacote_do_irmao = pacote_de(raiz_do_irmao)
            if pacote_do_irmao is None:
                continue
            with self.subTest(irmao=irmao):
                dele = rotas_de(pacote_do_irmao / "web.py")
                so_minhas = sorted(self.minhas_rotas - dele)
                so_dele = sorted(dele - self.minhas_rotas)
                self.assertEqual(
                    (so_minhas, so_dele), ([], []),
                    f"as rotas divergem de {irmao}.\n"
                    f"  só em {RAIZ.name}: {so_minhas}\n"
                    f"  só em {irmao}: {so_dele}\n"
                    "Rota que existe num e não no outro separa os produtos sem "
                    "quebrar nada -- é assim que a simetria some sem ninguém ver.",
                )


if __name__ == "__main__":
    unittest.main()
