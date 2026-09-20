from __future__ import annotations

import json
import os
import re
import uuid
from typing import Callable, Optional

import anthropic

from .config import settings, get_language_code
from .schemas import ExtractedRecipe


def _detect_source_language(pages: list[dict]) -> Optional[str]:
    """Erkennt die Sprache der ersten Seiten (ISO-639-1), ohne die Claude-API zu bemuehen.
    Liefert None, falls das nicht zuverlaessig moeglich ist (z.B. zu wenig Text)."""
    sample = " ".join(p["text"] for p in pages[:3])[:3000].strip()
    if len(sample) < 50:
        return None
    try:
        from langdetect import detect, LangDetectException
        try:
            return detect(sample)
        except LangDetectException:
            return None
    except ImportError:
        return None


def _build_system_prompt(same_language: bool) -> str:
    language = settings.output_language.strip() or "Deutsch"

    unit_instruction = (
        f"WICHTIG - Einheiten: Rechne alle US-/UK-typischen Maßeinheiten (cups, "
        f"fluid ounces, oz, lb/pounds, °F, Zoll/inch fuer Formgroessen) in "
        f"metrische Einheiten um (g, ml, °C, cm). Nutze realistische, in der "
        f"Kueche uebliche Umrechnungswerte (z.B. 1 cup Mehl ≈ 120 g, 1 cup "
        f"Zucker ≈ 200 g, 1 cup Butter ≈ 227 g, 1 cup Fluessigkeit ≈ 240 ml, "
        f"1 oz ≈ 28 g, 1 lb ≈ 454 g, °F -> °C mit (F-32)*5/9). Runde sinnvoll "
        f"(z.B. auf 5 oder 10 g/ml genau). Gib das Ergebnis der Umrechnung aus, "
        f"nicht die Originaleinheit. Im deutschsprachigen Raum bereits "
        f"gebraeuchliche Masse wie TL/EL/Prise bleiben unveraendert."
        if settings.convert_to_metric else
        "Einheiten: Uebernimm die im Originaltext verwendeten Maßeinheiten "
        "unveraendert (keine Umrechnung)."
    )

    if same_language:
        # Das Kochbuch ist bereits (vermutlich) in der Zielsprache verfasst -> keine
        # Uebersetzung noetig. Das spart Claude unnoetige "Umschreib-Arbeit" und
        # vermeidet, dass Formulierungen unnoetig veraendert werden.
        language_instruction = (
            f"WICHTIG - Sprache: Das Kochbuch ist bereits auf {language} verfasst. "
            f"Schreibe alle Textfelder (title, description, Zutatennamen, Einheiten, "
            f"Notizen, Zubereitungsschritte, Tags) UNVERÄNDERT auf {language} - "
            f"keine Übersetzung nötig. Korrigiere nur offensichtliche OCR-/Scanfehler "
            f"und glätte über Zeilenumbrüche getrennte Wörter, ohne den Wortlaut "
            f"inhaltlich zu verändern."
        )
    else:
        language_instruction = (
            f"WICHTIG - Sprache: Schreibe ALLE Textfelder (title, description, "
            f"Zutatennamen, Einheiten, Notizen, Zubereitungsschritte, Tags) auf "
            f"{language} - unabhängig davon, in welcher Sprache das Original-Kochbuch "
            f"verfasst ist. Übersetze natürlich und in gebräuchlicher Kochbuch-Sprache, "
            f"keine wörtliche Übersetzung."
        )

    return f"""Du bist Experte für das Digitalisieren von Kochbüchern.
Du bekommst den Text mehrerer aufeinanderfolgender PDF-Seiten eines Kochbuchs, \
jede Seite beginnt mit einer Markierung "===== SEITE n =====". Das Kochbuch \
kann in einer beliebigen Sprache verfasst sein.

Deine Aufgabe: Identifiziere jedes einzelne Rezept in diesem Textausschnitt und \
extrahiere es strukturiert. Ignoriere Inhaltsverzeichnis, Register, Vorwort, \
Werbung und reine Deko-/Kapitelseiten ohne konkretes Rezept.

{language_instruction}

{unit_instruction}

Ein Rezept kann sich über mehrere Seiten erstrecken oder mehrere Rezepte können \
auf einer Seite stehen. Nutze die Seitenmarkierungen, um source_page_start und \
source_page_end korrekt zu bestimmen (jeweils die tatsächliche Seitenzahl, nicht \
die Position im Text).

Falls ein Rezept am Rand des Textausschnitts beginnt oder endet und daher \
unvollständig wirkt, extrahiere trotzdem so viel wie möglich - das System \
verarbeitet die Seiten in überlappenden Abschnitten, unvollständige Duplikate \
werden später zusammengeführt.

WICHTIG - Nebeninformationen: Enthält der Rezepttext wichtige Zusatzinfos, die \
nicht Teil der eigentlichen Zubereitung sind - z.B. Haltbarkeit/Lagerung, \
Einfrieren, Aufbewahrungstipps, Variationsvorschläge, Allergiehinweise, "am \
Vortag vorbereiten" - füge dafür einen ZUSÄTZLICHEN, letzten Schritt mit dem \
title "Hinweis" hinzu, der diese Informationen zusammenfasst. Wenn es keine \
solchen Zusatzinfos gibt, füge keinen Hinweis-Schritt hinzu.

Zeiten (prep_time_minutes, cook_time_minutes, total_time_minutes) immer in \
Minuten als Ganzzahl, wenn nicht angegeben: null.

tags: kurze, allgemeine Schlagworte auf {language} (z.B. "vegetarisch", "Dessert", \
"schnell"), maximal 5, nur wenn aus dem Text ableitbar oder sehr naheliegend.

Antworte AUSSCHLIESSLICH mit einem JSON-Array (keine Erklärungen, kein \
Markdown-Codeblock), jedes Element nach folgendem Schema:

{{
  "title": string,
  "description": string | null,
  "servings": integer | null,
  "prep_time_minutes": integer | null,
  "cook_time_minutes": integer | null,
  "total_time_minutes": integer | null,
  "tags": string[],
  "ingredients": [
    {{"amount": number | null, "unit": string | null, "name": string, "note": string | null, "group": string | null}}
  ],
  "steps": [
    {{"title": string | null, "instruction": string, "time_minutes": integer | null}}
  ],
  "source_page_start": integer,
  "source_page_end": integer
}}

Wenn im Textausschnitt kein einziges Rezept vorkommt, antworte mit [].
"""


