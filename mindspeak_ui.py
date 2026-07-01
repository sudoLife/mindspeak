#!/usr/bin/env python3
"""Small desktop UI for adding audio recordings with MindSpeak."""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ModuleNotFoundError:
    DND_FILES = None
    TkinterDnD = None


AUDIO_EXTENSIONS = {
    ".aac",
    ".aiff",
    ".flac",
    ".m4a",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}

ROOT = Path(__file__).resolve().parent
MINDSPEAK_SCRIPT = ROOT / "mindspeak.py"
VENV_PYTHON = ROOT / "venv" / "bin" / "python"

THEMES = {
    "light": {
        "bg": "#efe6db",
        "surface": "#fff8f0",
        "panel": "#f8eee3",
        "tile": "#fffaf5",
        "text": "#2d2420",
        "muted": "#76685f",
        "border": "#dcc8b7",
        "accent": "#8d6e58",
        "accent_hover": "#765a47",
        "secondary": "#e7d4c4",
        "secondary_hover": "#dec5b0",
        "log": "#2d2420",
    },
    "dark": {
        "bg": "#191512",
        "surface": "#241d19",
        "panel": "#2f261f",
        "tile": "#382e26",
        "text": "#f5eadf",
        "muted": "#c9b8aa",
        "border": "#5b4b40",
        "accent": "#c49a78",
        "accent_hover": "#d1aa8b",
        "secondary": "#46372e",
        "secondary_hover": "#554439",
        "log": "#f5eadf",
    },
}


class MindSpeakApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.files: list[Path] = []
        self.dark_mode = tk.BooleanVar(value=False)
        self.busy = False
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.file_rows: list[ctk.CTkFrame] = []

        ctk.set_widget_scaling(1.0)
        ctk.set_window_scaling(1.0)

        self.root.title("MindSpeak")
        self.root.geometry("760x560")
        self.root.minsize(680, 500)

        self.build_ui()
        self.apply_theme()
        self.poll_messages()

    @property
    def palette(self) -> dict[str, str]:
        return THEMES["dark" if self.dark_mode.get() else "light"]

    def build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.app_frame = ctk.CTkFrame(self.root, corner_radius=0, fg_color="transparent")
        self.app_frame.grid(row=0, column=0, sticky="nsew")
        self.app_frame.columnconfigure(0, weight=1)
        self.app_frame.rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.app_frame, corner_radius=0, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 12))
        header.columnconfigure(0, weight=1)

        self.title_label = ctk.CTkLabel(header, text="MindSpeak", font=ctk.CTkFont(size=26, weight="bold"))
        self.title_label.grid(row=0, column=0, sticky="w")

        self.theme_switch = ctk.CTkSwitch(
            header,
            text="Dark",
            variable=self.dark_mode,
            command=self.apply_theme,
            corner_radius=16,
        )
        self.theme_switch.grid(row=0, column=1, sticky="e")

        self.main_card = ctk.CTkFrame(self.app_frame, corner_radius=24, border_width=1)
        self.main_card.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 24))
        self.main_card.columnconfigure(0, weight=1)
        self.main_card.rowconfigure(1, weight=1)

        self.drop_card = ctk.CTkFrame(self.main_card, corner_radius=20, border_width=1)
        self.drop_card.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 12))
        self.drop_card.columnconfigure(0, weight=1)
        self.drop_card.bind("<Button-1>", lambda _event: self.pick_files())

        self.drop_title = ctk.CTkLabel(
            self.drop_card,
            text="Drop audio recordings",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        self.drop_title.grid(row=0, column=0, pady=(22, 4))
        self.drop_hint = ctk.CTkLabel(self.drop_card, text="or click to choose files", font=ctk.CTkFont(size=13))
        self.drop_hint.grid(row=1, column=0, pady=(0, 22))

        self.register_drop_target()

        body = ctk.CTkFrame(self.main_card, corner_radius=0, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 12))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        self.file_panel = ctk.CTkScrollableFrame(body, corner_radius=18, border_width=1)
        self.file_panel.grid(row=0, column=0, sticky="nsew")
        self.file_panel.columnconfigure(0, weight=1)

        actions = ctk.CTkFrame(self.main_card, corner_radius=0, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 12))
        actions.columnconfigure(1, weight=1)

        self.choose_button = ctk.CTkButton(actions, text="Choose Files", command=self.pick_files, corner_radius=18)
        self.choose_button.grid(row=0, column=0, sticky="w")
        self.clear_button = ctk.CTkButton(actions, text="Clear", command=self.clear_files, corner_radius=18, width=86)
        self.clear_button.grid(row=0, column=1, sticky="e", padx=(8, 8))
        self.add_button = ctk.CTkButton(actions, text="Add 0 notes", command=self.add_notes, corner_radius=18, width=132)
        self.add_button.grid(row=0, column=2, sticky="e")

        self.status_label = ctk.CTkLabel(self.main_card, text="Ready", anchor="w", font=ctk.CTkFont(size=13))
        self.status_label.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 8))

        self.log_box = ctk.CTkTextbox(self.main_card, height=110, corner_radius=18, border_width=1, wrap="word")
        self.log_box.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 18))
        self.log_box.configure(state="disabled")

    def register_drop_target(self) -> None:
        if not TkinterDnD or not DND_FILES:
            self.drop_hint.configure(text="install tkinterdnd2 for drag and drop, or click to choose files")
            return
        target = self.drop_card if hasattr(self.drop_card, "drop_target_register") else self.root
        target.drop_target_register(DND_FILES)
        target.dnd_bind("<<Drop>>", self.handle_drop)

    def apply_theme(self) -> None:
        mode = "dark" if self.dark_mode.get() else "light"
        ctk.set_appearance_mode("Dark" if mode == "dark" else "Light")
        colors = self.palette

        self.root.configure(bg=colors["bg"])
        self.app_frame.configure(fg_color=colors["bg"])
        self.main_card.configure(fg_color=colors["surface"], border_color=colors["border"])
        self.drop_card.configure(fg_color=colors["panel"], border_color=colors["border"])
        self.file_panel.configure(fg_color=colors["panel"], border_color=colors["border"])
        self.log_box.configure(
            fg_color=colors["tile"],
            text_color=colors["log"],
            border_color=colors["border"],
            scrollbar_button_color=colors["secondary"],
            scrollbar_button_hover_color=colors["secondary_hover"],
        )

        for label in [self.title_label, self.drop_title, self.status_label]:
            label.configure(text_color=colors["text"])
        self.drop_hint.configure(text_color=colors["muted"])
        self.theme_switch.configure(
            text_color=colors["text"],
            progress_color=colors["accent"],
            button_color=colors["surface"],
            button_hover_color=colors["tile"],
            fg_color=colors["secondary"],
        )
        self.configure_buttons()
        self.refresh_files()

    def configure_buttons(self) -> None:
        colors = self.palette
        self.choose_button.configure(
            fg_color=colors["secondary"],
            hover_color=colors["secondary_hover"],
            text_color=colors["text"],
        )
        self.clear_button.configure(
            fg_color=colors["secondary"],
            hover_color=colors["secondary_hover"],
            text_color=colors["text"],
        )
        self.add_button.configure(
            fg_color=colors["accent"],
            hover_color=colors["accent_hover"],
            text_color="#fffaf5" if not self.dark_mode.get() else "#191512",
        )

    def handle_drop(self, event: tk.Event) -> None:
        paths = [Path(item) for item in self.root.tk.splitlist(event.data)]
        self.add_files(paths)

    def pick_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Choose audio recordings",
            filetypes=[
                ("Audio recordings", "*.m4a *.mp3 *.wav *.aac *.flac *.ogg *.opus *.webm *.mp4 *.mpeg *.mpga *.aiff"),
                ("All files", "*"),
            ],
        )
        self.add_files(Path(path) for path in paths)

    def add_files(self, paths) -> None:
        added = 0
        existing = {path.resolve() for path in self.files}
        for path in paths:
            path = Path(path).expanduser()
            if not path.exists() or not path.is_file():
                continue
            if path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            resolved = path.resolve()
            if resolved in existing:
                continue
            self.files.append(resolved)
            existing.add(resolved)
            added += 1
        if added:
            self.refresh_files()
            self.write_log(f"Added {added} file(s).")

    def clear_files(self) -> None:
        if self.busy:
            return
        self.files.clear()
        self.refresh_files()
        self.set_status("Ready")

    def refresh_files(self) -> None:
        colors = self.palette
        for row in self.file_rows:
            row.destroy()
        self.file_rows.clear()

        if not self.files:
            empty = ctk.CTkLabel(
                self.file_panel,
                text="No recordings queued",
                text_color=colors["muted"],
                font=ctk.CTkFont(size=14),
            )
            empty.grid(row=0, column=0, sticky="ew", padx=16, pady=26)
            self.file_rows.append(empty)
        else:
            for index, path in enumerate(self.files):
                row = ctk.CTkFrame(self.file_panel, corner_radius=14, fg_color=colors["tile"])
                row.grid(row=index, column=0, sticky="ew", padx=10, pady=(10 if index == 0 else 6, 4))
                row.columnconfigure(0, weight=1)
                name = ctk.CTkLabel(row, text=path.name, text_color=colors["text"], anchor="w")
                name.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 0))
                detail = ctk.CTkLabel(row, text=str(path.parent), text_color=colors["muted"], anchor="w", font=ctk.CTkFont(size=12))
                detail.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))
                self.file_rows.append(row)

        count = len(self.files)
        noun = "note" if count == 1 else "notes"
        self.add_button.configure(text=f"Add {count} {noun}", state="normal" if count and not self.busy else "disabled")
        self.choose_button.configure(state="disabled" if self.busy else "normal")
        self.clear_button.configure(state="disabled" if self.busy or not count else "normal")

    def add_notes(self) -> None:
        if self.busy or not self.files:
            return
        self.busy = True
        self.refresh_files()
        self.set_status(f"Processing 1 of {len(self.files)}...")
        threading.Thread(target=self.run_mindspeak_batch, args=(list(self.files),), daemon=True).start()

    def run_mindspeak_batch(self, files: list[Path]) -> None:
        python = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)
        successes = 0
        failed: list[Path] = []
        for index, path in enumerate(files, start=1):
            self.messages.put(("status", f"Processing {index} of {len(files)}: {path.name}"))
            result = subprocess.run(
                [str(python), str(MINDSPEAK_SCRIPT), str(path)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            if result.returncode == 0:
                successes += 1
                self.messages.put(("log", result.stdout.strip() or f"Saved {path.name}"))
            else:
                failed.append(path)
                self.messages.put(("log", f"Failed: {path.name}\n{result.stdout.strip()}"))
        self.messages.put(("done", {"text": f"Added {successes} of {len(files)} note(s).", "failed": failed}))

    def poll_messages(self) -> None:
        try:
            while True:
                kind, text = self.messages.get_nowait()
                if kind == "status":
                    self.set_status(str(text))
                elif kind == "log":
                    self.write_log(str(text))
                elif kind == "done":
                    result = text if isinstance(text, dict) else {"text": str(text), "failed": []}
                    self.busy = False
                    self.files = list(result["failed"])
                    self.refresh_files()
                    self.set_status(str(result["text"]))
                    self.write_log(str(result["text"]))
        except queue.Empty:
            pass
        self.root.after(100, self.poll_messages)

    def set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def write_log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        if self.log_box.index("end-1c") != "1.0":
            self.log_box.insert("end", "\n\n")
        self.log_box.insert("end", text)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")


def main() -> int:
    if TkinterDnD:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    MindSpeakApp(root)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
