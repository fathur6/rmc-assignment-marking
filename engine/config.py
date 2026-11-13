"""Module configuration and credential resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MODULE_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Course:
    """Course identity printed on every artifact."""

    code: str = "PPS10203"
    title: str = "Research Methodology"
    semester: str = "SEM II 2025/2026"
    assignment_weight: int = 60
    final_exam_weight: int = 40


@dataclass
class Marking:
    """Model and marking policy."""

    evaluator: str = "Fathurrahman Lananan"
    model: str = "deepseek-flash"
    api_base: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    temperature: float = 0.1
    max_output_tokens: int = 16000
    timeout_seconds: int = 300
    comments_min: int = 8
    comments_max: int = 14
    max_input_chars: int = 240000
    min_text_chars: int = 1200
    review_confidence_threshold: float = 0.7


@dataclass
class Batch:
    """Batch processing defaults."""

    concurrency: int = 4
    inbox: str = "~/Documents/RMC-Marking/inbox"
    output_dir: str = "~/Documents/RMC-Marking/marked"
    folder_pattern: str = "{matric} - {name}"
    overwrite: bool = False


@dataclass
class Config:
    """Resolved module configuration plus credential lookup."""

    course: Course = field(default_factory=Course)
    marking: Marking = field(default_factory=Marking)
    batch: Batch = field(default_factory=Batch)
    paths: dict[str, str] = field(
        default_factory=lambda: {
            "form_template": "assets/marking-form.pdf",
            "calibration": "prompts/calibration.json",
        }
    )
    root: Path = MODULE_ROOT
    config_path: Path | None = None

    def resolve(self, value: str) -> Path:
        """Resolve a configured path against the module root."""
        path = Path(value).expanduser()
        return path if path.is_absolute() else (self.root / path)

    @property
    def form_template(self) -> Path:
        return self.resolve(self.paths["form_template"])

    @property
    def calibration(self) -> Path:
        return self.resolve(self.paths["calibration"])

    @property
    def output_root(self) -> Path:
        return self.resolve(self.batch.output_dir)

    @property
    def inbox_root(self) -> Path:
        return Path(self.batch.inbox).expanduser()

    def api_key(self) -> str:
        """Return the API key from the environment or the DSH credential store."""
        key = os.environ.get(self.marking.api_key_env, "").strip()
        if key:
            return key
        dsh_home = Path(os.environ.get("DSH_HOME", "~/.dsh")).expanduser()
        store = dsh_home / ".credentials.yaml"
        if store.exists():
            try:
                data = yaml.safe_load(store.read_text()) or {}
            except yaml.YAMLError:
                data = {}
            refs = data.get("refs") or {}
            value = refs.get(self.marking.api_key_env)
            if isinstance(value, str) and value.strip():
                return value.strip()
        raise RuntimeError(
            f"No API key: set ${self.marking.api_key_env} or add it to {store}"
        )

    def redacted(self) -> dict[str, Any]:
        """Configuration view safe to print (no key material is stored here)."""
        return {
            "course": vars(self.course),
            "marking": vars(self.marking),
            "batch": vars(self.batch),
            "root": str(self.root),
        }


def _merge(target: Any, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if not hasattr(target, key) or value is None:
            continue
        setattr(target, key, value)


def load_config(path: str | Path | None = None) -> Config:
    """Load config.yaml, then apply CLI overrides separately."""
    config = Config()
    config_path = Path(path).expanduser() if path else MODULE_ROOT / "config.yaml"
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text()) or {}
        _merge(config.course, raw.get("course") or {})
        _merge(config.marking, raw.get("marking") or {})
        _merge(config.batch, raw.get("batch") or {})
        paths = raw.get("paths") or {}
        if isinstance(paths, dict):
            config.paths.update({k: str(v) for k, v in paths.items() if v is not None})
        config.config_path = config_path
    config.root = MODULE_ROOT
    return config


def output_root_for(config: Config, override: str | None) -> Path:
    """Resolve an output directory override against the caller's cwd."""
    if override:
        return Path(override).expanduser().resolve()
    configured = config.output_root
    if configured.is_absolute():
        return configured
    # Relative configured paths resolve against the module root.
    return (MODULE_ROOT / configured).resolve()
