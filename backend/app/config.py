from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Anthropic / Claude
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"

    # Tandoor
    tandoor_url: str = ""      # z.B. https://rezepte.meinserver.de (ohne Slash am Ende)
    tandoor_token: str = ""    # API-Token aus Tandoor: Benutzereinstellungen -> API

    # Ausgabe-Einstellungen
    output_language: str = "Deutsch"   # Zielsprache fuer Titel/Beschreibung/Zutaten/Schritte UND UI-Sprache
    convert_to_metric: bool = True     # US-/UK-Einheiten (cups, oz, lb, °F, inch) in metrisch umrechnen
    check_duplicates: bool = True      # vor dem Import per Titel in Tandoor nach evtl. bereits vorhandenen Rezepten suchen

    # App
    data_dir: str = "/app/data"
    max_upload_mb: int = 100

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()


# ---------- Sprachzuordnung (Name aus .env -> ISO-639-1-Code) ----------
# Wird sowohl fuer die Uebersetzungsanweisung an Claude als auch fuer die
# UI-Sprache verwendet, damit beides konsistent aus OUTPUT_LANGUAGE folgt.
LANGUAGE_NAME_TO_CODE = {
    "deutsch": "de", "german": "de", "allemand": "de", "tedesco": "de", "aleman": "de", "alemán": "de",
    "englisch": "en", "english": "en", "anglais": "en", "inglese": "en", "ingles": "en", "inglés": "en",
    "französisch": "fr", "franzosisch": "fr", "french": "fr", "français": "fr", "francais": "fr",
    "francese": "fr", "frances": "fr", "francés": "fr",
    "italienisch": "it", "italian": "it", "italien": "it", "italiano": "it",
    "spanisch": "es", "spanish": "es", "español": "es", "espanol": "es", "espagnol": "es", "spagnolo": "es",
    "niederländisch": "nl", "niederlaendisch": "nl", "dutch": "nl", "nederlands": "nl", "néerlandais": "nl",
    "polnisch": "pl", "polish": "pl", "polski": "pl",
    "portugiesisch": "pt", "portuguese": "pt", "português": "pt", "portugues": "pt",
}

# Sprachen, fuer die es fertige UI-Uebersetzungen im Frontend gibt.
# Alles andere faellt auf Englisch zurueck (funktioniert, ist nur nicht lokalisiert).
SUPPORTED_UI_LANGUAGES = {"de", "en", "fr", "it", "es"}


def get_language_code(name: str) -> str | None:
    """Liefert den ISO-Code fuer einen Sprachnamen, oder None falls unbekannt (keine Annahme!)."""
    return LANGUAGE_NAME_TO_CODE.get((name or "").strip().lower())


def get_ui_language_code(name: str) -> str:
    """Liefert die UI-Sprache: bekannte, unterstuetzte Sprache -> deren Code, sonst Fallback 'en'."""
    code = get_language_code(name)
    return code if code in SUPPORTED_UI_LANGUAGES else "en"
