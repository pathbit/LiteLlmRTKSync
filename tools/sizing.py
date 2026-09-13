#!/usr/bin/env python3
"""Resolve a formula de dimensionamento com os numeros medidos e publicados.

Tres classes de entrada, e a diferenca entre elas importa mais que os valores:

- MEDIDA: `PROFILES`, copiada da saida de `measure_agent_usage.py` numa
  maquina e num instante especificos (veja `PROFILE_SOURCE`). E instantaneo,
  nao constante do universo: o historico cresce, e rodar o medidor de novo da
  outro perfil. Troque `PROFILES` pela SUA saida antes de decidir qualquer
  coisa com as tabelas daqui.
- PUBLICADA: `API_TIERS`, a tabela de rate limits por tier da API da Anthropic.
- ARBITRADA: `CONCURRENCY` e `SLACK`. Nao foram medidos em lugar nenhum --
  estao variados de proposito para mostrar o quanto deslocam a resposta. Este
  script e onde esses dois numeros foram escolhidos, entao ele NAO e fonte
  deles: quem quiser o proprio `c` mede concorrencia no log de gasto.
"""

import math

# --- medido: perfil de UMA sessao ativa de agente ---
PROFILE_SOURCE = "measure_agent_usage.py, instantaneo de 2026-09-13T02:11:02Z, uma maquina"
PROFILES = {
    "mediana": {"rate_per_hour": 213, "billable_input": 3745, "output": 732},
    "p90": {"rate_per_hour": 342, "billable_input": 7668, "output": 1351},
}

# --- publicado: teto por tier da API (Opus 5 / Sonnet 5) ---
API_TIERS = {
    "Start": {"rpm": 1_000, "itpm": 2_000_000, "otpm": 400_000},
    "Build": {"rpm": 5_000, "itpm": 5_000_000, "otpm": 1_000_000},
    "Scale": {"rpm": 10_000, "itpm": 10_000_000, "otpm": 2_000_000},
}

TEAM_SIZES = [3, 12, 40]
# Arbitrados: nao medidos, variados de proposito. Veja o docstring.
CONCURRENCY = [0.4, 0.6, 1.0]
SLACK = 0.30


def burst_demand(team_size, concurrency, profile, slack=SLACK):
    """Demanda por minuto: e o teto que estoura primeiro."""
    simultaneous = team_size * concurrency
    rpm = simultaneous * profile["rate_per_hour"] / 60 * (1 + slack)
    return simultaneous, rpm, rpm * profile["billable_input"], rpm * profile["output"]


def smallest_tier(rpm, itpm, otpm):
    for name, tier in API_TIERS.items():
        if rpm <= tier["rpm"] and itpm <= tier["itpm"] and otpm <= tier["otpm"]:
            return name
    return "acima de Scale"


print("### CAMINHO DE API - teto publicado, resolve numericamente")
print(f"perfil medido copiado de: {PROFILE_SOURCE}")
print("c e folga: ARBITRADOS, nao medidos -- meca os seus antes de decidir.")
for label, profile in PROFILES.items():
    print(f"\nperfil {label}: R_h={profile['rate_per_hour']} req/h, "
          f"T_in={profile['billable_input']} tok/req, T_out={profile['output']} tok/req, "
          f"folga={SLACK:.0%}")
    print(f"{'devs':>5} {'c':>5} {'U_sim':>6} {'RPM':>7} {'ITPM':>10} {'OTPM':>9}  tier minimo")
    for size in TEAM_SIZES:
        for factor in CONCURRENCY:
            simultaneous, rpm, itpm, otpm = burst_demand(size, factor, profile)
            print(f"{size:>5} {factor:>5.1f} {simultaneous:>6.1f} {rpm:>7.0f} "
                  f"{itpm:>10,.0f} {otpm:>9,.0f}  {smallest_tier(rpm, itpm, otpm)}")

print("\n\n### unidade do tpm_limit: entrada que conta + saida, por requisicao")
print("O ITPM do fornecedor conta so entrada nao-cacheada; o limitador v3 do")
print("LiteLLM desconta cache e soma a saida. A diferenca entre as duas contas")
print("e o quanto copiar o ITPM do tier para PLATFORM_TPM_LIMIT e conservador.")
for label, profile in PROFILES.items():
    counted = profile["billable_input"]
    with_output = counted + profile["output"]
    print(f"perfil {label:>7} | ITPM conta {counted:>6,} tok/req | "
          f"tpm_limit v3 {with_output:>6,} tok/req | "
          f"conservador em {(with_output / counted - 1) * 100:4.1f}%")

print("\n\n### folga da rajada contra o tier Start (1000 rpm)")
for size in TEAM_SIZES:
    _, rpm, _, _ = burst_demand(size, 0.6, PROFILES["mediana"])
    print(f"{size:>2} devs, c=0.6 -> pico exigido {rpm:6.0f} rpm; "
          f"Start cobre {math.floor(1000 / rpm)}x a demanda")
