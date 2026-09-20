# Kochbuch → Tandoor Importer

Lädt ein PDF-Kochbuch hoch, lässt Claude die einzelnen Rezepte (inkl. Bilder,
Zutaten, Zeiten, Tags) erkennen, zeigt sie in einer Web-UI zur Auswahl/Korrektur
an und überträgt die ausgewählten Rezepte per API nach [Tandoor](https://tandoor.dev/).

## Setup

1. **`.env` anlegen**

   ```bash
   cp .env.example .env
   ```

   Trage ein:
   - `ANTHROPIC_API_KEY` – dein Claude-API-Key von [console.anthropic.com](https://console.anthropic.com)
   - `TANDOOR_URL` – URL deiner laufenden Tandoor-Instanz, **ohne** Slash am Ende
   - `TANDOOR_TOKEN` – API-Token aus Tandoor: *Benutzermenü → Einstellungen → API → "Neuen Token erstellen"*

2. **Starten**

   ```bash
   docker compose up --build -d
   ```

3. Die App läuft dann unter **http://localhost:8420** (Port in `docker-compose.yml` anpassbar).

## Ablauf

1. PDF hochladen (Drag & Drop oder Klick)
2. Claude liest das PDF seitenweise (in überlappenden Blöcken, damit auch dicke
   Kochbücher verarbeitet werden können), erkennt Rezepte inkl. Seitenbereich,
   **übersetzt alle Textfelder in die konfigurierte Sprache** (`OUTPUT_LANGUAGE`)
   und **rechnet US-/UK-Maßeinheiten in metrische Einheiten um** (`CONVERT_TO_METRIC`)
3. Zusätzlich wird einmalig ein **Kochbuch-Name vorgeschlagen** (aus Titelseite/Dateiname) –
   editierbar oben in der Review-Ansicht
4. Bilder aus dem PDF werden automatisch anhand der Seitenzahl den Rezepten zugeordnet –
   du kannst das Bild pro Rezept in der UI wechseln oder abwählen
5. In der Review-Ansicht: Rezepte einzeln oder gesammelt auswählen, Titel/Zutaten/
   Schritte/Zeiten direkt bearbeiten. Enthält der Originaltext wichtige Nebeninfos
   (Haltbarkeit, Einfrieren, Variationen, "am Vortag vorbereiten" …), landen diese
   automatisch als zusätzlicher, letzter Zubereitungsschritt "Hinweis"
6. **"In Tandoor importieren"** klicken – jedes Rezept bekommt einen Status
   (importiert / Fehler mit Meldung), wird dem oben angegebenen Kochbuch zugeordnet
   (wird automatisch angelegt, falls es noch nicht existiert), und am Ende erscheint
   ein Zusammenfassungs-Popup mit der Option, direkt das nächste PDF hochzuladen

Während der Analyse zeigt die App den Fortschritt an (z.B. "Seiten 25-36 analysiert
(3/8)"), da Claude das PDF in überlappenden Abschnitten verarbeitet. Vor dem
Import wird außerdem einmalig gegen die bereits in Tandoor vorhandenen Rezepte
abgeglichen: Bei (fast) exaktem Titel-Treffer wird das Rezept automatisch
abgewählt und deutlich markiert, bei nur ähnlichem Titel bleibt es ausgewählt,
erscheint aber mit einer Warnung.

## Konfigurierbare Optionen (`.env`)

| Variable | Standard | Bedeutung |
|---|---|---|
| `OUTPUT_LANGUAGE` | `Deutsch` | Zielsprache für Titel, Beschreibung, Zutaten, Schritte, Tags **und UI-Sprache dieser App** – unabhängig von der Sprache des Original-PDFs |
| `CONVERT_TO_METRIC` | `true` | Rechnet cups/oz/lb/°F/inch automatisch in g/ml/°C/cm um |
| `CHECK_DUPLICATES` | `true` | Gleicht vor dem Import Rezepttitel gegen bereits in Tandoor vorhandene Rezepte ab |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Welches Claude-Modell für Extraktion + Kochbuch-Titel-Vorschlag genutzt wird |
| `MAX_UPLOAD_MB` | `100` | Maximale PDF-Größe |

### Zur UI-Sprache

`OUTPUT_LANGUAGE` steuert **beides**: die Sprache, in die Claude die Rezepte
schreibt/übersetzt, UND die Sprache der Weboberfläche selbst (Buttons, Labels,
Meldungen). Fertige UI-Übersetzungen liegen für **Deutsch, English, Français,
Italiano, Español** vor (`backend/app/static/i18n.js`). Trägst du einen anderen
Sprachnamen ein (z.B. `Polski`), funktioniert die Rezept-Übersetzung trotzdem
(Claude versteht praktisch jede Sprache), die Oberfläche selbst zeigt dann aber
Englisch, weil dafür keine Übersetzungstabelle hinterlegt ist. Weitere Sprachen
lassen sich einfach ergänzen: neuer Eintrag in `TRANSLATIONS` in `i18n.js` +
Eintrag in `SUPPORTED_UI_LANGUAGES` in `config.py`.

Ist das Original-PDF bereits (erkennbar) in der Zielsprache verfasst, überspringt
das System automatisch die "Übersetzungs-Anweisung" an Claude (Spracherkennung
per `langdetect`, lokal, ohne zusätzlichen API-Call) – das vermeidet unnötig
umformulierten Text und spart etwas Zeit/Tokens.

## ⚠️ Wichtiger Hinweis zur Tandoor-API

Tandoor entwickelt sich weiter und die genauen Feldnamen der API (`/api/recipe/`,
`/api/food/`, `/api/unit/`, `/api/keyword/`) können sich je nach Version leicht
unterscheiden. `backend/app/tandoor_client.py` ist so geschrieben, dass es gegen
die gängige, dokumentierte REST-API arbeitet (Zutaten/Einheiten/Tags werden per
"get-or-create" angelegt, dann per ID **und** Name referenziert – manche Tandoor-
Versionen verlangen bei verschachtelten Objekten beides). Für das Kochbuch-Feature
wird sowohl `/api/recipe-book/` als auch `/api/cookbook/` probiert (Tandoor hat den
Endpunkt-Namen im Laufe der Versionen geändert), ebenso für die Rezept-Zuordnung
(`/api/recipe-book-entry/`, `/api/cookbook-recipe/`, `/api/cookbookrecipe/`). Falls
keiner davon zu deiner Version passt, wird das Rezept trotzdem importiert – die
Kochbuch-Zuordnung erscheint dann nur als Warnung im Import-Popup/an der Rezeptzeile.

Falls der Import mit einem Fehler wie *"Tandoor lehnte das Rezept ab (400): ..."*
fehlschlägt:

1. Öffne die Swagger-UI deiner eigenen Instanz: `https://DEINE-TANDOOR-URL/api/schema/swagger-ui/`
   (oder `/api/docs/`) und schau dir das exakte Schema von `POST /api/recipe/` an.
2. Die Fehlermeldung aus Tandoor wird 1:1 in der UI angezeigt (Tandoor gibt meist
   das fehlerhafte Feld zurück) – damit lässt sich `_build_recipe_payload()` in
   `tandoor_client.py` gezielt anpassen.
3. Gib mir (Claude) einfach die exakte Fehlermeldung oder das Swagger-Schema,
   dann passe ich den Client entsprechend an.

## Architektur

```
backend/
  app/
    main.py           FastAPI-Endpunkte, liefert auch das Frontend aus
    pdf_processor.py  Text-/Bildextraktion aus dem PDF (PyMuPDF)
    ai_extractor.py   Claude-Aufruf zur Rezepterkennung, Chunking + Dedupe + Sprach-Erkennung
    tandoor_client.py Tandoor-API-Client (get-or-create + Rezept-Import + Bild-Upload + Kochbuch + Duplikat-Abgleich)
    jobs.py           In-Memory Job-/State-Verwaltung
    schemas.py         Pydantic-Datenmodelle
    config.py          Settings + zentrale Sprachzuordnung (Name -> ISO-Code)
    static/           Vanilla-JS-Frontend (index.html, app.js, i18n.js, style.css)
```

Es gibt bewusst **keine Datenbank** – Jobs leben nur im Arbeitsspeicher des
Containers (PDFs/Bilder liegen im Volume `cookbook_data` unter `/app/data/<job_id>`).
Für den beschriebenen Anwendungsfall (PDF hochladen → sichten → importieren,
danach fertig) reicht das; bei einem Neustart des Containers gehen offene,
noch nicht importierte Jobs verloren.

## Bekannte Grenzen / mögliche nächste Schritte

- Sehr große PDFs (>200 Seiten) verursachen entsprechend viele Claude-Aufrufe –
  ggf. `pages_per_chunk` in `ai_extractor.py` erhöhen, um Kosten/Zeit zu sparen
  (Trade-off: höheres Risiko, dass ein Rezept die Kontextgrenze sprengt)
- Bild-Zuordnung ist heuristisch (nächstgelegenes Bild im Seitenbereich) – bei
  Kochbüchern mit vielen Deko-/Zutatenbildern pro Doppelseite lohnt sich der
  manuelle Blick in der UI
- Der Duplikat-Abgleich vergleicht nur Titel (exakt + Ähnlichkeit via `difflib`),
  nicht Zutaten/Inhalt – zwei Rezepte mit komplett unterschiedlichem Namen, aber
  gleichem Inhalt, werden nicht erkannt
- Kein Auth/Login für die Web-UI selbst – falls die App nicht nur lokal läuft,
  sollte davor ein Reverse Proxy mit Basic Auth o.ä. gesetzt werden
- Rezepte zusammenführen/teilen ist in der UI noch nicht möglich (falls Claude
  ein Rezept fälschlich in zwei Teile zerlegt oder umgekehrt)
