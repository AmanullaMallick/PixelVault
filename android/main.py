"""PixelVault mobile UI (Flet). Thin layer over core/vault.py.

Desktop preview (Windows):   flet run android/main.py
Android APK (one command):   flet build apk --module-name android.main

Status: first version. Paths are typed in, which works on desktop. On a phone,
file picking and app storage still need to be wired to Flet's file picker and
storage paths. That is the next step once the build runs.
"""

import sys
import threading
from pathlib import Path

# Make the repo root importable (for `core`) both in the desktop preview and
# in the packaged app, where this file sits one folder below the root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import flet as ft  # noqa: E402

from core import vault  # noqa: E402
from core.config import Config  # noqa: E402


def main(page: ft.Page):
    page.title = "PixelVault"
    page.padding = 16
    page.scroll = ft.ScrollMode.AUTO

    cfg = Config.redmi_note_9()  # starting profile for the phone

    src = ft.TextField(label="Source file or folder")
    out = ft.TextField(label="Output folder")
    passphrase = ft.TextField(label="Passphrase", password=True)
    tiles = ft.TextField(label="Folder containing tiles")
    dest = ft.TextField(label="Restore into")
    status = ft.Text("Ready.", size=14)
    bar = ft.ProgressBar(value=0, visible=False)

    def set_busy(busy: bool, message: str = ""):
        bar.visible = busy
        if message:
            status.value = message
        page.update()

    def progress(stage: str, done: int, total: int):
        if total > 0:
            bar.value = done / total
        status.value = f"{stage}: {done}/{total}"
        page.update()

    def run_in_background(work, label: str):
        def target():
            set_busy(True, f"{label}...")
            try:
                message = work()
            except Exception as exc:  # show errors on screen instead of closing the app
                message = f"Error: {exc}"
            set_busy(False, message)

        threading.Thread(target=target, daemon=True).start()

    def on_encode(e):
        if not src.value or not out.value:
            status.value = "Enter a source and an output folder."
            page.update()
            return
        if cfg.encryption_enabled and not passphrase.value:
            status.value = "Enter a passphrase (encryption is on)."
            page.update()
            return

        def work():
            found = vault.encode_vault(
                Path(src.value), Path(out.value), cfg,
                passphrase=passphrase.value or None, progress=progress,
            )
            return f"Wrote {len(found)} tiles to {found[0].parent}"

        run_in_background(work, "Encoding")

    def on_restore(e):
        if not tiles.value or not dest.value:
            status.value = "Enter the tile folder and a restore destination."
            page.update()
            return
        groups = vault.scan_folder(Path(tiles.value))
        if not groups:
            status.value = "No PixelVault tiles found in that folder."
            page.update()
            return
        if len(groups) > 1:
            status.value = "That folder holds several vaults. Move one vault into its own folder."
            page.update()
            return
        tile_paths = next(iter(groups.values()))

        def work():
            names, warnings = vault.decode_vault(
                tile_paths, Path(dest.value),
                passphrase=passphrase.value or None, progress=progress,
            )
            return "\n".join([f"Restored {', '.join(names)}."] + [f"Notice: {w}" for w in warnings])

        run_in_background(work, "Restoring")

    page.add(
        ft.Text("PixelVault", size=26, weight=ft.FontWeight.BOLD),
        ft.Text("Encode", size=18, weight=ft.FontWeight.BOLD),
        src,
        out,
        passphrase,
        ft.Button("Encode", on_click=on_encode),
        ft.Text("Restore", size=18, weight=ft.FontWeight.BOLD),
        tiles,
        dest,
        ft.Button("Restore", on_click=on_restore),
        bar,
        status,
    )


ft.run(main)
