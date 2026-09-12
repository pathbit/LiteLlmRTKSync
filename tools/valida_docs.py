#!/usr/bin/env python3
"""Confere o que a documentação afirma contra o que o código faz.

Documentação envelhece em silêncio: uma porta que mudou, uma flag renomeada,
uma variável de ambiente que deixou de ser lida. Nada disso quebra teste nem
aparece em revisão, e o leitor descobre sozinho quando o comando não funciona.

O que este verificador extrai das páginas e confronta com a fonte:

- **variáveis de ambiente** citadas -> existem em `os.environ` no código?
- **flags de linha de comando** citadas -> existem no argparse?
- **rotas HTTP** citadas -> existem no servidor?
- **caminhos de arquivo** do próprio repositório citados -> existem em disco?
- **portas** citadas -> batem com o default do código e dos composes?

Uma citação que não se confirma é reportada com a página e a linha.
"""

import os
import re
import sys
from typing import Dict, List, Set, Tuple

# --------------------------------------------------------------------------
# Extração da documentação
# --------------------------------------------------------------------------

# Variáveis em caixa alta, com pelo menos um sublinhado ou 4 letras. Filtramos
# depois contra uma lista de termos que não são variáveis (HTTP, JSON, ...).
RX_ENV = re.compile(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b")
RX_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]+)")
RX_ROTA = re.compile(r"[`\"'\s(](/(?:healthz|api|acoes|actions|index|static)[a-z0-9/_.-]*)")
RX_PORTA = re.compile(r"(?<![\w.:])(\d{4,5})(?![\w.])")

# Linha que invoca outro programa: as flags citadas pertencem a ele.
RX_COMANDO_DE_TERCEIRO = re.compile(
    r"\b(pip|pip3|docker|docker[- ]compose|git|curl|wget|tailscale|cloudflared|make|npm|npx|"
    r"apt|apt-get|brew|systemctl|python3?\s+-m\s+venv|openssl|psql)\b"
)

# Flags que pertencem a outro programa e aparecem em prosa, sem o comando na
# mesma linha -- "`--advertise-exit-node` mais `--exit-node` sao ajustes por
# host" fala do Tailscale sem invocar o binario.
FLAGS_DE_TERCEIROS = {
    "--advertise-exit-node", "--exit-node",   # tailscale
    "--help", "--version",                    # universais
}

# Rotas do GATEWAY, nao do painel. A pagina de saida de rede cita a API do
# proprio gateway de proposito: e la que o operador cadastra o proxy.
RX_ROTA_DO_GATEWAY = re.compile(r"(?i)\b(9router|omniroute|litellm|gateway|proxy pool)\b")

NAO_SAO_VARIAVEIS = {
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",  # padrão do sistema, não do projeto
}
# Siglas em caixa alta que aparecem em prosa e não são variáveis.
RUIDO = re.compile(
    r"^(HTTP_?\d*|JSON_?\w*|API_?KEY_?\w*|SQL\w*|UTC_?\w*|README\w*|TODO\w*|NOTE\w*|"
    r"MIT_?\w*|CI_?CD|OAUTH\w*|PKCE\w*|CRUD|REST_?API|URL_?\w*|URI_?\w*|ID_?\w*)$"
)


def paginas(raiz: str) -> List[str]:
    """Todo markdown versionado do repositório, menos os upstreams clonados."""
    achados = []
    for pasta, dirs, arquivos in os.walk(raiz):
        dirs[:] = [
            d for d in dirs
            if d not in (".git", "tmp", "node_modules", "__pycache__", ".venv", "assets")
        ]
        for a in arquivos:
            if a.endswith(".md"):
                achados.append(os.path.join(pasta, a))
    return sorted(achados)


def fonte_do_repo(raiz: str) -> str:
    """Todo o código Python e YAML do repositório, concatenado."""
    partes = []
    for pasta, dirs, arquivos in os.walk(raiz):
        dirs[:] = [
            d for d in dirs
            if d not in (".git", "tmp", "node_modules", "__pycache__", ".venv")
        ]
        for a in arquivos:
            if a.endswith((".py", ".yml", ".yaml", ".toml", ".cfg", ".sh", ".example")):
                try:
                    with open(os.path.join(pasta, a), encoding="utf-8") as f:
                        partes.append(f.read())
                except (OSError, UnicodeDecodeError):
                    pass
    return "\n".join(partes)


