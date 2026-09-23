"""Internacionalização da interface do painel.

Idioma padrão: português (pt-br). Inglês e espanhol são opcionais e escolhidos
pelo seletor de bandeiras no topo do painel e no cabeçalho do login. A escolha é
persistida em SQLite (ver prefs.py), em cookies e em localStorage/sessionStorage,
sobrevivendo a troca de navegador, reinicializações e limpezas de sessão.

Os catálogos de tradução ficam armazenados em arquivos JSON em `locales/*.json`,
sendo carregados dinamicamente na inicialização e sob demanda via
`recarrega_traducoes()`.

Chave ausente numa tradução cai para o padrão, nunca para a chave crua.

O catálogo é o mesmo texto nos três irmãos. O nome do produto e o do gateway
nunca são escritos aqui: entram por interpolação a partir de `identidade.py`,
que é o único arquivo onde eles moram.
"""

import json
import pathlib
from typing import Dict

from .identidade import ROTULOS_DO_PRODUTO, NOME_DO_GATEWAY

DEFAULT_LANGUAGE = "pt"

# Código do idioma -> (rótulo nativo, classe de bandeira do flag-icons)
LANGUAGES: Dict[str, tuple] = {
    "pt": ("Português", "fi-br"),
    "en": ("English", "fi-us"),
    "es": ("Español", "fi-es"),
}

LOCALES_DIR = pathlib.Path(__file__).resolve().parent / "locales"


def carrega_catalogo(idioma: str) -> Dict[str, str]:
    """Carrega o catálogo do arquivo JSON, interpolando o gateway de identidade.py."""
    caminho = LOCALES_DIR / f"{idioma}.json"
    if caminho.is_file():
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                bruto = json.load(f)
                return {
                    k: str(v).replace("{NOME_DO_GATEWAY}", NOME_DO_GATEWAY)
                    for k, v in bruto.items()
                }
        except Exception:
            pass
    return {}


def carrega_todas_traducoes() -> Dict[str, Dict[str, str]]:
    """Carrega todos os idiomas suportados do disco."""
    traducoes: Dict[str, Dict[str, str]] = {}
    for code in LANGUAGES:
        traducoes[code] = carrega_catalogo(code)
    return traducoes


TRANSLATIONS: Dict[str, Dict[str, str]] = carrega_todas_traducoes()


def recarrega_traducoes() -> None:
    """Recarrega os catálogos JSON do disco em tempo de execução."""
    global TRANSLATIONS
    novas = carrega_todas_traducoes()
    for lang, catalogo in novas.items():
        if catalogo:
            TRANSLATIONS[lang] = catalogo


def normalize_language(code: str) -> str:
    """Normaliza um código de idioma para um dos suportados, caindo no padrão."""
    if not code:
        return DEFAULT_LANGUAGE
    base = str(code).strip().lower().replace("_", "-").split("-")[0]
    return base if base in LANGUAGES else DEFAULT_LANGUAGE


def translate(key: str, lang: str = DEFAULT_LANGUAGE, **params) -> str:
    """Traduz uma chave, com fallback para o idioma padrão e interpolação opcional."""
    lang = normalize_language(lang)
    # O rótulo do produto vem primeiro: são as poucas chaves que dependem do que
    # ESTE gateway faz, e elas moram em identidade.py justamente para que o
    # catálogo possa ser o mesmo texto nos três irmãos.
    text = ROTULOS_DO_PRODUTO.get(lang, {}).get(key)
    if text is None:
        text = TRANSLATIONS.get(lang, {}).get(key)
    if text is None:
        text = ROTULOS_DO_PRODUTO.get(DEFAULT_LANGUAGE, {}).get(key)
    if text is None:
        text = TRANSLATIONS.get(DEFAULT_LANGUAGE, {}).get(key)
    if text is None:
        text = ROTULOS_DO_PRODUTO.get("en", {}).get(key)
    if text is None:
        text = TRANSLATIONS.get("en", {}).get(key, key)
    if params:
        try:
            return text.format(**params)
        except (KeyError, IndexError):
            return text
    return text
