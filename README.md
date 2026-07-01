# MindSpeak

MindSpeak turns a walking voice note into an Obsidian note.

Context: I go on long undistracted walks where thoughts come to me and need to be stored for processing later without breaking my focus in the moment.

The idea: make voice recordings on the phone or an audio recorder.

1. Transcribe an audio file with OpenAI speech-to-text.
2. Summarize and lightly validate the idea.
3. Save Markdown into `Voice/` in your Obsidian vault.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then edit `.env` with your `OPENAI_API_KEY` and either:

- `OBSIDIAN_WRITE_MODE=mcp` plus `OBSIDIAN_MCP_COMMAND` and `OBSIDIAN_API_KEY`
- `OBSIDIAN_WRITE_MODE=file` plus `OBSIDIAN_VAULT_PATH=/path/to/vault`

For the Obsidian MCP Tools plugin, set `OBSIDIAN_MCP_TOOL=execute_template`
and `OBSIDIAN_MCP_TEMPLATE` to the vault path of your voice-note template.
The template file must include YAML frontmatter with `tags` as an array, for
example:

```markdown
---
tags: []
---
```

## Usage

```bash
python mindspeak.py /path/to/voice-note.m4a
```

Preview the generated note without writing it:

```bash
python mindspeak.py /path/to/voice-note.m4a --dry-run
```

Run the desktop UI:

```bash
python mindspeak_ui.py
```
