#!/usr/bin/env python3
"""Mede o perfil de consumo real de um agente a partir do historico do Claude Code.

Le APENAS os campos numericos de `usage`, o `message.id` e o `timestamp`.
Nenhum conteudo de conversa e lido, agregado ou impresso.

Cuidado que muda o resultado: uma unica resposta da API costuma ser gravada em
VARIAS linhas `type: "assistant"` (texto + cada bloco de ferramenta), todas com
o mesmo `message.id` e o mesmo objeto `usage`. Contar linhas infla requisicoes e
tokens em ~2x. Aqui cada `message.id` conta uma vez.

A saida tem DOIS recortes, e confundi-los da numeros incompativeis para a mesma
grandeza:

- sob `== historico inteiro ==`: tudo o que existe em `~/.claude/projects`, sem
  filtro nenhum. E o par de contagens que mede a inflacao por nao deduplicar.
- sob `== amostra ==`: so as sessoes que passaram pelos dois filtros declarados
  abaixo (`MIN_TURNS_PER_SESSION` e `MIN_ACTIVE_SECONDS`). Por isso o total de
  turnos da amostra e varias vezes menor que os `message.id` distintos do
  historico: sao universos diferentes, de proposito.

O numero que sai daqui e um INSTANTANEO: o historico cresce a cada sessao, entao
rodar de novo na mesma maquina da outro resultado. O que reproduz e o metodo.
"""

import glob
import json
import os
import statistics
from datetime import datetime, timedelta

HISTORY_ROOT = os.path.expanduser("~/.claude/projects")
IDLE_GAP = timedelta(minutes=20)   # lacuna que encerra uma "hora ativa"
MIN_TURNS_PER_SESSION = 10         # sessao curta demais nao diz nada sobre ritmo
MIN_ACTIVE_SECONDS = 300


def read_session(path):
    """Le um arquivo de sessao.

    Devolve `(turns, raw_lines)`:

    - `turns`: os turnos unicos do arquivo, ordenados no tempo, ja deduplicados
      por `message.id`;
    - `raw_lines`: quantas linhas `type: "assistant"` com `usage` positivo o
      arquivo tem ANTES de deduplicar -- e a contagem que sustenta o fator de
      inflacao publicado pela pagina de dimensionamento;
    - `ids`: os `message.id` vistos, para a uniao global (uma sessao retomada
      recopia linhas da anterior, entao id repetido entre arquivos existe).

    As tres grandezas saem da mesma leitura de proposito: e a unica forma de a
    razao entre elas ser reproduzivel por quem rodar o script.
    """
    turns = {}
    raw_lines = 0
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if '"usage"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if record.get("type") != "assistant":
                    continue
                message = record.get("message") or {}
                usage = message.get("usage") or {}
                if not isinstance(usage, dict):
                    continue
                fresh_input = usage.get("input_tokens") or 0
                cache_write = usage.get("cache_creation_input_tokens") or 0
                cache_read = usage.get("cache_read_input_tokens") or 0
                output = usage.get("output_tokens") or 0
                if fresh_input + cache_write + cache_read + output <= 0:
                    continue
                try:
                    when = datetime.fromisoformat(
                        str(record.get("timestamp")).replace("Z", "+00:00")
                    )
                except Exception:
                    continue
                # Contada ANTES de deduplicar: e o numerador do fator de inflacao.
                raw_lines += 1
                # Deduplicacao: uma resposta da API = um message.id, nao uma linha.
                # Entre as linhas que repetem o id, fica a de maior contagem: as
                # primeiras podem trazer `output_tokens` ainda parcial.
                message_id = message.get("id") or f"{path}:{when.isoformat()}"
                candidate = (when, fresh_input + cache_write, output, cache_read)
                previous = turns.get(message_id)
                if previous is None or sum(candidate[1:]) > sum(previous[1:]):
                    turns[message_id] = candidate
    except Exception:
        return [], 0, ()
    return sorted(turns.values(), key=lambda t: t[0]), raw_lines, turns.keys()


def active_seconds(turns):
    """Tempo em que o agente esteve de fato requisitando, ignorando pausas longas."""
    total = 0.0
    for previous, current in zip(turns, turns[1:]):
        delta = current[0] - previous[0]
        if timedelta(0) <= delta <= IDLE_GAP:
            total += delta.total_seconds()
    return total


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    index = int(round(fraction * (len(ordered) - 1)))
    return ordered[max(0, min(len(ordered) - 1, index))]


