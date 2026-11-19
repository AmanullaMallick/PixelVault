"""Pure helpers for the mobile UI. No Flet import, so they can be unit-tested anywhere."""

from __future__ import annotations

# Stage names come from core/vault.py. Each maps to (position, stage count) in its workflow,
# so the UI can show one overall fraction instead of a bar that restarts at every stage.
_ENCODE_STAGES = {"archive": 0, "chunk": 1, "encrypt": 1, "parity": 2, "write_tiles": 3}
_DECODE_STAGES = {"read_tiles": 0, "rebuild": 1, "decrypt": 2}

STAGE_LABELS = {
    "archive": "Packing files",
    "chunk": "Splitting into blocks",
    "encrypt": "Encrypting",
    "parity": "Adding recovery data",
    "write_tiles": "Writing tiles",
    "read_tiles": "Reading tiles",
    "rebuild": "Rebuilding missing tiles",
    "decrypt": "Decrypting",
}


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage.replace("_", " ").capitalize())


def overall_progress(stage: str, done: int, total: int) -> float:
    """Fraction 0..1 across the whole encode or restore. Unknown stages return 0."""
    if stage in _ENCODE_STAGES:
        index, count = _ENCODE_STAGES[stage], 4
    elif stage in _DECODE_STAGES:
        index, count = _DECODE_STAGES[stage], 3
    else:
        return 0.0
    inner = min(max(done / total, 0.0), 1.0) if total > 0 else 0.0
    return min((index + inner) / count, 1.0)


def lit_cells(fraction: float, cells: int) -> int:
    """How many grid cells to light for a 0..1 fraction."""
    return max(0, min(cells, int(round(fraction * cells))))


def format_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def tile_estimate(total_bytes: int, chunk_size: int, parity_percent: int) -> tuple[int, int]:
    """(data tiles k, parity tiles m), using the same rule as core.vault.parity_warning_for_size."""
    k = max(1, -(-total_bytes // chunk_size))
    m = 0 if parity_percent == 0 else max(1, -(-(k * parity_percent) // 100))
    return k, m


def describe_plan(item_count: int, total_bytes: int, chunk_size: int, parity_percent: int) -> str:
    k, m = tile_estimate(total_bytes, chunk_size, parity_percent)
    items = f"{item_count} item" + ("" if item_count == 1 else "s")
    tiles = f"{k} tile" + ("" if k == 1 else "s")
    extra = f" + {m} recovery" if m else ""
    return f"{items}, {format_bytes(total_bytes)}. About {tiles}{extra}."