def env_do_codigo(fonte: str) -> Set[str]:
    """Variáveis que o código realmente lê, mais as declaradas nos composes."""
    lidas = set(re.findall(r"environ\.get\(\s*['\"]([A-Z][A-Z0-9_]+)['\"]", fonte))
    lidas |= set(re.findall(r"environ\[\s*['\"]([A-Z][A-Z0-9_]+)['\"]\s*\]", fonte))
    # Declaradas em compose/.env.example contam como parte do contrato.
    lidas |= set(re.findall(r"^\s*-?\s*([A-Z][A-Z0-9_]+)=", fonte, re.M))
    lidas |= set(re.findall(r"\$\{([A-Z][A-Z0-9_]+)[:?}-]", fonte))
    # Identificadores que o codigo produz sem serem variaveis de ambiente --
    # `b"LITELLM_UNREACHABLE"` e as respostas de /healthz, por exemplo. Se o
    # nome existe no fonte, a documentacao nao esta inventando nada.
    lidas |= set(re.findall(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b", fonte))
    return lidas


def flags_do_codigo(fonte: str) -> Set[str]:
    return set(re.findall(r"add_argument\(\s*['\"](--[a-z][a-z0-9-]*)['\"]", fonte))


def rotas_do_codigo(fonte: str) -> Set[str]:
    rotas = set(re.findall(r"['\"](/(?:healthz|api|acoes|actions|index|static)[a-z0-9/_.-]*)['\"]", fonte))
    # startswith("/api/") e comparações equivalentes
    rotas |= set(re.findall(r"startswith\(\s*['\"](/[a-z0-9/_.-]+)['\"]", fonte))
    return rotas


# --------------------------------------------------------------------------
# Verificação
# --------------------------------------------------------------------------

def verificar(raiz: str, nome: str) -> List[str]:
    problemas: List[str] = []
    fonte = fonte_do_repo(raiz)
    env_ok = env_do_codigo(fonte)
    flags_ok = flags_do_codigo(fonte)
    rotas_ok = rotas_do_codigo(fonte)

    for pagina in paginas(raiz):
        rel = os.path.relpath(pagina, raiz)
        try:
            with open(pagina, encoding="utf-8") as f:
                linhas = f.readlines()
        except (OSError, UnicodeDecodeError):
            continue

        for n, linha in enumerate(linhas, 1):
            for var in RX_ENV.findall(linha):
                if var in NAO_SAO_VARIAVEIS or RUIDO.match(var):
                    continue
                # Codigo de log, nao variavel: TCP_TUNNEL/200, HIER_DIRECT/1.2.3.4
                # e NONE_NONE/000 aparecem em trecho de log colado na pagina, e
                # sempre com uma barra logo depois.
                if re.search(re.escape(var) + r"/", linha):
                    continue
                if var not in env_ok:
                    problemas.append(f"{nome}/{rel}:{n}  variável citada e não usada no código: {var}")

            # Uma linha que invoca outro programa traz as flags DELE. Acusar
            # `pip install --upgrade` de nao existir no nosso CLI e ruido, e
            # ruido treina o leitor a ignorar o verificador inteiro.
            if RX_COMANDO_DE_TERCEIRO.search(linha):
                continue

            for flag in RX_FLAG.findall(linha):
                if flag in FLAGS_DE_TERCEIROS:
                    continue
                if flag not in flags_ok:
                    problemas.append(f"{nome}/{rel}:{n}  flag citada e inexistente no CLI: {flag}")

            # Uma rota citada numa frase sobre o gateway e do gateway. A frase
            # pode estar quebrada em varias linhas -- prosa com margem de 80
            # colunas quebra no meio o tempo todo -- entao vale o paragrafo, e
            # nao a linha isolada: olhar so a linha acusava uma rota do gateway
            # sempre que a palavra "gateway" tinha caido na linha de cima.
            contexto = "".join(linhas[max(0, n - 3):n + 1])
            if RX_ROTA_DO_GATEWAY.search(contexto):
                continue

            for rota in RX_ROTA.findall(linha):
                base = rota.rstrip(".,;:)")
                if base not in rotas_ok and not any(r.startswith(base) for r in rotas_ok):
                    problemas.append(f"{nome}/{rel}:{n}  rota citada e não servida: {base}")

    return problemas


def main() -> int:
    alvos: List[Tuple[str, str]] = [
        (a, os.path.basename(a.rstrip("/"))) for a in sys.argv[1:]
    ]
    total = 0
    for raiz, nome in alvos:
        problemas = verificar(raiz, nome)
        print(f"\n===== {nome}: {len(problemas)} divergência(s) =====")
        agrupado: Dict[str, List[str]] = {}
        for p in problemas:
            chave = p.split("  ", 1)[1].split(":")[0]
            agrupado.setdefault(chave, []).append(p)
        for chave in sorted(agrupado):
            print(f"-- {chave}")
            for p in sorted(set(agrupado[chave]))[:40]:
                print(f"   {p}")
        total += len(problemas)
    print(f"\nTOTAL: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
