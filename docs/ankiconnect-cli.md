# Anki-Connect CLI and Python client

The package includes all **96 API v1 methods** from upstream revision
`c577b44aaadbd72dbc22c823f874a126213fe5c5`. The method registry, parameter flags,
effects, and validation rules come from the unmodified schema in
`src/anki_automation/resources/`. Its adjacent `provenance.json` records the source
URL, revision, SHA-256 digest, and license. No schema download is needed at runtime.
See the [research reference](research/ankiconnect.md) for the full method inventory
and [upstream contract](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md).

## Discover methods

```sh
uv run --locked anki-automation anki --help
uv run --locked anki-automation anki methods
uv run --locked anki-automation anki methods --group notes
uv run --locked anki-automation anki methods --live
uv run --locked anki-automation anki schema notes.create
uv run --locked anki-automation anki notes.create --help
```

`methods` and `schema` work offline. `methods --live` calls `api.describe` and adds
runtime availability and reasons to the local catalog. Unknown runtime methods
are listed separately; they do not change the client's method or effect rules.
The installed add-on can have fewer methods than this package. Update the add-on
separately when you need a missing method. The client has no legacy v6 fallback.

All namespaces are covered: discovery, access, profiles, collections, notes, tags,
cards, scheduling, decks, presets, note types, media, reviews, statistics, package
I/O, browser, editors, reviewer, application, advanced writes, and operations.

## Input and output

Each dotted method name is a command. Scalar strings, integers, and numbers use
ordinary flag values. Camel-case parameter names become kebab-case flags:
`noteType` becomes `--note-type`, and `cardId` becomes `--card-id`.

```sh
uv run --locked anki-automation anki notes.search --query 'deck:Languages' --limit 10
uv run --locked anki-automation anki cards.setSuspended --ids '["123"]' --suspended
uv run --locked anki-automation anki cards.setSuspended --ids '["123"]' --no-suspended
```

Boolean flags have positive and negative forms. Arrays, objects, and union types
use JSON. Entity IDs are decimal **strings**, including inside arrays and reference
objects. Use exactly one of `{"id":"123"}` and `{"name":"Default"}` for a reference.
The IDs in this guide are examples; obtain real IDs from your collection first.

Every method also accepts a JSON object with `--params`, `--params-file FILE`,
or `--params-file -` for stdin. The generic `call METHOD` form has the same behavior:

```sh
uv run --locked anki-automation anki call notes.get --params '{"id":"123"}'
uv run --locked anki-automation anki notes.create --params-file data/note.json
```

You can add parameter flags to file or inline JSON input, but supplying one
parameter twice is an error. A JSON root of `null`, a non-object root, duplicate
object keys, non-finite numbers, unknown properties, and schema constraint errors
are rejected before HTTP. Schema defaults are left to Anki. JSON Schema `format`
annotations and native resource, token, scheduling, and content rules remain
server checks. Error diagnostics omit the private values that failed validation.
Response validation tolerates added properties as required by upstream.

Successful commands print the result as JSON to stdout. Failures print a JSON
error to stderr. API errors retain `method`, `code`, and `data` when present.
Bulk errors also retain the complete result, including successful items.

| Exit code | Meaning |
| --- | --- |
| `0` | Preview produced or API result received. An operation handle means accepted work. |
| `1` | Input, transport, protocol, API, item, or operation failure. |
| `2` | Command argument error. |
| `3` | Operation wait deadline reached; server work was not cancelled. |

## Execution and effects

Read methods execute by default. Every other upstream effect requires `--apply`:
writes, GUI actions, lifecycle changes, media, filesystem I/O, permissions,
refresh, ordered batches, and advanced writes. Without it, the CLI validates and
prints a preview. `--dry-run` previews any method without an HTTP request.

```sh
# Preview creation, then explicitly execute it.
uv run --locked anki-automation anki decks.create --name 'Languages::English'
uv run --locked anki-automation anki decks.create --name 'Languages::English' --apply
```

The local listener is `http://127.0.0.1:8765/api/v1`. Commands that use HTTP accept
`--port` and `--timeout` in seconds. Requests do not use proxies or redirects.
`ANKI_CONNECT_KEY` supplies the Bearer key when the add-on requires authentication.
The key is never a CLI argument or part of a preview. Input, previews, returned
fields, and media can be private; keep saved data under ignored `data/` or `exports/`.

For collection methods with effects, the client gets `collection.get` and pins
the returned session unless `--session` is supplied. It does not reuse a hidden
session after a profile switch. Read methods can also take an explicit session.
The active Anki profile is the write target. A method that deletes resources,
changes profiles, repairs a collection, imports history, or exits Anki has the
native effect described in its schema. `--apply` authorizes that effect.

## Notes and note types

Raw API fields contain HTML. Unlike the JSONL card pipeline, raw API commands do
not escape field content. Choose the actual note type and exact field names;
the generic API is not restricted to `Basic`.