def guess_cookbook_title(pages: list[dict], filename: str) -> str:
    """Leitet einen Vorschlag für den Kochbuch-Namen aus den ersten Seiten (Titelseite) ab,
    mit Fallback auf den Dateinamen falls Claude nicht verfügbar ist oder nichts liefert."""
    fallback = (
        os.path.splitext(filename)[0].replace("_", " ").replace("-", " ").strip().title()
        or "Importiertes Kochbuch"
    )

    if not settings.anthropic_api_key:
        return fallback

    intro_text = "".join(
        f"\n\n===== SEITE {p['page']} =====\n{p['text']}" for p in pages[:3]
    ).strip()
    if not intro_text:
        return fallback

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=settings.claude_model,
            max_tokens=60,
            messages=[{
                "role": "user",
                "content": (
                    f"Das sind die ersten Seiten eines eingescannten Kochbuch-PDFs "
                    f"(Dateiname: \"{filename}\"):\n\n{intro_text}\n\n"
                    f"Nenne NUR den wahrscheinlichen Titel dieses Kochbuchs als "
                    f"einfachen Klartext auf {settings.output_language.strip() or 'Deutsch'} "
                    f"(keine Anführungszeichen, keine Erklärung, kein Untertitel). "
                    f"Falls kein Titel erkennbar ist, leite einen kurzen, sinnvollen "
                    f"Namen aus dem Dateinamen ab."
                ),
            }],
        )
        text_out = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip().strip('"').strip("'").strip()
        return text_out or fallback
    except Exception:
        return fallback


def _extract_json_array(raw: str) -> list:
    raw = raw.strip()
    # Falls Claude trotz Anweisung Codeblock-Markierungen liefert, entfernen
    raw = re.sub(r"^```(json)?", "", raw.strip())
    raw = re.sub(r"```$", "", raw.strip())
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: größtes [...]-Segment im Text suchen
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def _chunk_pages(pages: list[dict], pages_per_chunk: int = 12, overlap: int = 2):
    i = 0
    n = len(pages)
    while i < n:
        end = min(i + pages_per_chunk, n)
        yield pages[i:end]
        if end >= n:
            break
        i = end - overlap


