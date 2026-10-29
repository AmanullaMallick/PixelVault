"""Textual desktop dashboard. A thin layer over core/vault.py.

Run from the repo root:
    python -m tui.app

Core work runs in a thread worker so the screen stays responsive. Core
progress callbacks are marshalled to the UI thread with App.call_from_thread,
which is the documented way to update widgets from another thread.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Log, ProgressBar, Static

from core import vault
from core.config import MAX_PARITY_PERCENT, Config, load_settings
from tui.progress import STAGE_LABELS

ROOT_CONFIG = Path(__file__).resolve().parent.parent / "config.json"

CSS = """
Screen { layout: vertical; }
#controls { height: 1fr; }
.row { height: auto; margin: 0 0 1 0; }
.row Input { width: 1fr; }
.row Button { margin-left: 1; }
#stage { height: 1; margin: 0 1; }
ProgressBar { height: 1; margin: 0 1 1 1; }
Log { height: 10; border: round $primary; margin: 0 1 1 1; }
"""


class PixelVaultApp(App):
    TITLE = "PixelVault"
    CSS = CSS
    BINDINGS = [("ctrl+q", "quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()
        self._cfg, self._default_folder, self._profile = load_settings(ROOT_CONFIG)
        self._pending_warning: str | None = None  # parity warning waiting for a second press

    # --- layout

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="controls"):
            yield Static("Encode a file or folder into PNG tiles")
            with Horizontal(classes="row"):
                yield Input(placeholder="Source file or folder", id="src")
            with Horizontal(classes="row"):
                yield Input(
                    value=self._default_folder or "",
                    placeholder="Output folder (each vault gets its own subfolder)",
                    id="out",
                )
            with Horizontal(classes="row"):
                yield Input(placeholder="Passphrase (blank if unencrypted)", password=True, id="pw")
                yield Input(placeholder="Repeat passphrase", password=True, id="pw2")
            with Horizontal(classes="row"):
                yield Input(
                    value=str(self._cfg.rs_parity_percent),
                    placeholder=f"Parity % (0 = none, max {MAX_PARITY_PERCENT})",
                    id="parity",
                )
            with Horizontal(classes="row"):
                yield Button("Encode", variant="primary", id="encode")

            yield Static("Restore from tiles")
            with Horizontal(classes="row"):
                yield Input(placeholder="Folder containing tiles", id="tiles")
            with Horizontal(classes="row"):
                yield Input(placeholder="Restore into (must not already contain these files)", id="dest")
            with Horizontal(classes="row"):
                yield Button("Restore", variant="success", id="restore")

        yield Static("", id="stage")
        yield ProgressBar(total=None, show_eta=True, id="bar")
        yield Log(id="log", highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#bar", ProgressBar).update(total=100, progress=0)
        self._log(f"Profile: {self._profile}. Settings from config.json.")

    # --- events

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "encode":
            self._start_encode()
        elif event.button.id == "restore":
            self._start_restore()

    # --- helpers (UI thread)

    def _log(self, message: str) -> None:
        self.query_one("#log", Log).write_line(message)

    def _value(self, widget_id: str) -> str:
        return self.query_one(f"#{widget_id}", Input).value.strip()

    def _set_progress(self, stage: str, done: int, total: int) -> None:
        self.query_one("#stage", Static).update(STAGE_LABELS.get(stage, stage))
        if total > 0:
            self.query_one("#bar", ProgressBar).update(total=total, progress=done)

    def _finish(self, message: str) -> None:
        self.query_one("#stage", Static).update("")
        self.query_one("#bar", ProgressBar).update(total=100, progress=0)
        self._log(message)
        self._set_buttons_disabled(False)

    def _set_buttons_disabled(self, disabled: bool) -> None:
        for button in self.query(Button):
            button.disabled = disabled

    # --- encode

    def _start_encode(self) -> None:
        src, out = self._value("src"), self._value("out")
        pw, pw2 = self._value("pw"), self._value("pw2")
        if not src:
            self._log("Choose a source file or folder first.")
            return
        if not out:
            self._log("Choose an output folder first.")
            return
        if self._cfg.encryption_enabled:
            if not pw:
                self._log("Enter a passphrase (encryption is on in config.json).")
                return
            if pw != pw2:
                self._log("Passphrases do not match.")
                return

        try:
            parity = int(self._value("parity") or "0")
        except ValueError:
            self._log("Parity must be a whole number.")
            return
        if not 0 <= parity <= MAX_PARITY_PERCENT:
            self._log(f"Parity must be between 0 and {MAX_PARITY_PERCENT}.")
            return
        cfg = dataclasses.replace(self._cfg, rs_parity_percent=parity)

        warning = vault.export_warning(Path(src), cfg)
        if warning and self._pending_warning != warning:
            self._pending_warning = warning
            self._log(f"Notice: {warning} Press Encode again to continue.")
            return
        self._pending_warning = None

        self._set_buttons_disabled(True)
        self._log("Encoding started.")
        self.run_worker(
            lambda: self._encode_worker(Path(src), Path(out), pw or None, cfg),
            thread=True, exclusive=True, group="vault", name="encode",
        )

    def _encode_worker(self, src: Path, out: Path, passphrase: str | None, cfg: Config) -> None:
        def progress(stage: str, done: int, total: int) -> None:
            self.call_from_thread(self._set_progress, stage, done, total)

        try:
            tiles = vault.encode_vault(src, out, cfg, passphrase=passphrase, progress=progress)
            message = f"Wrote {len(tiles)} tiles to {tiles[0].parent}"
        except Exception as exc:  # report any failure in the log instead of crashing the screen
            message = f"Error: {exc}"
        self.call_from_thread(self._finish, message)

    # --- restore

    def _start_restore(self) -> None:
        tiles_dir, dest = self._value("tiles"), self._value("dest")
        if not tiles_dir:
            self._log("Choose the folder that contains the tiles.")
            return
        if not dest:
            self._log("Choose a restore destination.")
            return
        groups = vault.scan_folder(Path(tiles_dir))
        if not groups:
            self._log("No PixelVault tiles found in that folder.")
            return
        if len(groups) > 1:
            ids = ", ".join(f"{fid[:8]} ({len(ts)} tiles)" for fid, ts in groups.items())
            self._log(f"That folder holds several vaults: {ids}. Move one vault into its own folder.")
            return
        tiles = next(iter(groups.values()))
        pw = self._value("pw") or None

        self._set_buttons_disabled(True)
        self._log("Restore started.")
        self.run_worker(
            lambda: self._restore_worker(tiles, Path(dest), pw),
            thread=True, exclusive=True, group="vault", name="restore",
        )

    def _restore_worker(self, tiles: list[Path], dest: Path, passphrase: str | None) -> None:
        def progress(stage: str, done: int, total: int) -> None:
            self.call_from_thread(self._set_progress, stage, done, total)

        try:
            names, warnings = vault.decode_vault(tiles, dest, passphrase=passphrase, progress=progress)
            lines = [f"Restored {', '.join(names)} into {dest}."]
            lines += [f"Notice: {w}" for w in warnings]
            message = "\n".join(lines)
        except Exception as exc:  # same reasoning as the encode worker
            message = f"Error: {exc}"
        self.call_from_thread(self._finish, message)


def main() -> None:
    PixelVaultApp().run()


if __name__ == "__main__":
    main()
