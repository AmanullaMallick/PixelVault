"""Single configuration object read by the core. Nothing else hardcodes limits."""

from __future__ import annotations

from dataclasses import dataclass

MIN_PBKDF2_ITERATIONS = 600_000
MAX_PARITY_PERCENT = 50


@dataclass
class Config:
    canvas_side: int = 4984              # square tile side in pixels
    chunk_size: int = 4 * 1024 * 1024    # plaintext bytes per chunk (RAM control)
    rs_parity_percent: int = 10          # 0 disables parity
    encryption_enabled: bool = True
    pbkdf2_iterations: int = MIN_PBKDF2_ITERATIONS
    workers: int = 2                     # threads for tile read/write
    ecc_block_bytes: int = 256 * 1024    # bytes per erasure-coding block (RAM control)

    def validate(self) -> None:
        if self.canvas_side < 1:
            raise ValueError("canvas_side must be >= 1")
        if self.chunk_size < 1:
            raise ValueError("chunk_size must be >= 1")
        if not 0 <= self.rs_parity_percent <= MAX_PARITY_PERCENT:
            raise ValueError(f"rs_parity_percent must be 0..{MAX_PARITY_PERCENT}")
        if self.encryption_enabled and self.pbkdf2_iterations < MIN_PBKDF2_ITERATIONS:
            raise ValueError(f"pbkdf2_iterations must be >= {MIN_PBKDF2_ITERATIONS}")
        if self.workers < 1:
            raise ValueError("workers must be >= 1")
        if self.ecc_block_bytes < 1:
            raise ValueError("ecc_block_bytes must be >= 1")

    @property
    def tile_capacity(self) -> int:
        """Gross bytes per tile (RGB, 8 bits per channel)."""
        return self.canvas_side * self.canvas_side * 3

    def parity_count(self, k: int) -> int:
        """m = ceil(k * p / 100) when p > 0, else 0 (minimum 1 when p > 0)."""
        if self.rs_parity_percent == 0:
            return 0
        return max(1, -(-(k * self.rs_parity_percent) // 100))

    @classmethod
    def redmi_note_9(cls) -> "Config":
        """Starting profile for Redmi Note 9 (4 GB RAM, MediaTek Helio G85).

        These are starting values, not measured optima. Tune on the device.
        """
        return cls(
            canvas_side=4984,
            chunk_size=4 * 1024 * 1024,
            rs_parity_percent=10,
            encryption_enabled=True,
            pbkdf2_iterations=MIN_PBKDF2_ITERATIONS,
            workers=4,
            ecc_block_bytes=256 * 1024,
        )


SETTINGS_KEYS = {"default_output_folder", "profile"}


def load_settings(path) -> tuple["Config", str | None, str]:
    """Read a config.json file. Returns (Config, default_output_folder, profile).

    Unknown keys are rejected so typos do not pass silently. Missing file
    yields defaults. Only plain JSON is read; no code is executed.
    """
    import dataclasses
    import json
    from pathlib import Path

    path = Path(path)
    if not path.exists():
        cfg = Config()
        cfg.validate()
        return cfg, None, "default"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a JSON object")
    fields = {f.name for f in dataclasses.fields(Config)}
    unknown = set(data) - fields - SETTINGS_KEYS
    if unknown:
        raise ValueError(f"{path}: unknown keys {sorted(unknown)}")
    profile = data.pop("profile", "default")
    default_folder = data.pop("default_output_folder", None)
    cfg = Config(**data)
    if profile == "redmi":
        base = Config.redmi_note_9()
        base_vals = dataclasses.asdict(base)
        base_vals.update(data)
        cfg = Config(**base_vals)
    elif profile != "default":
        raise ValueError(f"{path}: profile must be 'default' or 'redmi'")
    cfg.validate()
    return cfg, default_folder, profile
