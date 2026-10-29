# PixelVault

A local, offline utility that packs files into lossless PNG images, encrypts them, and adds Reed-Solomon redundancy so the original can be rebuilt even when some images are lost.

> **Status:** Draft. No code yet. Milestone 1 is a desktop TUI backed by a core pipeline with no UI dependencies.

---

## 1. What it does

1. Packs one file or a folder into an uncompressed `.tar` stream.
2. Optionally encrypts each chunk with AES-256-GCM, using a key derived from a passphrase with PBKDF2-HMAC-SHA256.
3. Adds Reed-Solomon parity across the encrypted chunks.
4. Writes each block as a PNG tile (`compress_level=0`, so no lossy or color-altering steps).
5. Restores the original from any sufficient subset of tiles, and verifies it with SHA-256.

## 2. Design principles

- **Core is independent.** `core/` has no dependency on `tui/` or `android/`.
- **Streaming, not monolithic buffers.** Input and output are processed in chunks so memory use stays bounded.
- **Every parameter is configurable.** Nothing relevant is hardcoded. Platform limits are defaults, not constants.
- **Each tile is self-describing.** A single surviving tile carries the parameters needed to decode it.
- **Metadata stays private.** When encryption is on, the original filename and the archive's SHA-256 live in an encrypted manifest. With encryption off, they are stored in plain text.

## 3. Configuration

All settings live in one config object, read by the core.

| Setting | Default | Notes |
|---|---|---|
| `canvas_side` | 4,984 px | Square tile side. Smaller values create more tiles. |
| `chunk_size` | 4 MB | Crypto/tar block size. Controls RAM use, independent of tile size. |
| `rs_parity_percent` | 10 | 0 to 50. `0` means no parity, and restore needs every tile. |
| `encryption_enabled` | true | When false, tiles hold raw bytes. |
| `pbkdf2_iterations` | 600,000 | Minimum 600,000. |

### Parity rule

- `k` = number of data tiles.
- `m` = `ceil(k × rs_parity_percent / 100)` if the percentage is above 0, otherwise 0.
- Restore needs any `k` of the `k + m` tiles.

### Warnings

1. **At export:** if parity adds storage (e.g., a single-tile file with parity above 0), show a warning. The user can continue or change the setting. The app never overrides the choice.
2. **At restore:** if exactly `k` tiles survive, show one informational notice that there is no remaining redundancy.

## 4. Tile format

Each PNG tile contains:

| Field | Plaintext | Purpose |
|---|---|---|
| `vault_version` | yes | Format version |
| `file_id` | yes | Groups tiles from the same vault |
| `chunk_index`, `total_chunks` | yes | Ordering |
| `k`, `m` | yes | Recovery threshold |
| `canvas_side`, `chunk_size`, `parity_percent` | yes | Decode without the original device config |
| `salt`, `nonce` | yes | Per-tile nonce, per-file salt |
| `payload_size_bytes` | yes | Usable bytes in this tile |
| Encrypted manifest (original filename, archive SHA-256) | **encrypted** when encryption is on; plain text otherwise | Restore checks the rebuilt archive against the hash |

Output files use sequential or random names (e.g., `vault_block_01.png`), never names that encode metadata.

## 5. Pipeline

```
File(s) → tar stream → chunks (4 MB)
        → AES-256-GCM per chunk (unique nonce per tile)   [if enabled]
        → Reed-Solomon parity across ciphertext chunks
        → tile layout → PNG write (streamed rows, compress_level=0)
```

Restore reverses the flow. Missing tiles are rebuilt by Reed-Solomon first, and each chunk's GCM tag is verified after that.

## 6. Project layout

```
pixelvault/
├── core/
│   ├── archiver.py        # tar packing and metadata
│   ├── crypto_engine.py   # PBKDF2 and AES-256-GCM
│   ├── ecc_matrix.py      # Reed-Solomon
│   └── image_tiler.py     # bytes ↔ PNG tiles
├── tui/
│   └── app.py             # Textual interface (after core works)
├── android/
│   └── main.py            # Future mobile port
├── tests/
│   ├── test_crypto.py
│   └── test_pipeline.py   # end-to-end: file → tiles → file
├── requirements.txt
└── README.md
```

## 7. Milestones

1. **Core pipeline (desktop, no UI):** tar → encrypt → chunk → RS → PNG write, then read back with one tile removed, reconstruct, decrypt, and verify SHA-256 against the original.
2. **Desktop TUI:** Textual front end over the core.
3. **Android port:** after the desktop build is stable, with a low-memory profile and encode-time testing on target hardware.

## 8. Known limitations

- **Platform limits are unverified.** The 75 MB and 100 MP figures are carried over from earlier notes and are not yet checked against current Google Photos documentation. They are config defaults and can be changed.
- **Content scanning risk.** Encrypted tiles look like random noise. Cloud providers may flag high-entropy images. Check the provider's terms before relying on cloud backup.
- **Pure-Python PNG encoding** may be slow on large tiles. Encode time is to be measured in Milestone 1.

## 9. Security notes

- Passphrase strength is the main defense. PBKDF2 raises the cost of guessing but does not replace a strong passphrase.
- Tampering with any encrypted byte causes decryption to fail (GCM authentication).
- Plaintext headers reveal the vault's structure (count, sizes, parameters), not its contents.

## 10. Requirements

- Python 3.x (version to be pinned in `requirements.txt` after testing)
- Libraries to be confirmed during Milestone 1 (crypto, Reed-Solomon, PNG writing, TUI later)

---

*License: see `LICENSE`.*
