"""Progress reporting for terminal front ends.

Turns the core's progress(stage, done, total) callbacks into a single-line bar
with a percentage, the stage name, and a time estimate. Standard library only,
so it runs anywhere; the Textual dashboard can reuse ProgressState later.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import Callable, TextIO

STAGE_LABELS = {
    "archive": "Archiving",
    "chunk": "Chunking",
    "encrypt": "Encrypting",
    "parity": "Computing parity",
    "write_tiles": "Writing tiles",
    "read_tiles": "Reading tiles",
    "rebuild": "Rebuilding shards",
    "decrypt": "Decrypting",
}


def format_bar(fraction: float, width: int = 30) -> str:
    fraction = min(max(fraction, 0.0), 1.0)
    filled = int(round(fraction * width))
    return "[" + "#" * filled + "-" * (width - filled) + f"] {fraction * 100:5.1f}%"


def format_eta(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


@dataclass
class ProgressState:
    """Tracks one stage at a time and estimates time remaining."""

    stage: str = ""
    done: int = 0
    total: int = 0
    started_at: float = field(default_factory=time.monotonic)
    stage_started_at: float = field(default_factory=time.monotonic)

    def update(self, stage: str, done: int, total: int, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if stage != self.stage:
            self.stage = stage
            self.stage_started_at = now
        self.done, self.total = done, total

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 0.0

    def eta(self, now: float | None = None) -> float | None:
        now = time.monotonic() if now is None else now
        elapsed = now - self.stage_started_at
        if self.done <= 0 or self.total <= 0 or elapsed <= 0:
            return None
        return elapsed * (self.total - self.done) / self.done

    def line(self, now: float | None = None) -> str:
        label = STAGE_LABELS.get(self.stage, self.stage or "Working")
        return f"{label:<20} {format_bar(self.fraction)}  ETA {format_eta(self.eta(now))}"


def make_callback(stream: TextIO = sys.stderr) -> Callable[[str, int, int], None]:
    """Return a core progress callback that redraws one line on `stream`."""
    state = ProgressState()

    def callback(stage: str, done: int, total: int) -> None:
        state.update(stage, done, total)
        stream.write("\r" + state.line().ljust(80))
        if total and done >= total:
            stream.write("\n")
        stream.flush()

    return callback
