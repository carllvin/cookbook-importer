# Cookbook → Tandoor Importer

Turn a **PDF cookbook into a Tandoor recipe collection**.

Upload a cookbook and let an AI model automatically identify individual recipes, extract their ingredients and instructions, find recipe images, calculate metadata, and apply tags. Review and edit the results in a web interface, then import the recipes you want directly into [Tandoor](https://tandoor.dev/) via its API.

**Supported AI providers:** Claude · ChatGPT · Gemini

<p align="center">
  <img src="screenshots/main_page.png" alt="Main page" width="85%">
</p>

<p align="center">
  <img src="screenshots/preview_page_1.png" alt="Recipe preview" width="85%">
</p>

<p align="center">
  <img src="screenshots/preview_page_2.png" alt="Recipe preview" width="85%">
</p>

<p align="center">
  <img src="screenshots/succesful_import.png" alt="Successful Tandoor import" width="85%">
</p>

---

## ✨ Features

* 📖 **PDF cookbook import** — upload an entire cookbook at once
* 🤖 **AI-powered recipe extraction** — automatically identifies individual recipes
* 🖼️ **Recipe images** — extracts and associates images with recipes
* 🥕 **Ingredients & instructions** — structured into Tandoor-compatible recipes
* ⏱️ **Recipe metadata** — cooking time, preparation time, servings, etc.
* 🏷️ **Automatic tagging** — generate tags and optionally reuse existing Tandoor tags
* 🌍 **Language conversion** — output recipes in a language of your choice
* ⚖️ **Metric conversion** — automatically convert imperial measurements to metric
* 🔍 **Duplicate detection** — check existing Tandoor recipes before importing
* ✏️ **Review & edit** — inspect and modify extracted recipes before importing
* 🚀 **Bulk import** — select multiple recipes and import them into Tandoor
* 🔌 **Multiple AI providers** — choose Claude, ChatGPT, or Gemini
* 🐳 **Docker-based** — simple setup with Docker Compose

---

## 🚀 Setup

### Requirements

* Docker
* Docker Compose
* A running [Tandoor](https://tandoor.dev/) instance
* An API key for one of the supported AI providers

### 1. Create your environment file

```bash
cp .env.example .env
```

Edit `.env` and configure:

```env
AI_PROVIDER=anthropic

ANTHROPIC_API_KEY=your-api-key

TANDOOR_URL=http://your-tandoor-instance
TANDOOR_TOKEN=your-tandoor-token
```

See [Configuration](#configuration) for all available options.

### 2. Start the application

```bash
docker compose up --build -d
```

### 3. Open the web interface

Go to:

**http://localhost:8420**

The port can be changed in `docker-compose.yml`.

---

## 🔑 Tandoor API Token

The importer communicates with Tandoor through its API.

In Tandoor, create an API token under:

**User menu → Settings → API → Create new token**

Then add the token to `.env`:

```env
TANDOOR_TOKEN=your-token
```

`TANDOOR_URL` should contain the base URL of your Tandoor instance **without a trailing slash**:

```env
# Correct
TANDOOR_URL=http://192.168.1.100:8080

# Avoid
TANDOOR_URL=http://192.168.1.100:8080/
```

---

## ⚙️ Configuration

All configuration is handled through `.env`.

| Variable              | Default             | Description                                                   |
| --------------------- | ------------------- | ------------------------------------------------------------- |
| `AI_PROVIDER`         | `anthropic`         | AI provider: `anthropic`, `openai`, or `gemini`               |
| `ANTHROPIC_API_KEY`   | —                   | API key for Anthropic                                         |
| `CLAUDE_MODEL`        | `claude-sonnet-4-6` | Claude model to use                                           |
| `OPENAI_API_KEY`      | —                   | API key for OpenAI                                            |
| `OPENAI_MODEL`        | `gpt-4o`            | OpenAI model to use                                           |
| `GEMINI_API_KEY`      | —                   | API key for Google Gemini                                     |
| `GEMINI_MODEL`        | `gemini-2.5-flash`  | Gemini model to use                                           |
| `TANDOOR_URL`         | —                   | URL of your Tandoor instance                                  |
| `TANDOOR_TOKEN`       | —                   | Tandoor API token                                             |
| `OUTPUT_LANGUAGE`     | `English`           | Language for extracted recipes **and the application's UI**   |
| `CONVERT_TO_METRIC`   | `true`              | Convert cups, oz, lb, °F, inches, etc. to metric units        |
| `CHECK_DUPLICATES`    | `true`              | Check recipe titles against existing Tandoor recipes          |
| `REUSE_EXISTING_TAGS` | `true`              | Prefer existing Tandoor tags when generating tags             |
| `CUSTOM_INSTRUCTIONS` | —                   | Additional instructions appended to the extraction prompt     |
| `JOB_RETENTION_HOURS` | `48`                | How long completed jobs and their uploaded files are retained |
| `MAX_UPLOAD_MB`       | `100`               | Maximum PDF upload size                                       |

### AI Providers

The extraction pipeline supports three providers:

| Provider           | `AI_PROVIDER` | API Key             |
| ------------------ | ------------- | ------------------- |
| Anthropic / Claude | `anthropic`   | `ANTHROPIC_API_KEY` |
| OpenAI / ChatGPT   | `openai`      | `OPENAI_API_KEY`    |
| Google Gemini      | `gemini`      | `GEMINI_API_KEY`    |

Only the configuration for the selected provider is used.

For example, to use Gemini:

```env
AI_PROVIDER=gemini
GEMINI_API_KEY=your-api-key
GEMINI_MODEL=gemini-2.5-flash
```

---

## 🌍 Output Language

`OUTPUT_LANGUAGE` controls both the extracted recipe content **and the application's UI language**.

It is independent of the language of the cookbook.

For example, you can upload a German cookbook and request English output:

```env
OUTPUT_LANGUAGE=English
```

This affects:

* Recipe titles
* Descriptions
* Ingredients
* Instructions
* Tags
* Application UI

---

## ⚖️ Metric Conversion

Set:

```env
CONVERT_TO_METRIC=true
```

to automatically convert common imperial measurements such as:

* cups → ml
* oz → g/ml
* lb → g
* °F → °C
* inches → cm

Disable it with:

```env
CONVERT_TO_METRIC=false
```

to preserve the measurements from the original cookbook.

---

## 🏷️ Tags

When enabled:

```env
REUSE_EXISTING_TAGS=true
```

the importer retrieves existing tags from Tandoor and provides them to the AI as context.

The AI can then prefer existing tags rather than creating unnecessary duplicates.

You can also define your own tagging rules using [`CUSTOM_INSTRUCTIONS`](#custom-instructions).

---

## 🔍 Duplicate Detection

With:

```env
CHECK_DUPLICATES=true
```

the importer checks recipe titles against recipes already present in Tandoor before importing.

This is particularly useful when processing multiple cookbooks or re-processing a cookbook.

---

## ✏️ Custom Instructions

`CUSTOM_INSTRUCTIONS` allows you to add your own rules to the AI extraction prompt without modifying the source code.

For example:

```env
CUSTOM_INSTRUCTIONS="Always add the tag 'family-recipe'. If servings aren't stated, assume 4. Keep step instructions under two sentences."
```

Custom instructions are appended **verbatim** and are explicitly given priority over the built-in extraction guidance when the two conflict.

This makes them useful for changing the importer's default behavior, for example:

* Custom tagging conventions
* Default serving sizes
* Formatting preferences
* Ingredient naming conventions
* Instruction length
* Cookbook-specific rules

---

## 🗑️ Job Retention

Uploaded PDFs and extracted images are stored temporarily while a job is being processed.

By default, completed jobs are automatically removed after **48 hours**:

```env
JOB_RETENTION_HOURS=48
```

---

## 📦 Upload Limits

The default maximum PDF size is:

```env
MAX_UPLOAD_MB=100
```

Increase this value if you need to process larger cookbooks.

---

## 🛠️ Development

Start the application:

```bash
docker compose up --build
```

Run it in the background:

```bash
docker compose up --build -d
```

View logs:

```bash
docker compose logs -f
```

Stop the application:

```bash
docker compose down
```

---

## 🤝 Contributing

Contributions, bug reports, and ideas are welcome.

If you find a problem or have an idea for improving the importer, please open an issue or submit a pull request.
