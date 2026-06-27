#!/usr/bin/env python3
"""Transcribe a voice note, summarize it, and save it into Obsidian."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class VoiceNoteOutput:
    idea_name: str
    summary: list[str]
    strengths: list[str]
    risks: list[str]
    next_action: str


@dataclass(frozen=True)
class Config:
    transcription_model: str
    summary_model: str
    transcription_language: str | None
    transcription_prompt: str | None
    summary_prompt: str
    include_transcript: bool
    tags: list[str]
    template_tag_line: str | None
    write_mode: str
    vault_path: Path | None
    voice_folder: str
    mcp_command: str | None
    mcp_tool: str | None
    mcp_template: str | None
    mcp_patch_tool: str
    mcp_extra_args: dict[str, Any]
    mcp_required_env: list[str]


def main() -> int:
    args = parse_args()
    load_environment()
    config = load_config()

    audio_path = args.audio_file.expanduser().resolve()
    if not audio_path.exists():
        raise SystemExit(f"Audio file not found: {audio_path}")
    if not audio_path.is_file():
        raise SystemExit(f"Audio path is not a file: {audio_path}")

    try:
        from openai import OpenAI
    except ModuleNotFoundError as exc:
        raise SystemExit("Missing dependency: run `pip install -r requirements.txt`.") from exc

    client = OpenAI()

    print(f"Transcribing {audio_path.name} with {config.transcription_model}...")
    transcript = transcribe_audio(client, audio_path, config)

    print(f"Analyzing with {config.summary_model}...")
    note_data = analyze_transcript(client, transcript, config)

    created_at = dt.datetime.now().astimezone()
    title = note_data.idea_name.strip() or audio_path.stem
    note_filename = f"{safe_note_filename(title)}.md"
    note_relative_path = str(Path(config.voice_folder) / note_filename)
    summary_markdown = render_summary(note_data)
    note_preview = render_note(
        title=title,
        summary_markdown=summary_markdown,
        transcript=transcript,
        audio_path=audio_path,
        created_at=created_at,
        config=config,
    )

    if args.dry_run:
        if config.write_mode == "mcp" and config.mcp_template:
            print(render_template_preview(summary_markdown, transcript, config))
        else:
            print(note_preview)
        return 0

    destination = save_note(
        note_relative_path=note_relative_path,
        title=title,
        summary_markdown=summary_markdown,
        transcript=transcript,
        audio_path=audio_path,
        created_at=created_at,
        config=config,
    )
    print(f"Saved note: {destination}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe an audio file, summarize the idea, and save it to Obsidian."
    )
    parser.add_argument("audio_file", type=Path, help="Path to an audio recording.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the generated Markdown instead of writing to Obsidian.",
    )
    return parser.parse_args()


def load_config() -> Config:
    summary_prompt = getenv(
        "MINDSPEAK_SUMMARY_PROMPT",
        "Summarize this walking voice note as an Obsidian-ready idea note.",
    )
    tags = [
        tag.strip().lstrip("#")
        for tag in getenv("MINDSPEAK_TAGS", "voice,mindspeak").split(",")
        if tag.strip()
    ]
    write_mode = getenv("OBSIDIAN_WRITE_MODE", "file").lower()
    if write_mode not in {"file", "mcp"}:
        raise SystemExit("OBSIDIAN_WRITE_MODE must be either 'file' or 'mcp'.")

    vault_path_raw = os.getenv("OBSIDIAN_VAULT_PATH")
    vault_path = Path(vault_path_raw).expanduser() if vault_path_raw else None
    mcp_extra_args = parse_json_env("OBSIDIAN_MCP_EXTRA_ARGS", {})

    return Config(
        transcription_model=getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-transcribe"),
        summary_model=getenv("OPENAI_SUMMARY_MODEL", "gpt-4.1-mini"),
        transcription_language=blank_to_none(os.getenv("OPENAI_TRANSCRIPTION_LANGUAGE")),
        transcription_prompt=blank_to_none(os.getenv("OPENAI_TRANSCRIPTION_PROMPT")),
        summary_prompt=summary_prompt,
        include_transcript=getenv("MINDSPEAK_INCLUDE_TRANSCRIPT", "true").lower()
        in TRUE_VALUES,
        tags=tags,
        template_tag_line=blank_to_none(os.getenv("MINDSPEAK_TEMPLATE_TAG_LINE")),
        write_mode=write_mode,
        vault_path=vault_path,
        voice_folder=getenv("OBSIDIAN_VOICE_FOLDER", "Voice").strip("/"),
        mcp_command=blank_to_none(os.getenv("OBSIDIAN_MCP_COMMAND")),
        mcp_tool=blank_to_none(os.getenv("OBSIDIAN_MCP_TOOL")),
        mcp_template=blank_to_none(os.getenv("OBSIDIAN_MCP_TEMPLATE")),
        mcp_patch_tool=getenv("OBSIDIAN_MCP_PATCH_TOOL", "patch_vault_file"),
        mcp_extra_args=mcp_extra_args,
        mcp_required_env=parse_csv_env("OBSIDIAN_MCP_REQUIRED_ENV", "OBSIDIAN_API_KEY"),
    )


def transcribe_audio(client: Any, audio_path: Path, config: Config) -> str:
    transcription_args: dict[str, Any] = {"model": config.transcription_model}
    if config.transcription_language:
        transcription_args["language"] = config.transcription_language
    if config.transcription_prompt:
        transcription_args["prompt"] = config.transcription_prompt

    with audio_path.open("rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            file=audio_file,
            **transcription_args,
        )

    text = getattr(transcription, "text", None)
    if not text and isinstance(transcription, str):
        text = transcription
    if not text:
        raise RuntimeError("The transcription response did not include text.")
    return text.strip()


def analyze_transcript(client: Any, transcript: str, config: Config) -> VoiceNoteOutput:
    instructions = (
        "You turn raw walking voice notes into structured idea notes. "
        "Return only fields that are supported by the transcript. "
        "The idea_name must be a concise human-readable title with spaces, not camelCase, snake_case, or a slug."
    )
    response = client.responses.create(
        model=config.summary_model,
        instructions=instructions,
        input=f"{config.summary_prompt}\n\nTranscript:\n{transcript}",
        text={
            "format": {
                "type": "json_schema",
                "name": "voice_note_analysis",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "idea_name": {
                            "type": "string",
                            "description": "A short descriptive Title Case idea name with spaces, suitable as an Obsidian note filename.",
                        },
                        "summary": {
                            "type": "array",
                            "description": "Two to four concise bullets summarizing the idea.",
                            "minItems": 2,
                            "maxItems": 4,
                            "items": {"type": "string"},
                        },
                        "strengths": {
                            "type": "array",
                            "description": "Zero to three plausible strengths or validation signals from the transcript.",
                            "maxItems": 3,
                            "items": {"type": "string"},
                        },
                        "risks": {
                            "type": "array",
                            "description": "Zero to three risks, unknowns, or weak assumptions.",
                            "maxItems": 3,
                            "items": {"type": "string"},
                        },
                        "next_action": {
                            "type": "string",
                            "description": "One practical next action for processing the idea later.",
                        },
                    },
                    "required": [
                        "idea_name",
                        "summary",
                        "strengths",
                        "risks",
                        "next_action",
                    ],
                },
            }
        },
    )
    text = getattr(response, "output_text", None)
    if not text:
        raise RuntimeError("The analysis response did not include output_text.")
    data = json.loads(text)
    return VoiceNoteOutput(
        idea_name=str(data["idea_name"]).strip(),
        summary=[str(item).strip() for item in data["summary"] if str(item).strip()],
        strengths=[str(item).strip() for item in data["strengths"] if str(item).strip()],
        risks=[str(item).strip() for item in data["risks"] if str(item).strip()],
        next_action=str(data["next_action"]).strip(),
    )


def render_summary(note_data: VoiceNoteOutput) -> str:
    sections = [markdown_list(note_data.summary)]
    if note_data.strengths or note_data.risks:
        sections.append("## Validation")
    if note_data.strengths:
        sections.append("### Strengths\n\n" + markdown_list(note_data.strengths))
    if note_data.risks:
        sections.append("### Risks\n\n" + markdown_list(note_data.risks))
    if note_data.next_action:
        sections.append("## Next Action\n\n" + note_data.next_action)
    return "\n\n".join(section for section in sections if section.strip()).strip()


def markdown_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def render_note(
    title: str,
    summary_markdown: str,
    transcript: str,
    audio_path: Path,
    created_at: dt.datetime,
    config: Config,
) -> str:
    tags_yaml = "[" + ", ".join(json.dumps(tag) for tag in config.tags) + "]"
    frontmatter = "\n".join(
        [
            "---",
            f"title: {json.dumps(title)}",
            f"created: {created_at.isoformat(timespec='seconds')}",
            f"source_audio: {json.dumps(str(audio_path))}",
            f"transcription_model: {json.dumps(config.transcription_model)}",
            f"summary_model: {json.dumps(config.summary_model)}",
            f"tags: {tags_yaml}",
            "---",
            "",
        ]
    )

    body = summary_markdown.strip()
    if config.include_transcript:
        body = f"{body}\n\n## Transcript\n\n{transcript.strip()}\n"
    else:
        body = f"{body}\n"
    return frontmatter + body


def render_template_preview(summary_markdown: str, transcript: str, config: Config) -> str:
    return "\n\n".join(
        [
            "# Summary",
            summary_markdown,
            "# Transcription",
            render_transcription_section(transcript, config),
        ]
    )


def save_note(
    note_relative_path: str,
    title: str,
    summary_markdown: str,
    transcript: str,
    audio_path: Path,
    created_at: dt.datetime,
    config: Config,
) -> str:
    if config.write_mode == "mcp":
        return save_note_with_mcp_template(
            note_relative_path=note_relative_path,
            title=title,
            summary_markdown=summary_markdown,
            transcript=transcript,
            audio_path=audio_path,
            created_at=created_at,
            config=config,
        )

    note = render_note(
        title=title,
        summary_markdown=summary_markdown,
        transcript=transcript,
        audio_path=audio_path,
        created_at=created_at,
        config=config,
    )
    return save_note_to_vault(note_relative_path, note, config)


def save_note_to_vault(relative_path: str, note: str, config: Config) -> str:
    if not config.vault_path:
        raise SystemExit("Set OBSIDIAN_VAULT_PATH when OBSIDIAN_WRITE_MODE=file.")

    destination = (config.vault_path / relative_path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(note, encoding="utf-8")
    return str(destination)


def save_note_with_mcp_template(
    note_relative_path: str,
    title: str,
    summary_markdown: str,
    transcript: str,
    audio_path: Path,
    created_at: dt.datetime,
    config: Config,
) -> str:
    if not config.mcp_command:
        raise SystemExit("Set OBSIDIAN_MCP_COMMAND when OBSIDIAN_WRITE_MODE=mcp.")
    if not config.mcp_tool:
        raise SystemExit("Set OBSIDIAN_MCP_TOOL when OBSIDIAN_WRITE_MODE=mcp.")
    if not config.mcp_template:
        raise SystemExit("Set OBSIDIAN_MCP_TEMPLATE when OBSIDIAN_WRITE_MODE=mcp.")
    missing_env = [name for name in config.mcp_required_env if not os.getenv(name)]
    if missing_env:
        missing = ", ".join(missing_env)
        raise SystemExit(f"Set required MCP environment variable(s): {missing}.")

    client = StdioMcpClient(config.mcp_command)
    try:
        client.start()
        target_path = unique_mcp_path(client, note_relative_path)
        template_arguments = {
            "title": title,
            "summary": summary_markdown,
            "transcript": transcript if config.include_transcript else "",
            "source_audio": str(audio_path),
            "created": created_at.isoformat(timespec="seconds"),
            "tags": ", ".join(config.tags),
        }
        template_arguments.update({str(key): str(value) for key, value in config.mcp_extra_args.items()})
        client.call_tool(
            config.mcp_tool,
            {
                "name": config.mcp_template,
                "arguments": template_arguments,
                "createFile": "true",
                "targetPath": target_path,
            },
        )
        client.call_tool(
            config.mcp_patch_tool,
            {
                "filename": target_path,
                "operation": "replace",
                "target": "Summary",
                "targetType": "heading",
                "contentType": "text/markdown",
                "content": summary_markdown,
            },
        )
        client.call_tool(
            config.mcp_patch_tool,
            {
                "filename": target_path,
                "operation": "replace",
                "target": "Transcription",
                "targetType": "heading",
                "contentType": "text/markdown",
                "content": render_transcription_section(transcript, config),
            },
        )
        return f"mcp:{target_path}"
    finally:
        client.close()


def unique_mcp_path(client: "StdioMcpClient", desired_path: str) -> str:
    path = Path(desired_path)
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    candidate = desired_path
    counter = 2
    while mcp_file_exists(client, candidate):
        candidate = str(parent / f"{stem}-{counter}{suffix}")
        counter += 1
    return candidate


def mcp_file_exists(client: "StdioMcpClient", filename: str) -> bool:
    try:
        client.call_tool("get_vault_file", {"filename": filename, "format": "markdown"})
        return True
    except RuntimeError as exc:
        if "404" in str(exc) or "Not Found" in str(exc):
            return False
        raise


def render_transcription_section(transcript: str, config: Config) -> str:
    parts = []
    if config.include_transcript:
        parts.append(transcript.strip())
    if config.template_tag_line:
        parts.append(config.template_tag_line)
    return "\n\n".join(part for part in parts if part).strip()


class StdioMcpClient:
    def __init__(self, command: str) -> None:
        self.command = command
        self.next_id = 1
        self.process: subprocess.Popen[str] | None = None

    def start(self) -> None:
        command = parse_command(self.command)
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=os.environ.copy(),
        )
        self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mindspeak", "version": "0.1.0"},
            },
        )
        self.notify("notifications/initialized", {})

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def request(self, method: str, params: dict[str, Any]) -> Any:
        request_id = self.next_id
        self.next_id += 1
        self.write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            message = self.read()
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"MCP {method} failed: {message['error']}")
            return message.get("result")

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.write({"jsonrpc": "2.0", "method": method, "params": params})

    def write(self, message: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise RuntimeError("MCP process is not running.")
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def read(self) -> dict[str, Any]:
        if not self.process or not self.process.stdout:
            raise RuntimeError("MCP process is not running.")
        line = self.process.stdout.readline()
        if not line:
            stderr = ""
            if self.process.stderr:
                stderr = self.process.stderr.read().strip()
            raise RuntimeError(f"MCP process exited unexpectedly. {stderr}".strip())
        return json.loads(line)

    def close(self) -> None:
        if not self.process:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()


def safe_note_filename(value: str) -> str:
    filename = re.sub(r'[\\\\/:*?"<>|]+', " ", value)
    filename = re.sub(r"\s+", " ", filename).strip(" .")
    return filename[:120].strip() or "Voice note"


def parse_command(value: str) -> list[str]:
    stripped = value.strip()
    if stripped.startswith("["):
        parsed = json.loads(stripped)
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise SystemExit("OBSIDIAN_MCP_COMMAND JSON must be an array of strings.")
        return parsed
    return shlex.split(stripped)


def load_environment() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        load_simple_dotenv(Path(".env"))
        return
    load_dotenv()


def load_simple_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def parse_json_env(name: str, default: dict[str, Any]) -> dict[str, Any]:
    raw = os.getenv(name)
    if not raw:
        return default
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise SystemExit(f"{name} must be a JSON object.")
    return parsed


def parse_csv_env(name: str, default: str) -> list[str]:
    return [item.strip() for item in getenv(name, default).split(",") if item.strip()]


def getenv(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value is not None and value != "" else default


def blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