ProgressCallback = Callable[[int, int, int, int], None]  # (chunk_idx, total_chunks, page_start, page_end)


def extract_recipes_from_pages(
    pages: list[dict],
    on_progress: Optional[ProgressCallback] = None,
) -> list[ExtractedRecipe]:
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY ist nicht gesetzt. Bitte in der .env-Datei konfigurieren."
        )

    # Einmal pro Job pruefen, ob das Kochbuch schon in der Zielsprache verfasst ist -
    # dann muss Claude nicht "uebersetzen", sondern nur strukturieren (siehe _build_system_prompt).
    source_lang = _detect_source_language(pages)
    target_lang = get_language_code(settings.output_language)
    same_language = bool(source_lang and target_lang and source_lang == target_lang)
    system_prompt = _build_system_prompt(same_language)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    chunks = list(_chunk_pages(pages))
    total_chunks = len(chunks) or 1
    raw_recipes: list[dict] = []

    for idx, chunk in enumerate(chunks, start=1):
        chunk_text = "".join(
            f"\n\n===== SEITE {p['page']} =====\n{p['text']}" for p in chunk
        )
        page_start, page_end = chunk[0]["page"], chunk[-1]["page"]

        if not chunk_text.strip():
            if on_progress:
                on_progress(idx, total_chunks, page_start, page_end)
            continue

        response = client.messages.create(
            model=settings.claude_model,
            max_tokens=8000,
            system=system_prompt,
            messages=[{"role": "user", "content": chunk_text}],
        )
        text_out = "".join(
            block.text for block in response.content if block.type == "text"
        )
        try:
            parsed = _extract_json_array(text_out)
        except Exception:
            # Diesen Chunk überspringen statt den ganzen Job abzubrechen
            parsed = []

        raw_recipes.extend(parsed)

        if on_progress:
            on_progress(idx, total_chunks, page_start, page_end)

    return _dedupe_recipes(raw_recipes)


def _dedupe_recipes(raw_recipes: list[dict]) -> list[ExtractedRecipe]:
    """Entfernt Duplikate, die durch überlappende Chunks entstehen (gleicher Titel + überschneidender Seitenbereich)."""
    results: list[ExtractedRecipe] = []

    def overlaps(a_start, a_end, b_start, b_end) -> bool:
        return a_start <= b_end and b_start <= a_end

    for item in raw_recipes:
        try:
            recipe = ExtractedRecipe(
                id=uuid.uuid4().hex[:10],
                title=item.get("title") or "Unbenanntes Rezept",
                description=item.get("description"),
                servings=item.get("servings"),
                prep_time_minutes=item.get("prep_time_minutes"),
                cook_time_minutes=item.get("cook_time_minutes"),
                total_time_minutes=item.get("total_time_minutes"),
                tags=item.get("tags") or [],
                ingredients=item.get("ingredients") or [],
                steps=item.get("steps") or [],
                source_page_start=item.get("source_page_start") or 1,
                source_page_end=item.get("source_page_end") or item.get("source_page_start") or 1,
            )
        except Exception:
            continue

        is_duplicate = False
        for existing in results:
            same_title = existing.title.strip().lower() == recipe.title.strip().lower()
            pages_overlap = overlaps(
                existing.source_page_start, existing.source_page_end,
                recipe.source_page_start, recipe.source_page_end,
            )
            if same_title and pages_overlap:
                # Die Version mit mehr Details (mehr Zutaten/Schritte) behalten
                if len(recipe.ingredients) + len(recipe.steps) > len(existing.ingredients) + len(existing.steps):
                    results[results.index(existing)] = recipe
                is_duplicate = True
                break

        if not is_duplicate:
            results.append(recipe)

    # Nach Seitenzahl sortieren, damit die UI-Liste der Buchreihenfolge folgt
    results.sort(key=lambda r: (r.source_page_start, r.source_page_end))
    return results
