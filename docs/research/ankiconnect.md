# Anki-Connect API research

Checked: 2026-11-04. Source revision: `c577b44aaadbd72dbc22c823f874a126213fe5c5`.
This report covers the current API contract. Protocol tests do not establish
native write, GUI, sync, or filesystem behavior against a real collection.

## Local read-only check

On 2026-11-04, `api.describe` and `collection.get` succeeded against the running
Anki Desktop 26.09.3 instance. Its add-on reported API major 1 and 94 available
methods. Compared with the pinned schema, it lacks `scheduling.getQueue` and
`scheduling.repositionNewCards`. The collection was open. No collection writes,
GUI changes, profile switches, or sync operations were performed. This is local
runtime evidence; it does not change the upstream 96-method contract.

## Supported protocol and source of truth

Upstream recommends API v1 for new clients. It uses JSON-RPC 2.0 at
`POST http://127.0.0.1:8765/api/v1`. It requires Anki Desktop 26.08 or later with
the add-on enabled. Upstream lists 26.08 and 26.09.3 in its compatibility tests.
The old action API remains at `/`; it is a separate legacy protocol.
[Upstream README](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/README.md).

The pinned schema contains **96 methods in 21 namespaces**. The API reference also
has 96 method sections. The README says 94 methods; that count is stale relative
to the schema at this revision. Use `x-methods` and its referenced parameter and
result definitions as the complete contract. Use `api.describe` to inspect the
methods and capabilities of the installed add-on.
[Schema](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/api-v1.schema.json), [API reference](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md).

Of the 96 methods, 40 have effect `read`; 56 have another effect. There are 76
methods that return a completed result and 20 that first return an operation
handle. Collection scope applies to 87 methods; application scope applies to 9.
These counts come from schema metadata, rather than from method names or undo
labels. [Schema method metadata](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/api-v1.schema.json).

## Transport, authentication, and errors

