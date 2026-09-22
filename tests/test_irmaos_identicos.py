"""Os módulos comuns são o MESMO texto nos três irmãos, byte a byte.

9RTKSync, OminiRTkSync e LiteLlmRTKSync são a mesma aplicação. O que muda entre
eles é cor, nome, logo, identidade visual e a quem cada um se conecta — e isso
mora inteiro em `identidade.py` (o que é identidade) e em `gateway.py` (o que é
domínio do gateway). Todo o resto tem de ser o mesmo arquivo, ou "são quase
clones" vira uma promessa que ninguém consegue conferir.

São DUAS medidas aqui, e elas não usam a mesma régua -- vale saber qual é qual
antes de ler um número:

- `IDENTICOS` compara byte a byte, sem NORMALIZAR nada: zero substituição. Isso
  só é possível porque (a) todo import intra-pacote é relativo nos três, de modo
  que o nome do pacote não aparece no texto dos módulos, e (b) depois do cânone,
  cor, nome de produto e nome de gateway só existem em `identidade.py`, que não
  está na lista. Um teste que normaliza é um teste que aceita divergência: a
  cada `re.sub` a mais, um agente ganha uma brecha. Se algum dia for preciso
  normalizar alguma coisa, a resposta certa é mover aquilo para `identidade.py`;
- `TETOS_DE_DIVERGENCIA` é a catraca dos módulos que AINDA estão convergindo, e
  essa, sim, normaliza a identidade antes de medir -- senão o número seria
  dominado pelo que muda de propósito.

Por isso zerar a catraca não promove um módulo sozinho: pode sobrar um `9Router`
literal que a normalização escondeu. O módulo só entra em `IDENTICOS` quando a
divergência normalizada chega a zero E os termos de identidade sumiram do texto
bruto.

Num clone isolado, sem os irmãos ao lado, o teste pula em vez de reprovar —
ninguém deve precisar dos três repositórios para rodar a suíte de um.

A lista cresce a cada módulo que converge. Ela é curta de propósito: só entra
aqui o que JÁ é idêntico, para que o teste fique verde desde o primeiro dia e
qualquer regressão apareça imediatamente, e não no fim de uma migração.
"""

import difflib
import pathlib
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent

# Nome do diretório de cada irmão ao lado deste repositório.
IRMAOS = ("9RTKSync", "OminiRTkSync", "LiteLlmRTKSync")

# Módulos que têm de ser o mesmo texto nos três, relativos a `src/<pacote>/`.
IDENTICOS = (
    "auth.py",
    "credential_check.py",
    "i18n.py",
    "logs.py",
    "paginacao.py",
    "prefs.py",
    "protecao.py",
    "sessao.py",
    "sso.py",
)

