---
name: anki-delivery
description: Validate, export, preview, and perform explicitly authorized Anki imports through the existing anki-automation CLI; report actual collection results.
---

# Deliver a card batch

Read [AGENTS.md](../../../AGENTS.md) and [README.md](../../../README.md). Use the existing CLI from this repository root. For low-level API work, also read [the CLI guide](../../../docs/ankiconnect-cli.md). Keep new Anki logic in this repository.

## Resolve the operation

Use the caller's reviewed source batch and exact target deck. In languages-vault, read `40 Anki/decks.json` to resolve the category, paths, and deck. Pass that map to `replika-validate` and `replika-export` with `--decks`. Keep each draft file in its category. A Replika package can route selected files to several decks.

Draft batches may be previewed for content review; they must not be imported as reviewed material. Check that the user's review covers the current records, not only the folder name. If any record field changed after review, follow [the authoring revision workflow](../anki-card-authoring/SKILL.md): return the batch to drafts, validate the changes, and obtain review of the new version. This also applies when the changed file is still in the reviewed folder. An import request for an earlier version does not authorize changed content.

Confirm what the user authorized: local export, deck creation, import, deletion, scheduling, and AnkiWeb sync are separate effects. A general request to prepare cards does not authorize a collection write.

## Validate, export, or preview

For `Replika · Meaning`, use the actual input path and deck name in these commands:

```sh
uv run --locked anki-automation validate "$batch_path"
uv run --locked anki-automation anki-import "$batch_path" --deck "$target_deck"
```

The import command above is an offline preview. Inspect the deck, note type, fields, tags, and count. For a requested TSV export, use the existing `export` command and a new output path under this repository's ignored `exports/`. It refuses to overwrite files. Store previews with the same care as the private input.

For Replika, follow [the package workflow](../../../docs/replika.md#prepare-a-package-from-obsidian). Inspect `Preview.html` and report the round trip in the temporary collection separately from any import in the intended profile. The reviewed package can be imported through Anki Desktop with separate authorization.

## Apply only the authorized operation

Before any collection write, run the read-only `templates` check on `40 Anki/Templates` in languages-vault. If it reports drift, stop and report it; `templates --apply` is a collection write and needs its own explicit user authority. For a `Replika · Meaning` collection write, check the running add-on's capabilities, active collection, and the required deck and `Replika · Meaning` note type using documented read commands. Inspect their current CLI help or schema rather than guessing API flags. Show the exact effect and resolve an ambiguous active profile before writing.

When the user has explicitly authorized importing the current reviewed batch version into that collection, run the same `anki-import` command with `--apply`. If the records change after the preview or review, return to the revision workflow before writing. Keep atomic imports, session checks, and duplicate rejection. The current import flow creates notes; it does not update existing ones. Do not delete and recreate notes to simulate an update.

Never retry a write automatically. A transport failure or invalid reply can leave its outcome unknown. Inspect collection state before proposing a next action. An operation handle alone is not completion evidence; follow the CLI guide for operation results.

After an import, check the returned IDs and read the resulting notes in the intended deck. Report source count, validated count, preview or export result, and confirmed write result separately. Do not change review grades, scheduling, or run AnkiWeb sync as an import follow-up unless separately authorized.