Send a request ID and an object of named parameters. Entity IDs are decimal
strings. Check the HTTP status first, then the JSON-RPC version, response ID,
and exactly one of `result` or `error`. HTTP 200 can carry a JSON-RPC error.
Transport rejection statuses include 401, 403, 405, and 413. If an API key is set,
send it in `Authorization: Bearer <key>`. Origin permission is a separate check.
`access.requestPermission` uses the HTTP origin and does not return the API key.
[Request and access contract](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md#requests-authentication-and-errors).

Preserve structured error `code`, `message`, and `data`; match the code or
`data.type` instead of the message text. Method-specific errors include missing
resources, invalid content, open-editor conflicts, unavailable collections,
stale sessions or cursors, state-token conflicts, and unsupported capabilities.
A successful bulk response can still contain item errors. Each item keeps its
original `index` and contains either a result or an error. A JSON-RPC success
alone does not prove that all items succeeded.
[Errors and bulk results](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md#working-with-collections).

A request ID is not an idempotency key. A client must not repeat a write after a
lost response without checking its outcome. The server socket idle timeout is
ten seconds, but accepted work has no implicit execution timeout.
[Error rules](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md#requests-authentication-and-errors),
[Socket and media rules](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md#notes-and-media).

## Collections and writes

Read `collection.get` and pass its `session` to later collection requests. This
prevents a request from acting on another collection after a profile switch.
Switching profiles invalidates collection IDs held by the client, cursors,
scheduling tokens, and editor history. References to decks, presets, and note
types use exactly one of `id` or `name`. A read does not create a missing resource.
Fields contain HTML. A field patch merges by field name; a supplied tags array
replaces the full tag set. Names and fields are exact.
[Collection contract](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md#working-with-collections).

`notes.createMany` with `atomic: true` validates the full batch, including its
internal duplicates, and writes one native collection transaction. A rejected
atomic batch leaves the collection unchanged. External I/O from third-party
add-on hooks is outside that transaction. Ordinary bulk methods can have
independent item outcomes. Undo grouping is not a general transaction guarantee.
Affected note editors must be closed for collection writes.
[Notes and media](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md#notes-and-media),
[Undo rules](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md#undo-behavior).

The effect field is the useful execution boundary. `undo: none` does not mean
read-only: media deletion, profile changes, operation cancellation, GUI changes,
package export, and application exit can have effects without a collection undo
entry. Integrity checks can repair the database and clear undo history. The
`advancedWrite` methods can change metadata or import history; history import
clears undo rather than answering or rescheduling a card.
[Schema effects](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/api-v1.schema.json),
[Advanced method contract](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md#advanced-writes).

## Operations and ordered execution

Operation states are `queued`, `running`, `waitingForUser`, `succeeded`, `failed`,
and `cancelled`. Only terminal records contain a result or error. Poll
`operations.get` until a terminal state or a client deadline. Keep the handle
when the deadline expires: stopping the wait or disconnecting does not cancel
server work. Handles survive a profile switch but not an Anki restart.
[Operation contract](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md#operations-and-ordered-commands).

`operations.cancel` requests cancellation only when the operation supports it.
It can lose a race with success and does not undo committed changes. Check the
operation's `cancellable` field and inspect its final state.
[Cancellation method](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md#operationscancel).

`api.execute` runs commands in order and waits for each command's final outcome.
It continues after command errors and returns an operation with indexed item
results. It is not an atomic batch, and it does not insert earlier results into
later parameters. Nested `api.execute`, `profiles.switch`, and `application.exit`
are forbidden inside its commands. Pin the collection session for collection
commands, and validate every command against its own method schema.
[Ordered commands](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md#apiexecute).

## Search, reviewer state, and media

For cursor searches, keep the query, sort, limit, and session unchanged between
pages. The default page size is 100; the maximum is 1000. The cursor holds an
ordered snapshot for ten minutes. A successful continuation consumes its cursor;
use the returned `nextCursor` and stop when it is null. New matches do not enter
an existing snapshot, and deleted resources are omitted. Review pagination also
uses a snapshot, with an exclusive `afterId`.
[Pagination contract](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md#search-and-pagination).

For non-GUI scheduling, get a current `stateToken` from `scheduling.predict`, then
pass an offered rating and that token to `scheduling.answer`. For an active GUI
reviewer, use `reviewer.get` and the `reviewer.*` methods so scheduling hooks are
respected. Show the answer before answering. Get a new reviewer token when the
card or displayed side changes. Tokens belong to the collection session.
[Scheduling contract](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md#scheduling-and-reviewer-tokens).

Store media before adding references to note HTML. Use the returned filename:
Anki can normalize or rename the requested one. A skipped `skipIfHash` result
has no filename and does not promise a stored file. File paths refer to the
machine that runs Anki. Media replacement is explicit; media writes cannot be
reversed with collection undo. Package export writes a file even though it does
not change note scheduling.
[Media contract](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md#notes-and-media),
[Package export](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md#packagesexport).

## Limits

| Limit | Value |
| --- | ---: |
| JSON request body | 16 MiB |
| JSON-RPC batch items | 100 |
| Page size | 1000 |
| Media file size | 64 MiB |
| Active operations | 100 |
| Retained terminal operations | 1000 |
| Terminal operation retention | 1 hour |

These values come from `x-limits` in the
[pinned schema](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/api-v1.schema.json).

## Complete method inventory

The table records schema metadata. `completed` means the response is the method
result; `operation` means the response is a handle. Effect and undo are separate
properties. Exact parameters, enum values, references, constraints, result types,
and examples are in the [pinned schema](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/api-v1.schema.json) and
[method reference](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/API_V1.md).

| Method | Scope | Effect | Completion | Undo |
| --- | --- | --- | --- | --- |
| `access.requestPermission` | `application` | `permission` | `completed` | `none` |
| `advanced.cards.update` | `collection` | `advancedWrite` | `completed` | `single` |
| `advanced.reviews.import` | `collection` | `advancedWrite` | `operation` | `clears` |
| `api.describe` | `application` | `read` | `completed` | `none` |
| `api.execute` | `application` | `batch` | `operation` | `perCommand` |
| `application.exit` | `application` | `lifecycle` | `completed` | `none` |
| `application.importFile` | `collection` | `write` | `operation` | `clears` |
| `application.navigate` | `collection` | `gui` | `operation` | `none` |
| `browser.getSelection` | `collection` | `read` | `completed` | `none` |
| `browser.open` | `collection` | `gui` | `operation` | `none` |
| `browser.select` | `collection` | `gui` | `operation` | `none` |
| `cards.get` | `collection` | `read` | `completed` | `none` |
| `cards.getMany` | `collection` | `read` | `completed` | `none` |
| `cards.move` | `collection` | `write` | `completed` | `perItem` |
| `cards.render` | `collection` | `read` | `completed` | `none` |
| `cards.search` | `collection` | `read` | `completed` | `none` |
| `cards.setFlags` | `collection` | `write` | `completed` | `perItem` |
| `cards.setSuspended` | `collection` | `write` | `completed` | `perItem` |
| `collection.checkIntegrity` | `collection` | `write` | `operation` | `clears` |
| `collection.get` | `collection` | `read` | `completed` | `none` |
| `collection.refresh` | `collection` | `refresh` | `completed` | `none` |
| `collection.sync` | `collection` | `lifecycle` | `operation` | `none` |
| `collection.undo` | `collection` | `write` | `operation` | `undo` |
| `deckPresets.assign` | `collection` | `write` | `operation` | `perItem` |
| `deckPresets.create` | `collection` | `write` | `completed` | `single` |
| `deckPresets.delete` | `collection` | `write` | `operation` | `grouped` |
| `deckPresets.get` | `collection` | `read` | `completed` | `none` |
| `deckPresets.list` | `collection` | `read` | `completed` | `none` |
| `deckPresets.update` | `collection` | `write` | `operation` | `single` |
| `decks.create` | `collection` | `write` | `completed` | `single` |
| `decks.delete` | `collection` | `write` | `completed` | `perItem` |
| `decks.get` | `collection` | `read` | `completed` | `none` |
| `decks.getCounts` | `collection` | `read` | `completed` | `none` |
| `decks.list` | `collection` | `read` | `completed` | `none` |
| `decks.update` | `collection` | `write` | `completed` | `single` |
| `editors.openAdd` | `collection` | `gui` | `operation` | `none` |
| `editors.openNote` | `collection` | `gui` | `operation` | `none` |
| `editors.updateDraft` | `collection` | `gui` | `operation` | `none` |
| `media.delete` | `collection` | `media` | `completed` | `none` |
| `media.get` | `collection` | `read` | `completed` | `none` |
| `media.getDirectory` | `collection` | `read` | `completed` | `none` |
| `media.list` | `collection` | `read` | `completed` | `none` |
| `media.store` | `collection` | `media` | `operation` | `none` |
| `noteTypes.create` | `collection` | `write` | `completed` | `single` |
| `noteTypes.deleteUnused` | `collection` | `write` | `completed` | `perItem` |
| `noteTypes.editStructure` | `collection` | `write` | `completed` | `single` |
| `noteTypes.get` | `collection` | `read` | `completed` | `none` |
| `noteTypes.getTemplateUsage` | `collection` | `read` | `completed` | `none` |
| `noteTypes.list` | `collection` | `read` | `completed` | `none` |
| `noteTypes.replaceText` | `collection` | `write` | `completed` | `grouped` |
| `noteTypes.update` | `collection` | `write` | `completed` | `single` |
| `notes.convert` | `collection` | `write` | `completed` | `single` |
| `notes.create` | `collection` | `write` | `completed` | `single` |
| `notes.createMany` | `collection` | `write` | `completed` | `perItemOrAtomic` |
| `notes.delete` | `collection` | `write` | `completed` | `perItem` |
| `notes.findEmpty` | `collection` | `read` | `completed` | `none` |
| `notes.get` | `collection` | `read` | `completed` | `none` |
| `notes.getMany` | `collection` | `read` | `completed` | `none` |
| `notes.planConversion` | `collection` | `read` | `completed` | `none` |
| `notes.search` | `collection` | `read` | `completed` | `none` |
| `notes.update` | `collection` | `write` | `completed` | `single` |
| `notes.validate` | `collection` | `read` | `completed` | `none` |
| `notes.validateMany` | `collection` | `read` | `completed` | `none` |
| `operations.cancel` | `application` | `lifecycle` | `completed` | `none` |
| `operations.get` | `application` | `read` | `completed` | `none` |
| `packages.export` | `collection` | `filesystem` | `operation` | `none` |
| `packages.import` | `collection` | `write` | `operation` | `clears` |
| `profiles.getActive` | `application` | `read` | `completed` | `none` |
| `profiles.list` | `application` | `read` | `completed` | `none` |
| `profiles.switch` | `application` | `lifecycle` | `operation` | `none` |
| `reviewer.answer` | `collection` | `write` | `operation` | `single` |
| `reviewer.get` | `collection` | `read` | `completed` | `none` |
| `reviewer.playAudio` | `collection` | `gui` | `completed` | `none` |
| `reviewer.showAnswer` | `collection` | `gui` | `completed` | `none` |
| `reviewer.showQuestion` | `collection` | `gui` | `completed` | `none` |
| `reviewer.startTimer` | `collection` | `gui` | `completed` | `none` |
| `reviews.getLatestId` | `collection` | `read` | `completed` | `none` |
| `reviews.list` | `collection` | `read` | `completed` | `none` |
| `scheduling.answer` | `collection` | `write` | `completed` | `single` |
| `scheduling.answerMany` | `collection` | `write` | `completed` | `perItem` |
| `scheduling.get` | `collection` | `read` | `completed` | `none` |
| `scheduling.getDue` | `collection` | `read` | `completed` | `none` |
| `scheduling.getQueue` | `collection` | `read` | `completed` | `none` |
| `scheduling.predict` | `collection` | `read` | `completed` | `none` |
| `scheduling.repositionNewCards` | `collection` | `write` | `completed` | `single` |
| `scheduling.reset` | `collection` | `write` | `completed` | `perItem` |
| `scheduling.setDueDate` | `collection` | `write` | `completed` | `perItem` |
| `statistics.getHtml` | `collection` | `read` | `completed` | `none` |
| `statistics.getReviewDays` | `collection` | `read` | `completed` | `none` |
| `statistics.getSummary` | `collection` | `read` | `completed` | `none` |
| `statistics.getToday` | `collection` | `read` | `completed` | `none` |
| `tags.add` | `collection` | `write` | `completed` | `perItem` |
| `tags.deleteUnused` | `collection` | `write` | `completed` | `single` |
| `tags.list` | `collection` | `read` | `completed` | `none` |
| `tags.remove` | `collection` | `write` | `completed` | `perItem` |
| `tags.replace` | `collection` | `write` | `completed` | `single` |

## Client design conclusions

The following are design conclusions for this repository, not additional upstream
requirements:

- Build method discovery, parameter help, and dispatch from one pinned contract.
  Check the runtime with `api.describe` when capability support matters.
- Let only effect `read` execute without an explicit write or effect flag.
  Preview every other effect without contacting Anki until execution is requested.
- Keep request validation offline. Check required result structure and bulk item
  outcomes, while accepting added response properties as the upstream contract
  requires. Return a nonzero status for an unconfirmed outcome while preserving
  the structured result or error for the caller.
- Preserve operation handles on timeouts. Keep polling, cancellation, and write
  execution separate. Do not retry writes automatically.
- Use protocol tests and isolated files for the client. Keep live Anki tests and
  account sync tests separate, with explicit authority and a disposable collection.

## Source distribution decision

The upstream [LICENSE](https://git.sr.ht/~foosoft/anki-connect/blob/c577b44aaadbd72dbc22c823f874a126213fe5c5/LICENSE)
states GPL version 3 or later. The user approved moving this project's distribution
to `GPL-3.0-or-later` and bundling the official schema. Keep the schema unmodified,
record its source revision and SHA-256 digest, and include the upstream notice.
Retain the original MIT notice for the existing project work. These notices
identify different source contributions; they must not identify the upstream
schema as MIT.

The implementation uses `jsonschema` for Draft 2020-12 validation rather than a
custom schema engine. Its MIT notice remains separate from the upstream
Anki-Connect notice. This report's method inventory contains API identifiers and
factual metadata, with original explanatory text.
