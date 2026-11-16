"""Local data-motor configuration and filesystem layout.

Values marked as startup values are placeholders pending calibration
(spec section 16); they are not validated performance parameters.

Configuration is loaded from a versioned `defaults.toml` (ADR-006). The
canonical content of that file is hashed into `config_hash`, which enters the
`job_key` so any configuration change yields a distinct, traceable result
(spec section 7).
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path


@dataclass(frozen=True)
class MotorPaths:
    """Concrete filesystem locations derived from a single root."""

    root: Path
    inbox: Path
    model_inputs: Path
    processing: Path
    failed: Path
    quarantine: Path
    tmp: Path
    processed: Path
    model_outputs: Path
    sqlite_path: Path

    @classmethod
    def from_root(cls, root: Path) -> MotorPaths:
        root = Path(root)
        data = root / "data"
        data.mkdir(mode=0o750, parents=True, exist_ok=True)
        paths = cls(
            root=root,
            inbox=data / "inbox",
            model_inputs=data / "model-inputs",
            processing=data / "processing",
            failed=data / "failed",
            quarantine=data / "quarantine",
            tmp=data / "tmp",
            processed=data / "processed",
            model_outputs=data / "model-outputs",
            sqlite_path=data / "motor.sqlite3",
        )
        for directory in (
            paths.inbox,
            paths.model_inputs,
            paths.processing,
            paths.failed,
            paths.quarantine,
            paths.tmp,
            paths.processed,
            paths.model_outputs,
        ):
            directory.mkdir(mode=0o750, parents=True, exist_ok=True)
        return paths


def hash_config(raw: bytes) -> str:
    """Return the sha256 of the canonical configuration content (ADR-006)."""
    return hashlib.sha256(raw).hexdigest()


def packaged_defaults_path() -> Path:
    """Return the path of the packaged `defaults.toml` (single source of truth)."""
    return Path(str(resources.files("motor") / "defaults.toml"))


@dataclass(frozen=True)
class MotorConfig:
    """Runtime configuration for the local worker."""

    paths: MotorPaths
    admision_factor: float = 2.0      # startup value, not calibrated (section 16)
    scanner_interval_s: float = 1.0   # startup value, not calibrated
    lease_ttl_s: float = 30.0         # startup value, not calibrated
    config_hash: str = ""             # sha256 of defaults.toml (ADR-006)

    @classmethod
    def from_root(cls, root: Path) -> MotorConfig:
        return cls.from_file(packaged_defaults_path(), root=root)

    @classmethod
    def from_file(cls, toml_path: Path, *, root: Path) -> MotorConfig:
        """Load configuration from a `defaults.toml`, hashing its content."""
        toml_path = Path(toml_path)
        raw = toml_path.read_bytes()
        data = tomllib.loads(raw.decode("utf-8"))
        motor = data.get("motor", {})
        return cls(
            paths=MotorPaths.from_root(root),
            admision_factor=float(motor.get("admision_factor", 2.0)),
            scanner_interval_s=float(motor.get("scanner_interval_s", 1.0)),
            lease_ttl_s=float(motor.get("lease_ttl_s", 30.0)),
            config_hash=hash_config(raw),
        )
