# Anki Automation

Python tools to prepare language-learning notes from reviewed source records.
Validate JSONL, export an Anki TSV file, or import notes through Anki-Connect API v1.
The client supports all 96 methods in the pinned Anki-Connect API v1 schema.
It uses standard-library HTTP and `jsonschema` for contract checks.

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
the `Replika · Meaning` note type, the deck name, and a tags column. Anki's import screen controls
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
| `source` | Optional source title, URL, or citation. Becomes `Source`; templates show it in a collapsed block. Use `Title — https://example.org/page` to make the title a clickable link. A bare HTTP(S) URL uses the hostname as its link text. |
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

The complete API is available through `anki`:

```sh
uv run --locked anki-automation anki methods
uv run --locked anki-automation anki methods --live
uv run --locked anki-automation anki schema notes.create
uv run --locked anki-automation anki decks.list
uv run --locked anki-automation anki notes.search --query 'deck:Languages' --all-pages
uv run --locked anki-automation anki decks.create --name 'Languages::English'
```

Reads execute by default. Methods with other effects show a preview until
`--apply` is supplied. Use `--dry-run` to preview a read. Every method has its own
parameter flags and help. See the [CLI and Python guide](docs/ankiconnect-cli.md)
for JSON input, operation waiting, pagination, bulk errors, and API examples.
The [research reference](docs/research/ankiconnect.md) lists all methods and links
to the pinned primary sources. `methods --live` shows which methods the running
add-on supports; the packaged catalog can be newer than the installed add-on.

Use Anki Desktop **26.08 or later** and a current
[Anki-Connect](https://git.sr.ht/~foosoft/anki-connect) add-on with API v1.
Create the target deck and the `Replika · Meaning` note type with fields named `Front`,
`Back`, and `Source`. `templates --apply` creates the note type from the vault
sources. Names are exact; localized note type names must be changed in Anki.
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

## Template sources

Note type layouts live as JSON files (the `noteTypes.create` schema, one file per
note type, file name equal to the type name) next to one shared `replika.css`.
Compare them with the open profile, then apply:

```sh
uv run --locked anki-automation templates '/path/to/vault/40 Anki/Templates'
uv run --locked anki-automation templates '/path/to/vault/40 Anki/Templates' --apply
```

Only settings a source states are managed; omitted ones keep their Anki values.
The check makes only reads in one
collection session and exits with code 1 when a field setting, template, the CSS,
the sort field, or a LaTeX setting differs, or a note type is missing. `--apply`
creates missing note types and updates those settings in the same session, so a
profile switch fails with `staleSession`. It refuses to write anything when a field
list, note kind, or template name differs: such a change needs a structural
migration and a full sync. Writes are never retried. If a write fails, the error
JSON lists the steps already confirmed for each note type under
`result.applied_before_failure`; run the check again before you continue.

## Development

For the three Replika types, use `replika-validate` to check Obsidian JSONL and
`replika-export` to build an Anki package and standalone HTML preview. See
[Replika source format and demo build](docs/replika.md).

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

GPL-3.0-or-later. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
The original MIT notice is kept in [LICENSES/MIT.txt](LICENSES/MIT.txt).
