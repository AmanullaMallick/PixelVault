"""PixelVault mobile UI (Flet). Thin layer over core/vault.py.

Desktop preview (Windows):   flet run android/main.py
Android APK (one command):   flet build apk --module-name android.main

File and folder choices use Flet's FilePicker (pick_files, get_directory_path).
Settings live in config.json inside the app's documents folder.
API names were checked against Flet's source (flet/controls/services).
"""

import dataclasses
import shutil
import sys
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path

# Make the repo root importable (for `core`) in the desktop preview and in the
# packaged app, where this file sits one folder below the root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import flet as ft  # noqa: E402

from core import vault  # noqa: E402
from core.config import MAX_PARITY_PERCENT, load_settings, reset_settings, save_settings  # noqa: E402


BUILD_MARKER = "diag-2026-10-30-encode-restore"
LOG_NAME = "pixelvault.log"


def make_logger(log_path: Path):
    """Append timestamped lines to log_path. Never raises, so logging cannot break the app."""
    def log(message: str) -> None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
        except OSError:
            pass
    return log


def icon(name: str, fallback: str):
    """Material icon by name; falls back if this Flet build lacks that name."""
    value = getattr(ft.Icons, name, None)
    return value if value is not None else getattr(ft.Icons, fallback)


async def main(page: ft.Page):
    page.title = "PixelVault"
    page.padding = 16
    page.scroll = ft.ScrollMode.AUTO

    picker = ft.FilePicker()
    page.services.append(picker)
    storage = ft.StoragePaths()
    page.services.append(storage)

    docs_dir = Path(await storage.get_application_documents_directory())
    temp_dir = Path(await storage.get_temporary_directory())
    temp_dir.mkdir(parents=True, exist_ok=True)
    config_path = docs_dir / "PixelVault" / "config.json"
    log = make_logger(docs_dir / "PixelVault" / LOG_NAME)
    log(f"startup build={BUILD_MARKER} docs={docs_dir} temp={temp_dir}")

    state = {"cfg": None, "default_folder": None, "profile": "default", "sources": [], "pending": None}

    def reload_settings():
        state["cfg"], state["default_folder"], state["profile"] = load_settings(config_path)

    reload_settings()

    # --- widgets: encode tab

    sources_text = ft.Text("No files chosen.", size=14)
    out = ft.TextField(label="Output folder", value=state["default_folder"] or "")
    parity = ft.TextField(label=f"Parity % (0 = none, max {MAX_PARITY_PERCENT})",
                          value=str(state["cfg"].rs_parity_percent))
    pw_encode = ft.TextField(label="Passphrase", password=True)

    # --- widgets: restore tab

    tiles = ft.TextField(label="Folder containing tiles")
    dest = ft.TextField(label="Restore into")
    pw_restore = ft.TextField(label="Passphrase", password=True)

    # --- shared status (below the tabs, visible on every tab)

    status = ft.Text("Ready.", size=14)
    bar = ft.ProgressBar(value=0, visible=False)

    # --- helpers

    def show(message: str):
        status.value = message
        page.update()

    last_stage = {"name": None}

    def set_progress(stage: str, done: int, total: int):
        if stage != last_stage["name"]:
            last_stage["name"] = stage
            log(f"stage={stage} total={total}")
        if total > 0:
            bar.value = done / total
        status.value = f"{stage}: {done}/{total}"
        page.update()

    def run_in_background(label: str, work):
        def target():
            bar.visible = True
            log(f"{label}: started")
            show(f"{label}...")
            try:
                message = work()
                log(f"{label}: finished: {message}")
            except Exception as exc:  # show errors on screen instead of closing the app
                log(f"{label}: failed: {exc}\n{traceback.format_exc()}")
                message = f"Error: {exc}"
            bar.visible = False
            bar.value = 0
            show(message)

        threading.Thread(target=target, daemon=True).start()

    def parse_parity() -> int | None:
        try:
            value = int(parity.value.strip() or "0")
        except ValueError:
            show("Parity must be a whole number.")
            return None
        if not 0 <= value <= MAX_PARITY_PERCENT:
            show(f"Parity must be between 0 and {MAX_PARITY_PERCENT}.")
            return None
        return value

    def total_size(paths: list[Path]) -> int:
        total = 0
        for p in paths:
            if p.is_dir():
                total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            else:
                total += p.stat().st_size
        return total

    # --- pickers (async: the dialog returns its result directly)

    async def choose_sources(e):
        files = await picker.pick_files(dialog_title="Choose files", allow_multiple=True)
        paths = [f.path for f in files if f.path]
        if not paths:
            show("No files chosen, or this device did not return file paths.")
            return
        state["sources"] = paths
        state["pending"] = None
        sources_text.value = f"{len(paths)} item(s): " + ", ".join(Path(p).name for p in paths)
        page.update()

    async def choose_out(e):
        folder = await picker.get_directory_path(dialog_title="Choose output folder")
        if folder:
            out.value = folder
            page.update()

    async def choose_tiles(e):
        folder = await picker.get_directory_path(dialog_title="Choose folder with tiles")
        if folder:
            tiles.value = folder
            page.update()

    async def choose_dest(e):
        folder = await picker.get_directory_path(dialog_title="Choose restore folder")
        if folder:
            dest.value = folder
            page.update()

    # --- encode

    def on_encode(e):
        if not state["sources"]:
            show("Choose files to encode first.")
            return
        if not out.value.strip():
            show("Choose an output folder first.")
            return
        p = parse_parity()
        if p is None:
            return
        cfg = dataclasses.replace(state["cfg"], rs_parity_percent=p)
        if cfg.encryption_enabled and not pw_encode.value:
            show("Enter a passphrase (encryption is on in settings).")
            return

        sources = [Path(s) for s in state["sources"]]
        warning = vault.parity_warning_for_size(total_size(sources), cfg)
        if warning and state["pending"] != warning:
            state["pending"] = warning
            show(f"Notice: {warning} Press Encode again to continue.")
            return
        state["pending"] = None

        def work():
            staged = None
            try:
                if len(sources) == 1:
                    src = sources[0]
                else:
                    staged = temp_dir / f"pv_input_{uuid.uuid4().hex[:8]}" / "input"
                    staged.mkdir(parents=True)
                    for s in sources:
                        target = staged / s.name
                        if s.is_dir():
                            shutil.copytree(s, target)
                        else:
                            shutil.copy2(s, target)
                    src = staged
                made = vault.encode_vault(
                    src, Path(out.value.strip()), cfg,
                    passphrase=pw_encode.value or None, progress=set_progress,
                    work_dir=temp_dir,
                )
                return f"Wrote {len(made)} tiles to {made[0].parent}"
            finally:
                if staged is not None:
                    shutil.rmtree(staged.parent, ignore_errors=True)

        run_in_background("Encoding", work)

    # --- restore

    def on_restore(e):
        if not tiles.value.strip() or not dest.value.strip():
            show("Choose the tile folder and a restore folder.")
            return
        groups = vault.scan_folder(Path(tiles.value.strip()))
        if not groups:
            show("No PixelVault tiles found in that folder.")
            return
        if len(groups) > 1:
            show("That folder holds several vaults. Move one vault into its own folder.")
            return
        tile_paths = next(iter(groups.values()))

        def work():
            names, warnings = vault.decode_vault(
                tile_paths, Path(dest.value.strip()),
                passphrase=pw_restore.value or None, progress=set_progress,
                work_dir=temp_dir,
            )
            return "\n".join([f"Restored {', '.join(names)}."] + [f"Notice: {w}" for w in warnings])

        run_in_background("Restoring", work)

    # --- settings tab

    profile_dd = ft.Dropdown(
        label="Profile",
        options=[ft.DropdownOption(key="default", text="Default"),
                 ft.DropdownOption(key="redmi", text="Redmi Note 9")],
        value=state["profile"],
    )
    enc_dd = ft.Dropdown(
        label="Encryption",
        options=[ft.DropdownOption(key="on", text="On"), ft.DropdownOption(key="off", text="Off")],
        value="on" if state["cfg"].encryption_enabled else "off",
    )
    settings_folder = ft.TextField(label="Default output folder", value=state["default_folder"] or "")
    settings_parity = ft.TextField(label="Parity %", value=str(state["cfg"].rs_parity_percent))
    settings_status = ft.Text("", size=14)

    def fill_settings_fields():
        profile_dd.value = state["profile"]
        enc_dd.value = "on" if state["cfg"].encryption_enabled else "off"
        settings_folder.value = state["default_folder"] or ""
        settings_parity.value = str(state["cfg"].rs_parity_percent)
        out.value = state["default_folder"] or ""
        parity.value = str(state["cfg"].rs_parity_percent)

    def on_save_settings(e):
        try:
            parity_value = int(settings_parity.value.strip() or "0")
            save_settings(config_path, {
                "profile": profile_dd.value,
                "default_output_folder": settings_folder.value.strip() or None,
                "rs_parity_percent": parity_value,
                "encryption_enabled": enc_dd.value == "on",
            })
        except ValueError as exc:
            settings_status.value = f"Not saved: {exc}"
            page.update()
            return
        reload_settings()
        fill_settings_fields()
        settings_status.value = "Saved."
        page.update()

    def on_reset_settings(e):
        try:
            reset_settings(config_path)
        except ValueError as exc:
            settings_status.value = f"Not reset: {exc}"
            page.update()
            return
        reload_settings()
        fill_settings_fields()
        settings_status.value = "Factory settings restored."
        page.update()

    settings_panel = [
        profile_dd,
        enc_dd,
        settings_folder,
        settings_parity,
        ft.Row([
            ft.Button("Save", on_click=on_save_settings),
            ft.Button("Set to default", on_click=on_reset_settings),
        ]),
        settings_status,
    ]

    # --- layout: three tabs with icons; the active section name sits under the app title

    titles = ["Encode", "Restore", "Settings"]
    heading = ft.Text("Encode", size=18, weight=ft.FontWeight.BOLD)

    def on_tab_change(e):
        heading.value = titles[tabs.selected_index]
        page.update()

    encode_tab = ft.Column(controls=[
        ft.Button("Choose files or folders", on_click=choose_sources),
        sources_text,
        ft.Row([out, ft.Button("Browse", on_click=choose_out)]),
        parity,
        pw_encode,
        ft.Button("Encode", on_click=on_encode),
    ])
    restore_tab = ft.Column(controls=[
        ft.Row([tiles, ft.Button("Browse", on_click=choose_tiles)]),
        ft.Row([dest, ft.Button("Browse", on_click=choose_dest)]),
        pw_restore,
        ft.Button("Restore", on_click=on_restore),
    ])
    settings_tab = ft.Column(controls=settings_panel)

    tabs = ft.Tabs(
        length=3,
        selected_index=0,
        expand=True,
        on_change=on_tab_change,
        content=ft.Column(expand=True, controls=[
            ft.TabBar(tabs=[
                ft.Tab(icon=icon("LOCK", "HOME")),
                ft.Tab(icon=icon("LOCK_OPEN", "HOME")),
                ft.Tab(icon=icon("SETTINGS", "HOME")),
            ]),
            ft.TabBarView(expand=True, controls=[encode_tab, restore_tab, settings_tab]),
        ]),
    )

    page.add(
        ft.Text("PixelVault", size=26, weight=ft.FontWeight.BOLD),
        heading,
        tabs,
        bar,
        status,
    )


ft.run(main)
