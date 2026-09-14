"""O produto lê data de um jeito só.

Havia dois leitores para o mesmo campo. `models.to_epoch_ms` lia carimbo ISO sem
fuso como UTC -- que é como os gateways gravam -- e
`normalizer.parse_iso_or_str_to_ms` deixava o Python assumir o fuso da máquina.
Nesta máquina (-03) isso dava três horas de diferença para o mesmo texto.

Não era diferença acadêmica: o normalizer é quem decide APAGAR a trava de rate
limit quando ela venceu, e o models é quem decide MOSTRAR `rate_limited` na
tela. Com leitores que discordam, o painel podia desenhar uma trava que o
próprio sincronizador já tinha considerado vencida -- ou pior, o sincronizador
apagar uma trava que ainda valia.

Nenhum teste cobria isso, e foi um cético lendo o diff que percebeu. Este teste
existe para que os dois não voltem a divergir: qualquer carimbo tem de valer o
mesmo instante para os dois módulos.
"""

import os
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "src"))


def pacote():
    origem = os.path.join(RAIZ, "src")
    for nome in sorted(os.listdir(origem)):
        if os.path.isfile(os.path.join(origem, nome, "identidade.py")):
            return nome
    raise AssertionError("este repositório não tem src/<pacote>/identidade.py")


PACOTE = pacote()

# Os formatos que os gateways realmente gravam, incluindo os tortos que deram
# origem a esta família de projetos.
CARIMBOS = (
    "2026-09-12T15:20:22",            # ISO sem fuso -- o caso que divergia
    "2026-09-12T15:20:22Z",           # ISO em UTC explícito
    "2026-09-12T15:20:22.336Z",       # com milissegundos
    "2026-09-12T15:20:22+00:00",      # deslocamento explícito
    "1789226422000",                  # epoch em ms, gravado como TEXTO
    1789226422000,                    # epoch em ms, numérico
    1789226422,                       # epoch em segundos
    "",                               # vazio
    None,                             # ausente
)


class OsDoisModulosLeemAMesmaData(unittest.TestCase):
    def setUp(self):
        from importlib import import_module
        self.models = import_module(f"{PACOTE}.models")
        try:
            self.normalizer = import_module(f"{PACOTE}.normalizer")
        except ModuleNotFoundError:
            self.skipTest("este produto não tem normalizer.py")
        # O nome da função difere entre os irmãos; o que importa é o critério.
        self.ler_do_normalizer = getattr(self.normalizer, "parse_iso_or_str_to_ms", None) \
            or getattr(self.normalizer, "parse_expiry_to_ms", None)
        self.assertIsNotNone(
            self.ler_do_normalizer,
            "normalizer.py não expõe leitor de data com nome conhecido",
        )

    def test_o_mesmo_carimbo_vale_o_mesmo_instante_nos_dois(self):
        for carimbo in CARIMBOS:
            with self.subTest(carimbo=carimbo):
                self.assertEqual(
                    self.ler_do_normalizer(carimbo),
                    self.models.to_epoch_ms(carimbo),
                    f"normalizer e models discordam sobre {carimbo!r}. Um decide "
                    f"apagar a trava de rate limit, o outro decide desenhá-la na "
                    f"tela -- e com leitores diferentes eles brigam.",
                )

    def test_carimbo_sem_fuso_e_lido_como_utc(self):
        """O critério certo, explicitado: os gateways gravam em UTC.

        Assumir o fuso da máquina faria o mesmo dado significar horas diferentes
        em dois servidores, o que é indefensável para um painel que compara
        prazos.
        """
        sem_fuso = self.models.to_epoch_ms("2026-09-12T15:20:22")
        com_utc = self.models.to_epoch_ms("2026-09-12T15:20:22Z")
        self.assertEqual(
            sem_fuso, com_utc,
            "carimbo sem fuso tem de valer o mesmo que o carimbo em UTC",
        )


if __name__ == "__main__":
    unittest.main()