# Módulos que AINDA divergem, com o teto medido em 13/09/2026. A convergência
# é trabalho de várias rodadas, e uma lista que só aceitasse zero deixaria a
# guarda vermelha por semanas -- vermelho permanente é ruído, e ruído se ignora.
#
# Então isto é uma catraca: cada número só pode DESCER. Uma rodada que aumente a
# divergência reprova na hora, mesmo que a suíte inteira esteja verde, porque
# foi assim que os três se separaram em primeiro lugar -- ninguém percebeu.
#
# Quando um módulo chega a zero, ele sai daqui e entra em IDENTICOS. Esse é o
# fim da linha: a lista de baixo vazia e a de cima com tudo.
#
# A medida normaliza o que DEVE mudar (nome do pacote, do produto, prefixo de
# container e nome do gateway) antes de comparar; o que sobra é divergência de
# verdade.
#
# E a medida é feita em ORDEM CANÔNICA: o par é sempre comparado na direção
# dada por IRMAOS, nunca "eu contra ele". Isso não é preciosismo -- o difflib
# indexa a segunda sequência e devolve número diferente quando se troca a ordem
# (o mesmo sso.py deu 1354 de um lado e 1388 do outro). Sem ordem fixa, a suíte
# do 9RTKSync passava e a do LiteLlmRTKSync reprovava com os arquivos idênticos,
# e o teto viraria uma régua que estica conforme quem mede.
#
# Medidos em 13/09/2026, no par que mais diverge de cada módulo. Sem folga de
# propósito: um teto com margem é permissão para piorar um pouco, e "um pouco"
# foi como se chegou a mil linhas de diferença.
# O teto de `web.py` subiu de 759 para 765 numa unica ocasiao, e a razao fica
# registrada aqui porque a catraca existe justamente para exigir isso: seis
# linhas entraram no LiteLlmRTKSync para corrigir uma tela que MENTIA. Com o
# gateway fora do ar, `list_models()` levantava, o `except` devolvia lista vazia
# e o painel dizia "o gateway respondeu com o catalogo vazio" -- o operador ia
# procurar um cadastro faltando em vez de olhar o gateway.
#
# Nos irmaos o estado do catalogo nasce em `gateway.py` e chega pronto ao
# `web.py` em duas linhas; no LiteLlmRTKSync a leitura acontece no proprio
# `web.py`, entao sao quatro. A convergencia completa desse ponto e mover a
# leitura para `gateway.py`, e isso continua em aberto.
TETOS_DE_DIVERGENCIA = {
    "models.py": 7,
    "render.py": 560,
    "web.py": 578,
}

# O que cada produto troca de propósito, e que não conta como divergência.
IDENTIDADE = (
    ("nine_rtksync", "omini_rtksync", "litellm_rtksync"),
    ("9RTKSync", "OminiRTKSync", "OminiRTkSync", "LiteLlmRTKSync"),
    ("9rtk", "ominirtk", "litellmrtk"),
    ("9Router", "OmniRoute", "LiteLLM"),
)

# Limite de linhas do diff mostrado na falha: o suficiente para ver o que mudou
# sem despejar um arquivo inteiro no terminal.
LINHAS_DE_DIFF = 40


def ordem_canonica(nome: str) -> int:
    """Posição do repositório em IRMAOS; um clone renomeado vai para o fim.

    Serve para fixar a direção da comparação. A comparação ignora maiúsculas
    porque nesta máquina o mesmo repositório é alcançado como `9RTKSync` e como
    `9rtksync` -- o instalador editável gravou o caminho em minúsculas, e o
    sistema de arquivos não distingue. Sem o `.lower()`, quem rodasse a suíte a
    partir do caminho minúsculo caía no fim da ordem, media todos os pares
    invertidos e via números que não batem com nenhum teto.
    """
    alvo = nome.lower()
    for i, irmao in enumerate(IRMAOS):
        if irmao.lower() == alvo:
            return i
    return len(IRMAOS)


def pacote_de(raiz: pathlib.Path):
    """O diretório do pacote dentro de um repositório, pelo `identidade.py`."""
    origem = raiz / "src"
    if not origem.is_dir():
        return None
    for candidato in sorted(origem.iterdir()):
        if (candidato / "identidade.py").is_file():
            return candidato
    return None


