# Build the Replika demo

Use the current Replika `.apkg` as the source of demo notes, card IDs, and media. Use the three note type JSON files and `replika.css` as the source of layout. The input package must contain the `Реплика — демо` deck and the three Replika note types. To revise demo content, edit it in a test profile and export that deck with media as a new input package.

On macOS, set the paths to the installed Anki Desktop packages and a matching Python interpreter. The command below uses the Python 3.13 runtime required by Anki Desktop 26.09.3. The CLI itself runs under the locked Python 3.15 environment.

```sh
uv run --locked anki-automation replika-build \
  --templates '/path/to/vault/40 Anki/Templates' \
  --base '/path/to/vault/40 Anki/Templates/Реплика — демо.apkg' \
  --output-dir exports/replika-next \
  --anki-python "$(uv python find --no-project 3.13)" \
  --anki-packages '/Applications/Anki.app/Contents/Resources/app_packages'
```

Use a new output directory. The builder checks note fields, keys, cloze syntax, and local media. It updates the note types in a disposable collection and verifies card and note identities after reimport. It writes the package and the self-contained HTML preview together after the checks pass. The package excludes study history and deck settings. The command uses local files and does not open the active profile.

Check the resulting preview. Then replace both generated files in the vault together. Keep the same `Key` when editing a note. The builder fills missing keys from the note GUID and preserves existing keys. For a new note entered in Anki, generate a unique key with `uv run --locked python -c 'import uuid; print("r-" + uuid.uuid4().hex)'`.

## Prepare a package from Obsidian

Save UTF-8 JSONL with one note per line. Use the category's draft folder. Choose
the note type for the task and the category for the destination deck.

```json
{"key":"r-1234567890abcdef1234567890abcdef","type":"cloze","category":"grammar","fields":{"Text":"I arrived {{c1::at}} noon.","Source":"Lesson 1"}}
```

| Type | Anki note type | Required fields |
| --- | --- | --- |
| `context` | Реплика · Контекст | `Prompt`, `Answer`, `Source` |
| `cloze` | Реплика · Пропуск | `Text` with one `{{c1::answer}}`, `Source` |
| `recall` | Реплика · Точный ответ | `Prompt`, one-line `Answer`, `Source` |

Generate a new `key` for each new note. Keep it when revising the note. The package
uses this value for the Anki GUID and fills the hidden `Key` field. Use
`words`, `grammar`, or `slang` as the category. The `--decks` file maps these
categories to deck names. Add optional `tags` as an array of strings. The exporter
adds the language and category tags.

Field names match the selected template JSON. Use text or the supported simple
HTML, such as `<strong>` and HTTPS links in `Source`. Put an optional recording in
`Audio` as `[sound:filename.mp3]`. Put an optional image in `Figure` as
`<img src="filename.png" alt="Description">`. Keep referenced files directly in
`--media-dir` and give each distinct asset a distinct name. The preview includes
the media inline.

From the repository root, set `vault` and list the exact files to export in
`inputs`. Set `--media-dir` to the folder that contains the files referenced by
the notes.

```sh
vault='/path/to/languages-vault'
inputs=("$vault/40 Anki/Drafts/Grammar/example.jsonl")
options=(
  --templates "$vault/40 Anki/Templates"
  --decks "$vault/40 Anki/decks.json"
  --media-dir "$vault/Attachments"
)
uv run --locked anki-automation replika-validate "${options[@]}" "${inputs[@]}"
uv run --locked anki-automation replika-export "${options[@]}" "${inputs[@]}" \
  --base "$vault/40 Anki/Templates/Реплика — демо.apkg" \
  --output-dir exports/replika-batch \
  --anki-python "$(uv python find --no-project 3.13)" \
  --anki-packages '/Applications/Anki.app/Contents/Resources/app_packages'
```

Validation uses local files. Export uses the demo package for the note type
definitions and IDs. It selects the source notes for export and imports the
result into a separate temporary collection. It checks card identities, fields,
tags, deck names, and media before it publishes `Replika.apkg` and `Preview.html`.
The package contains the source notes, their types, and their media. The export
excludes review history and deck settings.

Open `Preview.html` to check both sides, the cloze, and any media. The typed
comparison runs in the Anki reviewer. For an authorized import, use **File →
Import** in the intended Anki Desktop profile. For a batch of new notes, use
**Update notes → Never**. If that profile already has Replika types, check that
their fields and template versions match before import. This workflow delivers
new notes and does not apply revisions to an existing collection.
