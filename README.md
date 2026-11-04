# Anki Automation

Python tools to prepare language-learning notes from reviewed source records.
Validate JSONL, export an Anki TSV file, or import notes through Anki-Connect API v1.
The application uses the Python standard library. It has no runtime dependencies.

## Start

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) **0.13.7 or later
in the 0.13 series**. Python **3.15** is required. `uv` can install it for you.

```sh
uv python install
uv sync --locked
uv run --locked anki-automation --help
uv run --locked anki-automation validate examples/cards.jsonl
uv run --locked anki-automation export examples/cards.jsonl \
  --deck 'Languages::English' --output exports/english.tsv
```

Open the TSV file with Anki's text import. It includes UTF-8 text, HTML settings,
the `Basic` note type, the deck name, and a tags column. Anki's import screen controls
whether existing notes are updated. Review those settings before you confirm.
Export creates parent directories and refuses to replace an existing file.

## Source records

Save one JSON object per line in a UTF-8 `.jsonl` file:

```json
{"term":"take your time","meaning":"не торопись","example":"Take your time. We can wait.","source":"Original example","tags":["lang::en","phrases"]}
```

| Field | Contract |
| --- | --- |
| `term` | Required non-empty expression or question. Becomes `Front`. |
| `meaning` | Required non-empty meaning or answer. Starts `Back`. |
| `example` | Optional example text. Added to `Back`. |
| `source` | Optional source title, URL, or citation. Added as text to `Back`. |
| `tags` | Optional array of tags. Use letters, digits, `_`, `:`, or `-`. |

Input fields contain plain text. The renderer escapes HTML and preserves line
breaks. It does not fetch source URLs. Unicode is normalized to NFC, outer
whitespace is removed, and tags are sorted and deduplicated. Empty lines are
ignored. Exact repeated records are merged. Conflicting records for the same term,
unknown fields, duplicate JSON keys, and invalid values fail with a file and line
number. Terms are case-sensitive. Use one language per file and a separate deck
for each language; combine different meanings of the same term in one record.

Keep personal input under ignored `data/` and generated files under `exports/`.
Only synthetic examples belong in Git. Review translations and examples before
you import them. This initial version does not generate translations, call a model,
extract PDF or web content, or download audio.

## Anki-Connect

Use Anki Desktop **26.08 or later** and a current
[Anki-Connect](https://git.sr.ht/~foosoft/anki-connect) add-on with API v1.
Create the target deck and a note type named `Basic` with fields named `Front`
and `Back`. Names are exact; localized note type names must be changed in Anki.
Open the intended profile and close its note editors.

```sh
# Preview only: prints the batch and makes no network request.
uv run --locked anki-automation anki-import examples/cards.jsonl \
  --deck 'Languages::English'

# Explicit write into the active profile.
uv run --locked anki-automation anki-import examples/cards.jsonl \
  --deck 'Languages::English' --apply
```

The client uses `http://127.0.0.1:8765/api/v1`. Use `--port` for another local port
and `--timeout` for the socket timeout in seconds. Set `ANKI_CONNECT_KEY` in your
environment if the add-on has an API key. The client sends it in a Bearer header.
Keys are excluded from preview output and the client representation.

Import pins the active collection session, sets `atomic: true`, and rejects
duplicates in the target deck. A rejected atomic batch makes no collection changes.
It does not update existing notes, create decks, change scheduling, or sync AnkiWeb.
Writes are never retried. If a response is lost or invalid, inspect Anki before
you run the import again: the server may have committed the batch.

Commands print JSON to stdout. Application errors print JSON to stderr and return
exit code 1. Argument errors return exit code 2. Preview output contains card text;
store it with the same care as the source file.

## Development

```sh
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked ty check
uv run --locked pytest --cov --cov-report=term-missing
uv run --locked pre-commit install
uv run --locked pre-commit run --all-files
uv build
```

On a Mac with `build-gate`, run package builds through `build-gate -- uv build`.
The lock file records exact development dependencies and hashes. Commit `uv.lock`
with dependency changes. Use `uv lock --upgrade` to update dependencies, then run
the checks. Ruff handles lint and formatting; ty checks types. Tests use temporary
files and a local HTTP protocol server. They do not open or change a real collection.
Passing protocol tests does not prove compatibility with an installed Anki add-on.

CI runs lint, format, type checks, tests with branch coverage on Linux, macOS, and
Windows, package build and installation checks, and secret scanning. Dependencies
and Actions receive weekly Dependabot updates. CI checks changes; it does not
publish packages or import notes. Read [AGENTS.md](AGENTS.md) before agent work.

## References

- [uv project guide](https://docs.astral.sh/uv/guides/projects/)
- [Ruff configuration](https://docs.astral.sh/ruff/configuration/)
- [ty configuration](https://docs.astral.sh/ty/configuration/)
- [Anki text import](https://docs.ankiweb.net/manual/importing/text-files)
- [Anki-Connect API v1](https://git.sr.ht/~foosoft/anki-connect/blob/master/API_V1.md)

MIT license. See [LICENSE](LICENSE).