class ModulosComunsSaoOMesmoTexto(unittest.TestCase):
    def test_cada_modulo_comum_e_identico_ao_do_irmao(self):
        meu_pacote = pacote_de(RAIZ)
        self.assertIsNotNone(meu_pacote, "este repositório não tem src/<pacote>/identidade.py")

        for irmao in IRMAOS:
            raiz_do_irmao = RAIZ.parent / irmao
            if raiz_do_irmao.resolve() == RAIZ:
                continue
            if not raiz_do_irmao.is_dir():
                with self.subTest(irmao=irmao):
                    self.skipTest(f"irmão {irmao} não está ao lado; clone isolado")
                continue
            pacote_do_irmao = pacote_de(raiz_do_irmao)
            if pacote_do_irmao is None:
                with self.subTest(irmao=irmao):
                    self.skipTest(f"irmão {irmao} ainda não declara identidade.py")
                continue

            for arquivo in IDENTICOS:
                with self.subTest(irmao=irmao, arquivo=arquivo):
                    meu = meu_pacote / arquivo
                    dele = pacote_do_irmao / arquivo
                    self.assertTrue(meu.is_file(), f"{arquivo} não existe neste repositório")
                    self.assertTrue(
                        dele.is_file(),
                        f"{arquivo} não existe em {irmao} -- é assim que a simetria "
                        "some sem ninguém ver",
                    )
                    texto_meu = meu.read_text(encoding="utf-8")
                    texto_dele = dele.read_text(encoding="utf-8")
                    if texto_meu == texto_dele:
                        continue
                    diferenca = list(
                        difflib.unified_diff(
                            texto_meu.splitlines(keepends=True),
                            texto_dele.splitlines(keepends=True),
                            fromfile=f"{RAIZ.name}/{arquivo}",
                            tofile=f"{irmao}/{arquivo}",
                        )
                    )
                    recorte = "".join(diferenca[:LINHAS_DE_DIFF])
                    if len(diferenca) > LINHAS_DE_DIFF:
                        recorte += f"... (+{len(diferenca) - LINHAS_DE_DIFF} linhas)\n"
                    self.fail(
                        f"{arquivo} difere de {irmao}. Se a diferença é identidade, "
                        f"ela pertence a identidade.py:\n{recorte}"
                    )

    def test_a_divergencia_dos_modulos_em_convergencia_nao_cresce(self):
        """Catraca: o que ainda difere só pode diferir menos a cada rodada.

        Sem isto, a convergência depende de alguém lembrar de medir. Foi a falta
        dessa medida que deixou o `sso.py` divergir em mil linhas entre irmãos
        que saíram do mesmo desenho -- cada agente escreveu do seu jeito e
        ninguém comparou.
        """
        meu_pacote = pacote_de(RAIZ)
        self.assertIsNotNone(meu_pacote)

        def normaliza(caminho):
            texto = caminho.read_text(encoding="utf-8", errors="ignore")
            for grupo in IDENTIDADE:
                for termo in grupo:
                    texto = texto.replace(termo, "X")
            return texto.splitlines()

        estourados = []
        for irmao in IRMAOS:
            raiz_do_irmao = RAIZ.parent / irmao
            if raiz_do_irmao.resolve() == RAIZ or not raiz_do_irmao.is_dir():
                continue
            pacote_do_irmao = pacote_de(raiz_do_irmao)
            if pacote_do_irmao is None:
                continue

            for arquivo, teto in sorted(TETOS_DE_DIVERGENCIA.items()):
                meu, dele = meu_pacote / arquivo, pacote_do_irmao / arquivo
                if not (meu.is_file() and dele.is_file()):
                    continue
                # Ordem canônica: quem vem antes em IRMAOS é sempre o lado
                # esquerdo, seja ele "eu" ou "ele". Assim os três repositórios
                # medem o mesmo par e chegam ao mesmo número.
                if ordem_canonica(irmao) < ordem_canonica(RAIZ.name):
                    esquerda, direita = dele, meu
                else:
                    esquerda, direita = meu, dele
                linhas = sum(
                    1
                    for linha in difflib.unified_diff(
                        normaliza(esquerda), normaliza(direita), n=0
                    )
                    if linha[:1] in "+-" and linha[:3] not in ("+++", "---")
                )
                if linhas > teto:
                    estourados.append(
                        f"{arquivo} contra {irmao}: {linhas} linhas, teto {teto} "
                        "-- a divergência AUMENTOU"
                    )

        self.assertEqual(
            estourados,
            [],
            "a convergência andou para trás:\n  " + "\n  ".join(estourados)
            + "\n\nSe a divergência cresceu de propósito, o teto é que precisa "
            "de justificativa -- não o contrário.",
        )


if __name__ == "__main__":
    unittest.main()
