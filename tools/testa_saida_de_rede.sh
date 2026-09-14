#!/usr/bin/env bash
# Testa POR ONDE o trafego de um gateway sai.
#
# A pergunta central nao se responde lendo codigo: quando uma conta esta
# vinculada a um proxy e esse proxy cai, o gateway FALHA -- ou sai direto, pelo
# IP da maquina, com o token da conta? A diferenca entre as duas respostas e a
# diferenca entre isolamento e aparencia de isolamento.
#
# Como funciona: o destino e um servidor local que devolve o endereco de origem
# que enxergou. Nao ha palpite -- ele diz se a requisicao chegou pelo proxy A,
# pelo B ou direto. Nada toca a internet.
#
# Uso:
#   docker compose -f docker-compose.egress-test.yml up -d
#   tools/testa_saida_de_rede.sh
#
# Variaveis (todas com padrao):
#   ECHO_URL    destino visto de dentro da rede de teste  (172.31.0.20:8080)
#   PROXY_A     proxy A, do host                          (127.0.0.1:18081)
#   PROXY_B     proxy B, do host                          (127.0.0.1:18082)
#   IP_HOST     o que o destino ve numa saida direta      (172.31.0.1)
#   IP_PROXY_A / IP_PROXY_B   enderecos dos proxies       (172.31.0.11/.12)

set -uo pipefail

ECHO_URL="${ECHO_URL:-http://172.31.0.20:8080}"
ECHO_HOST="${ECHO_HOST:-http://127.0.0.1:18080}"
PROXY_A="${PROXY_A:-http://127.0.0.1:18081}"
PROXY_B="${PROXY_B:-http://127.0.0.1:18082}"
IP_HOST="${IP_HOST:-172.31.0.1}"
IP_PROXY_A="${IP_PROXY_A:-172.31.0.11}"
IP_PROXY_B="${IP_PROXY_B:-172.31.0.12}"

TOTAL=0
FALHAS=0

ok()    { TOTAL=$((TOTAL+1)); printf '  ok    %-56s %s\n' "$1" "${2:-}"; }
falha() { TOTAL=$((TOTAL+1)); FALHAS=$((FALHAS+1)); printf '  FALHA %-56s %s\n' "$1" "${2:-}"; }

# Devolve o campo seen_from que o destino enxergou, ou vazio se nao respondeu.
origem_vista() {  # origem_vista <url-do-proxy-ou-vazio> <caminho>
  local proxy="$1" caminho="$2" resposta
  if [ -n "$proxy" ]; then
    resposta=$(curl -s --max-time 8 -x "$proxy" "$ECHO_URL$caminho" 2>/dev/null)
  else
    resposta=$(curl -s --max-time 8 "$ECHO_HOST$caminho" 2>/dev/null)
  fi
  printf '%s' "$resposta" | sed -n 's/.*"seen_from": *"\([^"]*\)".*/\1/p'
}

echo "===================================================================="
echo " Por onde o trafego sai"
echo "===================================================================="

echo
echo "-- 1. a bancada responde? --"
for nome in echo proxy-a proxy-b; do
  case "$nome" in
    echo)    alvo="$ECHO_HOST" ;;
    proxy-a) alvo="$PROXY_A" ;;
    proxy-b) alvo="$PROXY_B" ;;
  esac
  if curl -s -o /dev/null --max-time 5 --connect-timeout 3 "$alvo" 2>/dev/null \
     || nc -z "${alvo#http://}" 2>/dev/null \
     || curl -s -o /dev/null --max-time 5 -x "$alvo" "$ECHO_URL/ping" 2>/dev/null; then
    ok "$nome responde"
  else
    falha "$nome responde" "suba a bancada: docker compose -f docker-compose.egress-test.yml up -d"
  fi
done

echo
echo "-- 2. linha de base: sem proxy, o destino ve o endereco da maquina --"
visto=$(origem_vista "" "/direto")
if [ "$visto" = "$IP_HOST" ]; then
  ok "saida direta vista como $IP_HOST"
else
  falha "saida direta vista como $IP_HOST" "obtido=${visto:-<sem resposta>}"
fi

echo
echo "-- 3. com proxy, o destino ve o endereco DO PROXY, nao o da maquina --"
visto=$(origem_vista "$PROXY_A" "/via-a")
if [ "$visto" = "$IP_PROXY_A" ]; then
  ok "pelo proxy A, visto como $IP_PROXY_A"
else
  falha "pelo proxy A, visto como $IP_PROXY_A" "obtido=${visto:-<sem resposta>}"
fi

echo
echo "-- 4. proxies distintos produzem enderecos distintos --"
echo "     (e o que permite uma conta por endereco; com um proxy so, nao da"
echo "      para distinguir 'saiu pelo proxy' de 'saiu por qualquer proxy')"
a=$(origem_vista "$PROXY_A" "/conta-a")
b=$(origem_vista "$PROXY_B" "/conta-b")
if [ -n "$a" ] && [ -n "$b" ] && [ "$a" != "$b" ]; then
  ok "conta A ($a) e conta B ($b) saem por enderecos diferentes"
else
  falha "conta A e conta B saem por enderecos diferentes" "A=${a:-<->} B=${b:-<->}"
fi

echo
echo "-- 5. A PERGUNTA QUE IMPORTA: com o proxy fora do ar, o que acontece? --"
echo "     Se a requisicao ainda for atendida, ela saiu DIRETO -- pelo IP da"
echo "     maquina, com o token da conta. E o vazamento silencioso."
if docker ps --format '{{.Names}}' | grep -qx egress-proxy-b; then
  docker stop egress-proxy-b >/dev/null 2>&1
  sleep 2
  visto=$(origem_vista "$PROXY_B" "/proxy-morto")
  if [ -z "$visto" ]; then
    ok "proxy fora do ar -> a requisicao FALHA" "nenhuma resposta, como deve ser"
  elif [ "$visto" = "$IP_HOST" ]; then
    falha "proxy fora do ar -> a requisicao FALHA" \
          "VAZOU: saiu direto, visto como $IP_HOST"
  else
    falha "proxy fora do ar -> a requisicao FALHA" "resposta inesperada de $visto"
  fi
  docker start egress-proxy-b >/dev/null 2>&1
  sleep 3
else
  falha "proxy fora do ar -> a requisicao FALHA" "egress-proxy-b nao esta na bancada"
fi

echo
echo "-- 6. o proxy volta e a saida volta com ele --"
visto=$(origem_vista "$PROXY_B" "/depois-de-voltar")
if [ "$visto" = "$IP_PROXY_B" ]; then
  ok "proxy de volta, visto como $IP_PROXY_B"
else
  falha "proxy de volta, visto como $IP_PROXY_B" "obtido=${visto:-<sem resposta>}"
fi

echo
echo "===================================================================="
printf ' verificacoes: %s   falhas: %s\n' "$TOTAL" "$FALHAS"
echo "===================================================================="
echo
echo "Leitura do resultado:"
echo "  - o passo 5 e o unico que distingue isolamento de aparencia de"
echo "    isolamento. Um 'ok' ali significa que a queda do proxy interrompe o"
echo "    trafego em vez de desviá-lo pelo endereco da maquina."
echo "  - o passo 4 e o que sustenta uma conta por endereco: sem ele, todas as"
echo "    contas compartilham a mesma saida, que e o padrao que chama atencao."
echo
echo "Ao ligar isso num gateway real, aponte ECHO_URL para o destino que o"
echo "gateway alcanca e configure o proxy NO GATEWAY -- nao no curl. O que se"
echo "quer medir e a decisao do gateway, nao a do cliente."

exit $((FALHAS > 0))