```sh
uv run --locked anki-automation anki notes.create \
  --note-type '{"name":"Basic"}' --deck '{"name":"Languages::English"}' \
  --fields '{"Front":"cat","Back":"кот"}' --tags '["lang::en"]'

uv run --locked anki-automation anki notes.update --id 123 \
  --patch '{"fields":{"Back":"кот; кошка"}}'

uv run --locked anki-automation anki notes.createMany \
  --params-file data/notes.json --atomic
```

Add `--apply` after review. `notes.createMany` is not atomic by default in the raw
API. Use `--atomic` for one native transaction. The existing `anki-import` command
always uses an atomic batch. Other bulk methods can commit independent items;
an item error causes exit code 1 and keeps successful item results in stderr JSON.
Field patches merge by field name; a supplied tags array replaces the full tag set.
Use `notes.validate` or `notes.validateMany` for native eligibility checks.
Use the `noteTypes.*` methods to create and edit custom note types and templates.

## Long-running operations

Twenty methods return operation handles. Without `--wait`, the result contains an
`operationId`; it confirms admission, not completion. To wait for the final record:

```sh
uv run --locked anki-automation anki collection.sync --apply --wait --wait-timeout 120
uv run --locked anki-automation anki wait op-123 --wait-timeout 120
uv run --locked anki-automation anki operations.get --operation-id op-123
uv run --locked anki-automation anki operations.cancel --operation-id op-123 --apply
```

The waiter accepts `--poll-interval` and a finite positive deadline. It handles
`queued`, `running`, and `waitingForUser` until a terminal result. A failed or
cancelled operation returns an error with its ID and the terminal record.
Timeout retains the ID and last record. Lost responses do not cause a write retry.
Interrupting `--wait` after admission retains the handle in the error output.
Stopping the waiter does not cancel server work. Cancellation is a separate method
and can race with completion. Handles are lost when Anki restarts.

## Pagination and ordered commands

```sh
uv run --locked anki-automation anki notes.search \
  --query 'deck:Languages' --limit 100 --all-pages --max-pages 100
```

Pagination is available for `notes.search`, `cards.search`, `notes.findEmpty`, and
`reviews.list`. `--all-pages` returns a `pages` array, pins one session, and keeps
query, sort, and limit stable. It consumes each cursor once, detects repeated
cursors, and stops at `--max-pages`. On an error, stderr JSON retains pages already
received and the last result. Use its `nextCursor` to resume when one is available.
The iterator API lets Python callers process pages as they arrive.

Use `api.execute --params-file data/commands.json --apply --wait` for an ordered
`commands` array. Each entry has `method` and optional `params`. The client checks
every child method and its parameters before HTTP and pins one shared collection
session. Conflicting sessions are rejected. Nested execution, profile switching,
and application exit are prohibited by upstream. Commands continue after item
failures. Ordered execution is not a transaction; final item failures return
exit code 1, including errors inside a child bulk result.

## Media, files, scheduling, and GUI

```sh
uv run --locked anki-automation anki media.store --filename example.mp3 \
  --source '{"path":"/absolute/path/to/example.mp3"}' --apply --wait
uv run --locked anki-automation anki packages.export \
  --deck '{"name":"Languages::English"}' --path /absolute/path/to/english.apkg
```

Paths are interpreted on the Anki host. Package export writes a file and needs
`--apply`. Media storage can rename a file; use the returned filename in note HTML
or sound references. A hash-skipped upload has no stored filename. Media writes
are outside collection undo. Request bodies are limited to 16 MiB; media paths
support the server's 64 MiB file limit.

For scheduling, call `scheduling.predict` first and pass the returned state token
and an offered rating to `scheduling.answer`. GUI reviewer commands use their own
tokens from `reviewer.get`; show the answer before answering. The client never
fabricates tokens or changes scheduling to avoid a server error. GUI operations
can need interaction in Anki. Inspect each method with `anki schema METHOD`.

## Python API

```python
import os

from anki_automation.anki import AnkiClient
from anki_automation.contract import get_contract

client = AnkiClient(api_key=os.environ.get("ANKI_CONNECT_KEY"))
print(client.call("decks.list"))
print(get_contract().describe("notes.create"))

for page in client.iter_pages("notes.search", {"query": "deck:Languages", "limit": 100}):
    print(page["ids"])

# Explicit collection write; native resources and fields must exist.
created = client.call(
    "notes.create",
    {"deck": {"name": "Languages::English"}, "noteType": {"name": "Basic"},
     "fields": {"Front": "cat", "Back": "кот"}},
    apply=True,
)
```

`call(method, params, apply=False, wait=False)` supports the complete catalog.
The result is JSON and is checked against that method's response schema. Python
callers must select the expected result shape for the chosen method. For operation
methods use `wait=True` or `wait_for_operation(operation_id)`. `AnkiError` retains
structured fields and offers `as_dict()`; `OperationTimeout` identifies unfinished
work. Python calls with effects require `apply=True` and raise an error otherwise.

Tests use the pinned upstream request examples and a local HTTP protocol server.
They verify client behavior; they do not prove all native, GUI, sync, filesystem,
or destructive operations against a real Anki collection.
