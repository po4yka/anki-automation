---
name: anki-card-authoring
description: Prepare and check draft Anki source records from language-learning material using the anki-automation contract; keep collection writes separate.
---

# Prepare language cards

Read [AGENTS.md](../../../AGENTS.md) and [README.md](../../../README.md) from this repository before work. Read the caller's source notes, learner preferences, and category map when supplied. The caller owns learning content; this repository owns Anki formats and logic.

## Draft

Find the learning target and the reason to remember it. Use one clear test per prompt. Give enough context to identify the intended meaning. Accept valid alternatives or narrow the question. Do not make a reverse card by default.

Keep source facts, paraphrases, original examples, and the learner's observed errors distinct. Check translations and key language claims against accessible sources. Mark unresolved claims for review. Never invent a learner response or a review result.

For `Replika · Meaning`, use the `term`/`meaning` source fields in the README and `validate`; this note type shares the Replika stylesheet but is not part of the Replika JSONL format or the `replika-*` commands. For Replika, use the [Replika JSONL contract](../../../docs/replika.md#prepare-a-package-from-obsidian) for context, native cloze, and typed answers or dictation. Keep the Replika key when editing a note. Keep each JSONL file in one format.

The `Replika · Meaning` loader rejects conflicting records with the same `term`. The Replika loader rejects repeated keys across the selected files. Different questions are valid when they test different targets. Do not add arbitrary suffixes to evade duplicate checks. If the requested sense model needs a contract change, report that need and change it here only when authorized.

Save UTF-8 JSONL in the caller's draft location. For languages-vault, read `40 Anki/decks.json` and use the matching `drafts` directory. Pass this map to Replika validation and export with `--decks`. Keep personal content out of tracked example and test files in this repository.

## Check and hand off

From this repository root, run the existing command with the actual absolute input path:

```sh
uv run --locked anki-automation validate "$batch_path"
```

Use `validate` and the offline `anki-import` preview for `Replika · Meaning`. Use `replika-validate` and the HTML from `replika-export` for Replika. A format check does not prove language accuracy. Show the draft, its category, sources, and any remaining ambiguity.

Move the exact batch version to the caller's reviewed location only after explicit user review. Update links to the source when moving it. Keep that reviewed version unchanged; exports are derived files. Pass delivery to `anki-delivery` when requested. Authoring does not authorize collection writes.

## Revise a reviewed batch

Before changing any record field, move the batch back to the draft location for the same category. This includes changes to the prompt, answer, example, source, and tags. Preserve the file and update its links; do not overwrite a different batch at the destination. The previous review no longer applies.

Edit the draft, run validation, and show the new version for explicit user review. Move it back to the reviewed location only after that review. An import request for the earlier version does not authorize the changed content. If a file was already changed in the reviewed location, treat it as a draft and restore this workflow before delivery. A folder name alone does not prove review of changed records.

If the batch was already imported, explain that editing its source does not update Anki. Keep that separate from a new import: the current preparation flow cannot update existing notes. Do not delete and recreate notes to simulate an update.
