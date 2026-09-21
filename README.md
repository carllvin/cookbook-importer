# Cookbook → Tandoor Importer

Upload a PDF cookbook, let an AI (Claude, ChatGPT, or Gemini) detect the
individual recipes — including images, ingredients, times, and tags — review
and adjust them in a web UI, and import the selected ones into
[Tandoor](https://tandoor.dev/) via its API.

## Setup

1. **Create `.env`**

   ```bash
   cp .env.example .env
   ```

   Fill in:
   - `AI_PROVIDER` – `anthropic` (Claude), `openai` (ChatGPT), or `gemini` (Google) – see [Switching AI providers](#switching-ai-providers) below
   - the API key for the provider you chose (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY`)
   - `TANDOOR_URL` – the URL of your running Tandoor instance, **without** a trailing slash
   - `TANDOOR_TOKEN` – API token from Tandoor: *user menu → settings → API → "create new token"*

2. **Start it**

   ```bash
   docker compose up --build -d
   ```

3. The app runs at **http://localhost:8420** (change the port in `docker-compose.yml` if you like).


## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `AI_PROVIDER` | `anthropic` | Which AI provider handles recipe extraction: `anthropic`, `openai`, or `gemini` |
| `ANTHROPIC_API_KEY` / `CLAUDE_MODEL` | – / `claude-sonnet-4-6` | Only relevant when `AI_PROVIDER=anthropic` |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | – / `gpt-4o` | Only relevant when `AI_PROVIDER=openai` |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | – / `gemini-2.5-flash` | Only relevant when `AI_PROVIDER=gemini` |
| `OUTPUT_LANGUAGE` | `English` | Target language for title, description, ingredients, steps, tags **and this app's UI language** — independent of the source PDF's language |
| `CONVERT_TO_METRIC` | `true` | Converts cups/oz/lb/°F/inch to g/ml/°C/cm automatically |
| `CHECK_DUPLICATES` | `true` | Checks recipe titles against those already in Tandoor before import |
| `REUSE_EXISTING_TAGS` | `true` | Fetches existing Tandoor tags and asks the AI to prefer reusing them |
| `CUSTOM_INSTRUCTIONS` | *(empty)* | Free-text instructions appended to the extraction prompt — see [Custom instructions](#custom-instructions) below |
| `JOB_RETENTION_HOURS` | `48` | Deletes jobs (and their uploaded PDF/images) from disk after this many hours |
| `MAX_UPLOAD_MB` | `100` | Maximum PDF upload size |

### Custom instructions

`CUSTOM_INSTRUCTIONS` lets you append your own free-text rules to the
extraction prompt without touching the code, e.g.:

```bash
CUSTOM_INSTRUCTIONS="Always add the tag 'family-recipe'. If servings aren't stated, assume 4. Keep step instructions under two sentences."
```

These are appended verbatim and explicitly take precedence over the built-in
guidance when they conflict, so use them to override defaults you don't like
(e.g. a different tagging convention) rather than just to add minor notes.