billable_input, output_tokens, cached_input, total_input = [], [], [], []
request_rates, session_spans = [], []
grand_billable = grand_output = grand_cached = grand_turns = 0
# Historico INTEIRO, sem nenhum filtro de sessao: o par que mede a inflacao.
history_lines = 0
history_ids = set()

for session_path in glob.glob(os.path.join(HISTORY_ROOT, "**", "*.jsonl"), recursive=True):
    turns, raw_lines, ids = read_session(session_path)
    # Acumulado antes dos filtros: estas duas contagens descrevem o historico
    # todo, e nao a amostra que sobra depois do recorte por sessao.
    history_lines += raw_lines
    history_ids.update(ids)
    if len(turns) < MIN_TURNS_PER_SESSION:
        continue
    elapsed = active_seconds(turns)
    if elapsed < MIN_ACTIVE_SECONDS:
        continue
    count = len(turns)
    billable_input.append(sum(t[1] for t in turns) / count)
    output_tokens.append(sum(t[2] for t in turns) / count)
    cached_input.append(sum(t[3] for t in turns) / count)
    total_input.append(sum(t[1] + t[3] for t in turns) / count)
    request_rates.append(count / (elapsed / 3600.0))
    session_spans.append((turns[0][0], turns[-1][0]))
    grand_billable += sum(t[1] for t in turns)
    grand_output += sum(t[2] for t in turns)
    grand_cached += sum(t[3] for t in turns)
    grand_turns += count

# Primeiro recorte: o historico INTEIRO, sem filtro nenhum. Sao estas contagens,
# e nao os totais da amostra, que medem quanto contar linha infla o numero.
print("== historico inteiro (sem filtro de sessao) ==")
print(f"linhas type=assistant com usage             : {history_lines}")
print(f"message.id distintos                        : {len(history_ids)}")
print(f"fator de inflacao por contar linha          : "
      f"{history_lines / max(1, len(history_ids)):.2f}x")
# Segundo recorte: a AMOSTRA, so as sessoes que passaram pelos dois filtros
# (MIN_TURNS_PER_SESSION e MIN_ACTIVE_SECONDS). Sessao curta nao diz nada sobre
# ritmo, e e ritmo o que o resto do bloco mede. Por isso os turnos daqui sao
# varias vezes menos que os message.id do historico: universos diferentes.
print(f"\n== amostra (sessoes com >= {MIN_TURNS_PER_SESSION} turnos "
      f"e >= {MIN_ACTIVE_SECONDS}s ativos) ==")
print(f"sessoes analisadas                          : {len(request_rates)}")
print(f"turnos unicos (dedup por message.id)        : {grand_turns}")
print(f"T_in  entrada que conta p/ ITPM  mediana    : {statistics.median(billable_input):.0f}")
print(f"T_in  entrada que conta p/ ITPM  p90        : {percentile(billable_input, 0.90):.0f}")
print(f"T_out saida                      mediana    : {statistics.median(output_tokens):.0f}")
print(f"T_out saida                      p90        : {percentile(output_tokens, 0.90):.0f}")
print(f"T_cache leitura de cache         mediana    : {statistics.median(cached_input):.0f}")
print(f"T_tot entrada total (conta+cache) mediana   : {statistics.median(total_input):.0f}")
print(f"T_tot entrada total (conta+cache) p90       : {percentile(total_input, 0.90):.0f}")
print(f"R_h   requisicoes por hora ativa mediana    : {statistics.median(request_rates):.0f}")
print(f"R_h   requisicoes por hora ativa p90        : {percentile(request_rates, 0.90):.0f}")
total_all = grand_billable + grand_output + grand_cached
print(f"fracao de leitura de cache no total         : {grand_cached / total_all * 100:.1f}%")
print(f"razao entrada total / entrada que conta     : "
      f"{statistics.median(total_input) / statistics.median(billable_input):.1f}x")

# Concorrencia: quantas sessoes se sobrepoem no tempo. Numa maquina de um unico
# operador isto mede paralelismo de subagentes, nao concorrencia de time.
events = []
for start, end in session_spans:
    events.append((start, 1))
    events.append((end, -1))
events.sort()
open_sessions = peak = 0
for _, delta in events:
    open_sessions += delta
    peak = max(peak, open_sessions)
print(f"pico de sessoes simultaneas                 : {peak}")
