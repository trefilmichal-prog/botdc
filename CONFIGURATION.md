# Konfigurace

## Ollama

Bot používá Ollama pro proroctví a fallback překlad.

- `OLLAMA_URL` (default: `http://localhost:11434/api/generate`)
- `OLLAMA_TIMEOUT` (default: `120`)
- `OLLAMA_MODEL` (**podporováno pouze** `qwen3:4b`)

> ⚠️ Jakákoli jiná hodnota `OLLAMA_MODEL` je neplatná konfigurace.
> Cogy `ProphecyCog` a `AutoTranslateCog` při startu konfiguraci validují,
> zapíší jasnou chybu do logu a vyvolají chybu, aby bylo zřejmé,
> že je nutné použít model `qwen3:4b`.
