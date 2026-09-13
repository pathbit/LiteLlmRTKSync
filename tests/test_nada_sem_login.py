"""Nada do painel responde sem login, exceto o que precisa ser público.

O painel lê o banco do gateway e mostra a saúde das credenciais. Uma rota que
escape da exigência de sessão entrega isso a quem chegar — e o jeito de uma rota
escapar não é alguém decidir abri-la, é alguém acrescentar uma rota nova e
esquecer de protegê-la. Por isso esta guarda enumera o despacho do servidor e
cobra o inverso: toda rota é fechada, menos as quatro que têm motivo declarado.

As quatro públicas, e por quê:

- `/healthz`     o healthcheck do Docker roda sem credencial nenhuma;
- `/login`       exigir sessão para exibir o formulário que cria a sessão é um
                 círculo fechado;
- `/robots.txt`  um rastreador não tem como autenticar, e a regra só serve se
                 ele conseguir lê-la;
- `/credenciais-atualizadas`  é servida no instante seguinte à troca de senha,
                 quando o navegador ainda guarda a anterior; exigir a nova ali
                 daria um 401 cru logo depois de a troca ter dado certo.

E as quatro do acesso federado, acrescentadas com o motivo declarado:

- `/sso/oidc/iniciar` e `/sso/oidc/callback`, `/sso/saml/iniciar` e
  `/sso/saml/acs`: a ida ao provedor de identidade e a volta dele acontecem sem
  sessão — é a sessão que elas existem para criar. Quem chama `/callback` e o
  `/acs` é o provedor, que não tem cookie nosso para apresentar.

  Elas não ficam abertas por isso: as quatro passam pelo MESMO teto por endereço
  do formulário de login (`protecao.registra_tentativa`, 429 com `Retry-After`),
  e nenhuma delas responde nada enquanto não houver provedor configurado E
  ligado — sem configuração, `rota_existe` as trata como inexistentes e o
  servidor devolve 404 pelo mesmo caminho de qualquer rota que nunca existiu.

  `/sso/saml/metadata` NÃO está aqui de propósito: ela é servida depois do
  `require_auth()`. O operador baixa a descrição do serviço já autenticado, e
  não há pressa nenhuma em publicá-la — cada rota pública a mais é superfície a
  mais.
"""

import pathlib
import re
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
SERVIDOR = RAIZ / "src" / "litellm_rtksync" / "web.py"

PUBLICAS = {
    "/healthz", "/login", "/robots.txt", "/credenciais-atualizadas",
    # Acesso federado: a ida ao provedor e a volta dele. Ver o motivo por
    # extenso no topo do arquivo.
    "/sso/oidc/iniciar", "/sso/oidc/callback", "/sso/saml/iniciar",
}

# Rotas citadas no despacho: `route == "/x"`, `path == "/x"`, startswith("/x")
ROTA = re.compile(r'(?:route|path|rota_inicial)\s*==\s*"(/[a-z0-9/_-]*)"')
ROTA_PREFIXO = re.compile(r'startswith\(\s*"(/[a-z0-9/_-]+)"')


class NadaRespondeSemLogin(unittest.TestCase):
    def setUp(self):
        self.fonte = SERVIDOR.read_text(encoding="utf-8")

    def test_toda_rota_publica_esta_declarada_aqui(self):
        """Uma rota nova servida antes do require_auth tem de passar por aqui."""
        # Trecho entre o início do do_GET e a primeira exigência de sessão: é
        # exatamente o que o servidor entrega sem olhar credencial.
        inicio = self.fonte.find("def do_GET")
        fim = self.fonte.find("require_auth()", inicio)
        self.assertGreater(fim, inicio, "não achei a exigência de sessão no do_GET")
        antes_do_login = self.fonte[inicio:fim]

        servidas = set(ROTA.findall(antes_do_login))
        fora_da_lista = servidas - PUBLICAS
        self.assertEqual(
            fora_da_lista,
            set(),
            "estas rotas são servidas ANTES de exigir sessão e não estão na lista "
            f"de públicas: {sorted(fora_da_lista)} -- se a rota deve mesmo ser "
            "pública, acrescente-a à lista COM o motivo; se não, mova-a para "
            "depois do require_auth",
        )

    def test_o_post_tambem_exige_sessao(self):
        """O POST muda estado: escapar da sessão ali é pior que no GET."""
        inicio = self.fonte.find("def do_POST")
        fim = self.fonte.find("require_auth()", inicio)
        self.assertGreater(fim, inicio, "o do_POST precisa exigir sessão")
        antes = self.fonte[inicio:fim]
        servidas = set(ROTA.findall(antes))
        # /login e /logout são os únicos POST sem sessão: um a cria, o outro a
        # destrói, e exigir sessão para sair é prender quem quer ir embora.
        #
        # `/sso/saml/acs` entra ao lado deles porque quem o dispara é o PROVEDOR
        # de identidade, de outra origem e ainda sem sessão — é este POST que
        # cria a sessão. Ele também não passa pela guarda de mesma origem, e não
        # precisa: a autenticidade vem da assinatura XML e do `InResponseTo`
        # conferido contra o conjunto de pendentes do servidor, não do cabeçalho
        # `Origin`. O corpo continua sendo lido DENTRO do handler, nunca no
        # despacho — é o que o teste seguinte cobra.
        fora = servidas - {"/login", "/logout", "/sso/saml/acs"}
        self.assertEqual(
            fora,
            set(),
            f"POST sem exigir sessão: {sorted(fora)}",
        )

    def test_a_exigencia_de_sessao_vem_antes_do_corpo(self):
        """Ler o corpo antes de autenticar aceita carga de quem não entrou."""
        inicio = self.fonte.find("def do_POST")
        trecho = self.fonte[inicio : inicio + 2000]
        pos_auth = trecho.find("require_auth()")
        pos_corpo = trecho.find("Content-Length")
        if pos_corpo >= 0:
            self.assertLess(
                pos_auth,
                pos_corpo,
                "o corpo do POST é lido antes de a sessão ser exigida",
            )


if __name__ == "__main__":
    unittest.main()
