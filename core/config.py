"""Single configuration object read by the core. Nothing else hardcodes limits."""

from __future__ import annotations

from dataclasses import dataclass

MIN_PBKDF2_ITERATIONS = 600_000
MAX_PARITY_PERCENT = 50


@dataclass
class Config:
    canvas_side: int = 4984           # square tile side in pixels
    chunk_size: int = 4 * 1024 * 1024  # plaintext bytes per chunk (RAM control)
    rs_parity_percent: int = 10       # 0 disables parity
    encryption_enabled: bool = True
    pbkdf2_iterations: int = MIN_PBKDF2_ITERATIONS

    def validate(self) -> None:
        if self.canvas_side < 1:
            raise ValueError("canvas_side must be >= 1")
        if self.chunk_size < 1:
            raise ValueError("chunk_size must be >= 1")
        if not 0 <= self.rs_parity_percent <= MAX_PARITY_PERCENT:
            raise ValueError(f"rs_parity_percent must be 0..{MAX_PARITY_PERCENT}")
        if self.encryption_enabled and self.pbkdf2_iterations < MIN_PBKDF2_ITERATIONS:
            raise ValueError(f"pbkdf2_iterations must be >= {MIN_PBKDF2_ITERATIONS}")

    @property
    def tile_capacity(self) -> int:
        """Gross bytes per tile (RGB, 8 bits per channel)."""
        return self.canvas_side * self.canvas_side * 3

    def parity_count(self, k: int) -> int:
        """m = ceil(k * p / 100) when p > 0, else 0 (minimum 1 when p > 0)."""
        if self.rs_parity_percent == 0:
            return 0
        return max(1, -(-(k * self.rs_parity_percent) // 100))
