"""PixelVault mobile UI (Flet). Thin layer over core/vault.py.

Desktop preview (Windows):   flet run android/main.py
Android APK (one command):   flet build apk --module-name android.main

File and folder choices use Flet's FilePicker (pick_files, get_directory_path).
Settings live in config.json inside the app's documents folder.

Design: a light paper background with one dark panel, the pixel grid, that fills
square by square as tiles are made. Navigation sits at the bottom for thumb reach.
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

from android.helpers import describe_plan, lit_cells, overall_progress, stage_label  # noqa: E402
from core import vault  # noqa: E402
from core.config import MAX_PARITY_PERCENT, load_settings, reset_settings, save_settings  # noqa: E402


BUILD_MARKER = "pixel-grid-ui-2026-11-19"
LOG_NAME = "pixelvault.log"

# Palette: neutral paper and ink, one verdigris accent, mint only on the dark panel.
PAPER = "#EDF0F3"
INK = "#17212B"
MUTED = "#5A6775"
FIELD = "#FFFFFF"
LINE = "#D3DAE2"
ACCENT = "#0F766E"
ON_INK = "#E6EBF1"
CELL_OFF = "#2B3947"
CELL_ON = "#4FD1B5"
CELL_ERR = "#F97066"
ERROR = "#B42318"

GRID_COLS = 18
GRID_ROWS = 4
CELL = 14
GAP = 3
GRID_CELLS = GRID_COLS * GRID_ROWS


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


def button_style():
    return ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6))


def pixel_mark():
    """The app glyph: a 2x2 tile grid with one lit square."""
    def sq(color):
        return ft.Container(width=9, height=9, bgcolor=color, border_radius=2)
    return ft.Column(
        spacing=2,
        controls=[ft.Row(spacing=2, controls=[sq(ACCENT), sq(INK)]),
                  ft.Row(spacing=2, controls=[sq(INK), sq(INK)])],
    )


async def main(page: ft.Page):
    page.title = "PixelVault"
    page.padding = 0
    page.bgcolor = PAPER
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(color_scheme=ft.ColorScheme(
        primary=ACCENT, on_primary="#FFFFFF", surface=PAPER, on_surface=INK, error=ERROR,
    ))

    picker = ft.FilePicker()
    page.services.append(picker)
    storage = ft.StoragePaths()
    page.services.append(storage)

    docs_dir = Path(await storage.get_application_documents_directory())
    # All scratch work happens in the app's own documents folder (always writable).
    # Only finished files are copied to the folder the user picked.
    work_dir = docs_dir / "PixelVault" / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    config_path = docs_dir / "PixelVault" / "config.json"
    log_path = docs_dir / "PixelVault" / LOG_NAME
    log = make_logger(log_path)
    log(f"startup build={BUILD_MARKER} docs={docs_dir} work={work_dir}")

    state = {"cfg": None, "default_folder": None, "profile": "default",
             "sources": [], "source_bytes": 0, "pending": None, "busy": False, "frac": 0.0, "lit": 0}

    def reload_settings():
        state["cfg"], state["default_folder"], state["profile"] = load_settings(config_path)

    reload_settings()

    # --- shared widget builders

    def field(label, **kw):
        return ft.TextField(label=label, filled=True, bgcolor=FIELD, border_radius=6,
                            border=ft.InputBorder.NONE, **kw)

    def section(title, *controls):
        return ft.Column(spacing=10, controls=[
            ft.Text(title, size=16, weight=ft.FontWeight.W_600, color=INK), *controls])

    def path_row(text_field, on_pick):
        return ft.Row(vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Container(content=text_field, expand=True),
            ft.IconButton(icon=ft.Icons.FOLDER_OPEN, icon_color=ACCENT, tooltip="Choose folder",
                          on_click=on_pick),
        ])

    # --- activity panel (the pixel grid)

    cells = [ft.Container(width=CELL, height=CELL, bgcolor=CELL_OFF, border_radius=2)
             for _ in range(GRID_CELLS)]
    grid = ft.Container(
        width=GRID_COLS * (CELL + GAP),
        content=ft.Row(wrap=True, spacing=GAP, run_spacing=GAP, controls=cells),
    )
    panel_title = ft.Text("", size=15, weight=ft.FontWeight.W_600, color=ON_INK)
    panel_body = ft.Text("", size=13, color=ON_INK, selectable=True)
    panel_actions = ft.Row(visible=False, wrap=True, spacing=8)
    panel = ft.Container(
        visible=False, bgcolor=INK, padding=16,
        border_radius=ft.BorderRadius(top_left=14, top_right=14, bottom_left=0, bottom_right=0),
        content=ft.Column(spacing=10, controls=[panel_title, grid, panel_body, panel_actions]),
    )

    def paint_cells(count: int, color: str):
        for i, cell in enumerate(cells):
            cell.bgcolor = color if i < count else CELL_OFF

    def show(title: str, body: str = "", *, grid_visible: bool = False, tone: str = ON_INK):
        panel.visible = True
        panel_title.value = title
        panel_title.color = tone
        panel_body.value = body
        panel_body.visible = bool(body)
        grid.visible = grid_visible
        page.update()

    def reset_grid():
        state["frac"] = 0.0
        state["lit"] = 0
        paint_cells(0, CELL_ON)

    last_stage = {"name": None}

    def set_progress(stage: str, done: int, total: int):
        if stage != last_stage["name"]:
            last_stage["name"] = stage
            log(f"stage={stage} total={total}")
        state["frac"] = max(state["frac"], overall_progress(stage, done, total))  # never step backwards
        state["lit"] = lit_cells(state["frac"], GRID_CELLS)
        paint_cells(state["lit"], CELL_ON)
        panel_title.value = stage_label(stage)
        panel_body.value = f"{done} of {total}" if total > 0 else ""
        panel_body.visible = total > 0
        page.update()

    # --- action buttons (disabled while a job runs, so a second tap cannot start a second job)

    encode_btn = ft.FilledButton("Encode", icon=ft.Icons.LOCK, style=button_style())
    restore_btn = ft.FilledButton("Restore", icon=ft.Icons.LOCK_OPEN, style=button_style())

    def set_busy(busy: bool):
        state["busy"] = busy
        encode_btn.disabled = busy
        restore_btn.disabled = busy

    def run_in_background(label: str, work):
        def target():
            set_busy(True)
            reset_grid()
            panel_actions.visible = False
            log(f"{label}: started")
            show(f"{label}...", "", grid_visible=True)
            tone, title = ON_INK, "Done"
            try:
                kind, message = work()
                log(f"{label}: finished: {message}")
                if kind == "ok":
                    paint_cells(GRID_CELLS, CELL_ON)
                else:
                    tone, title = CELL_ERR, "Finished with a problem"
            except Exception as exc:  # show errors on screen instead of closing the app
                log(f"{label}: failed: {exc}\n{traceback.format_exc()}")
                message, tone, title = str(exc), CELL_ERR, f"{label} failed"
                paint_cells(state["lit"], CELL_ERR)
            set_busy(False)
            show(title, message, grid_visible=True, tone=tone)

        threading.Thread(target=target, daemon=True).start()

    def total_size(paths: list[Path]) -> int:
        total = 0
        for p in paths:
            if p.is_dir():
                total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            else:
                total += p.stat().st_size
        return total

    # ================= Encode screen

    sources_text = ft.Text("No files chosen yet.", size=14, color=MUTED)
    plan_text = ft.Text("", size=13, color=ACCENT, weight=ft.FontWeight.W_500)
    out = field("Output folder", value=state["default_folder"] or "")
    parity_label = ft.Text("", size=14, color=INK)
    parity = ft.Slider(min=0, max=MAX_PARITY_PERCENT, divisions=MAX_PARITY_PERCENT,
                       value=state["cfg"].rs_parity_percent, active_color=ACCENT)
    pw_encode = field("Passphrase", password=True, can_reveal_password=True)
    enc_off_note = ft.Text("Encryption is off. Tiles will hold your data unencrypted. "
                           "Turn it on in Settings.", size=13, color=ERROR, visible=False)

    def parity_value() -> int:
        return int(round(parity.value))

    def refresh_plan():
        parity_label.value = (f"Recovery data: {parity_value()}%" if parity_value()
                              else "Recovery data: off, every tile is needed to restore")
        if state["sources"]:
            plan_text.value = describe_plan(len(state["sources"]), state["source_bytes"],
                                            state["cfg"].chunk_size, parity_value())
        else:
            plan_text.value = ""

    def refresh_encode_view():
        enabled = state["cfg"].encryption_enabled
        pw_encode.visible = enabled
        enc_off_note.visible = not enabled
        refresh_plan()

    def on_parity_change(e):
        state["pending"] = None
        panel_actions.visible = False
        refresh_plan()
        page.update()

    parity.on_change = on_parity_change

    async def choose_sources(e):
        files = await picker.pick_files(dialog_title="Choose files", allow_multiple=True)
        paths = [f.path for f in files if f.path] if files else []
        if not paths:
            show("No files chosen", "Nothing was selected, or this device did not return file paths.")
            return
        state["sources"] = paths
        state["pending"] = None
        panel_actions.visible = False
        try:
            state["source_bytes"] = total_size([Path(p) for p in paths])
        except OSError as exc:
            state["source_bytes"] = 0
            log(f"size check failed: {exc}")
        names = ", ".join(Path(p).name for p in paths)
        sources_text.value = names
        sources_text.color = INK
        refresh_plan()
        page.update()

    async def choose_out(e):
        folder = await picker.get_directory_path(dialog_title="Choose output folder")
        if folder:
            out.value = folder
            page.update()

    def start_encode():
        p = parity_value()
        cfg = dataclasses.replace(state["cfg"], rs_parity_percent=p)
        sources = [Path(s) for s in state["sources"]]

        def work():
            staged = None
            try:
                if len(sources) == 1:
                    src = sources[0]
                else:
                    staged = work_dir / f"pv_input_{uuid.uuid4().hex[:8]}" / "input"
                    staged.mkdir(parents=True)
                    for s in sources:
                        target = staged / s.name
                        if s.is_dir():
                            shutil.copytree(s, target)
                        else:
                            shutil.copy2(s, target)
                    src = staged
                made = vault.encode_vault(
                    src, work_dir / "out", cfg,
                    passphrase=pw_encode.value or None, progress=set_progress,
                    work_dir=work_dir,
                )
                target = Path(out.value.strip()) / made[0].parent.name
                try:
                    target.mkdir(parents=True, exist_ok=True)
                    for tile in made:
                        shutil.copy2(tile, target / tile.name)
                except OSError as exc:
                    log(f"copy to output failed: {exc}")
                    return ("problem", f"Tiles were made but could not be copied to {target} ({exc}). "
                                       f"They are kept in {made[0].parent}.")
                shutil.rmtree(made[0].parent, ignore_errors=True)
                return ("ok", f"Wrote {len(made)} tiles to {target}")
            finally:
                if staged is not None:
                    shutil.rmtree(staged.parent, ignore_errors=True)

        run_in_background("Encoding", work)

    def continue_encode(e):
        panel_actions.visible = False
        start_encode()

    def turn_parity_off(e):
        parity.value = 0
        state["pending"] = None
        panel_actions.visible = False
        refresh_plan()
        show("Recovery data turned off", "Press Encode to continue.")

    def on_encode(e):
        if state["busy"]:
            return
        panel_actions.visible = False  # clear buttons left by an earlier recovery-data notice
        if not state["sources"]:
            show("Choose files first", "Pick the files or folders to turn into tiles.")
            return
        if not out.value.strip():
            show("Choose an output folder", "Tiles are copied there when encoding finishes.")
            return
        cfg = dataclasses.replace(state["cfg"], rs_parity_percent=parity_value())
        if cfg.encryption_enabled and not pw_encode.value:
            show("Enter a passphrase", "Encryption is on, so tiles need a passphrase.")
            return
        warning = vault.parity_warning_for_size(state["source_bytes"], cfg)
        if warning:
            state["pending"] = warning
            panel_actions.controls = [
                ft.FilledButton("Continue", on_click=continue_encode, style=button_style(),
                                bgcolor=CELL_ON, color=INK),
                ft.OutlinedButton("Set recovery to 0", on_click=turn_parity_off, style=button_style()),
            ]
            panel_actions.visible = True
            show("Recovery data adds images", warning)
            return
        state["pending"] = None
        start_encode()

    encode_btn.on_click = on_encode

    encode_screen = ft.Column(spacing=22, controls=[
        section("Files", ft.FilledButton("Choose files", icon=ft.Icons.ADD, on_click=choose_sources,
                                          style=button_style()),
                sources_text, plan_text),
        section("Save tiles to", path_row(out, choose_out)),
        section("Protection", enc_off_note, pw_encode, parity_label, parity),
        encode_btn,
    ])

    # ================= Restore screen

    tiles = field("Folder containing tiles")
    dest = field("Restore into")
    pw_restore = field("Passphrase", password=True, can_reveal_password=True)

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

    def on_restore(e):
        if state["busy"]:
            return
        if not tiles.value.strip() or not dest.value.strip():
            show("Choose both folders", "Pick the folder with the tiles and a folder to restore into.")
            return
        groups = vault.scan_folder(Path(tiles.value.strip()))
        if not groups:
            show("No tiles found", "That folder has no PixelVault tiles.")
            return
        if len(groups) > 1:
            show("Several vaults in one folder", "Move one vault into its own folder, then try again.")
            return
        tile_paths = next(iter(groups.values()))

        def work():
            restore_dir = work_dir / "restore_out"
            names, warnings = vault.decode_vault(
                tile_paths, restore_dir,
                passphrase=pw_restore.value or None, progress=set_progress,
                work_dir=work_dir,
            )
            dest_dir = Path(dest.value.strip())
            clashes = [n for n in names if (dest_dir / n).exists()]
            if clashes:
                shutil.rmtree(restore_dir, ignore_errors=True)
                raise ValueError(f"would overwrite existing files: {', '.join(clashes[:5])}")
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                for n in names:
                    src_item = restore_dir / n
                    if src_item.is_dir():
                        shutil.copytree(src_item, dest_dir / n)
                    else:
                        shutil.copy2(src_item, dest_dir / n)
            except OSError as exc:
                log(f"copy to restore folder failed: {exc}")
                return ("problem", f"Restored, but could not copy to {dest_dir} ({exc}). "
                                   f"Files are kept in {restore_dir}.")
            shutil.rmtree(restore_dir, ignore_errors=True)
            return ("ok", "\n".join([f"Restored {', '.join(names)} into {dest_dir}."]
                                    + [f"Notice: {w}" for w in warnings]))

        run_in_background("Restoring", work)

    restore_btn.on_click = on_restore

    restore_screen = ft.Column(spacing=22, controls=[
        section("Tiles", path_row(tiles, choose_tiles)),
        section("Restore into", path_row(dest, choose_dest)),
        section("Passphrase",
                ft.Text("Leave empty if the vault was made without encryption.", size=13, color=MUTED),
                pw_restore),
        restore_btn,
    ])

    # ================= Settings screen

    profile_dd = ft.Dropdown(
        label="Device profile",
        options=[ft.DropdownOption(key="default", text="Default"),
                 ft.DropdownOption(key="redmi", text="Redmi Note 9")],
        value=state["profile"],
    )
    enc_switch = ft.Switch(label="Encrypt tiles", value=state["cfg"].encryption_enabled,
                           active_color=ACCENT)
    settings_folder = field("Default output folder", value=state["default_folder"] or "")
    settings_parity = field("Recovery data %", value=str(state["cfg"].rs_parity_percent),
                            keyboard_type=ft.KeyboardType.NUMBER)
    settings_status = ft.Text("", size=14, color=MUTED)
    log_view = ft.TextField(
        label="Log (last 200 lines). Long-press to select and copy.",
        read_only=True, multiline=True, min_lines=8, max_lines=20, value="", visible=False,
    )

    async def choose_settings_folder(e):
        folder = await picker.get_directory_path(dialog_title="Choose default output folder")
        if folder:
            settings_folder.value = folder
            page.update()

    def on_view_log(e):
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            log_view.value = "\n".join(lines[-200:]) or "Log is empty."
        except OSError:
            log_view.value = "No log yet. Run encode or restore first."
        log_view.visible = True
        page.update()

    def fill_settings_fields():
        profile_dd.value = state["profile"]
        enc_switch.value = state["cfg"].encryption_enabled
        settings_folder.value = state["default_folder"] or ""
        settings_parity.value = str(state["cfg"].rs_parity_percent)
        out.value = state["default_folder"] or ""
        parity.value = state["cfg"].rs_parity_percent
        refresh_encode_view()

    def on_save_settings(e):
        try:
            parity_number = int(settings_parity.value.strip() or "0")
            save_settings(config_path, {
                "profile": profile_dd.value,
                "default_output_folder": settings_folder.value.strip() or None,
                "rs_parity_percent": parity_number,
                "encryption_enabled": bool(enc_switch.value),
            })
        except ValueError as exc:
            settings_status.value = f"Not saved: {exc}"
            settings_status.color = ERROR
            page.update()
            return
        reload_settings()
        fill_settings_fields()
        settings_status.value = "Saved."
        settings_status.color = ACCENT
        page.update()

    def on_reset_settings(e):
        try:
            reset_settings(config_path)
        except ValueError as exc:
            settings_status.value = f"Not reset: {exc}"
            settings_status.color = ERROR
            page.update()
            return
        reload_settings()
        fill_settings_fields()
        settings_status.value = "Factory settings restored."
        settings_status.color = ACCENT
        page.update()

    settings_screen = ft.Column(spacing=22, controls=[
        section("Defaults", profile_dd, enc_switch, path_row(settings_folder, choose_settings_folder),
                settings_parity),
        ft.Row(spacing=8, controls=[
            ft.FilledButton("Save", on_click=on_save_settings, style=button_style()),
            ft.OutlinedButton("Reset to defaults", on_click=on_reset_settings, style=button_style()),
        ]),
        settings_status,
        section("Diagnostics", ft.TextButton("View log", icon=ft.Icons.DESCRIPTION,
                                              on_click=on_view_log), log_view),
    ])

    # ================= Shell: header, body, activity panel, bottom navigation

    screens = [("Encode", encode_screen), ("Restore", restore_screen), ("Settings", settings_screen)]
    screen_title = ft.Text(screens[0][0], size=28, weight=ft.FontWeight.W_700, color=INK)
    body = ft.Container(content=screens[0][1], padding=ft.Padding(left=20, top=4, right=20, bottom=20))

    def on_nav(e):
        index = int(e.control.selected_index)
        screen_title.value = screens[index][0]
        body.content = screens[index][1]
        page.update()

    nav = ft.NavigationBar(
        selected_index=0,
        on_change=on_nav,
        bgcolor=FIELD,
        indicator_color=LINE,
        destinations=[
            ft.NavigationBarDestination(icon=icon("LOCK", "HOME"), label="Encode"),
            ft.NavigationBarDestination(icon=icon("LOCK_OPEN", "HOME"), label="Restore"),
            ft.NavigationBarDestination(icon=icon("SETTINGS", "HOME"), label="Settings"),
        ],
    )

    header = ft.Container(
        padding=ft.Padding(left=20, top=16, right=20, bottom=8),
        content=ft.Column(spacing=10, controls=[
            ft.Row(spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                pixel_mark(), ft.Text("PixelVault", size=15, weight=ft.FontWeight.W_600, color=MUTED)]),
            screen_title,
        ]),
    )

    refresh_encode_view()

    page.add(ft.SafeArea(expand=True, content=ft.Column(spacing=0, expand=True, controls=[
        header,
        ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, controls=[body]),
        panel,
        nav,
    ])))


ft.run(main)
