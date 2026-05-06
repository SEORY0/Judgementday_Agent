"""Shared helpers for Judgement Day tooling."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios"


def scenario_dir(scenario: str) -> Path:
    path = SCENARIOS / scenario
    if not path.is_dir():
        raise ValueError(f"unknown scenario: {scenario}")
    return path


def artifact_dir(scenario: str) -> Path:
    path = scenario_dir(scenario) / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def slugify(value: str) -> str:
    allowed = []
    for ch in value.lower().replace("/", "-").replace(" ", "-"):
        if ch.isalnum() or ch in {"-", "_"}:
            allowed.append(ch)
        elif ch in {".", ":"}:
            allowed.append("-")
    out = "".join(allowed).strip("-_")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "artifact"


def timestamp_slug() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_path(artifact_path: str | Path) -> Path:
    path = Path(artifact_path)
    return path.with_suffix(path.suffix + ".manifest.json")


def write_manifest(
    *,
    artifact_path: str | Path,
    scenario: str,
    channel: str,
    family: str,
    generator: str,
    variant_id: str,
    visible_text: str,
    hidden_text: str = "",
    target_action: str = "",
    bounty: int | None = None,
    description: str = "",
    method: str = "programmatic",
    method_detail: str = "",
    payload_text: str = "",
    seed: str | int | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    artifact = Path(artifact_path)
    record: dict[str, Any] = {
        "created_ts": int(time.time()),
        "artifact": str(artifact),
        "artifact_sha256": sha256_file(artifact) if artifact.exists() else "",
        "scenario": scenario,
        "channel": channel,
        "family": family,
        "generator": generator,
        "variant_id": variant_id,
        "target_action": target_action,
        "bounty": bounty,
        "visible_text": visible_text,
        "hidden_text": hidden_text,
        "payload_text": payload_text,
        "description": description,
        "method": method,
        "method_detail": method_detail,
        "seed": seed,
        "extra": extra or {},
    }
    path = manifest_path(artifact)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest = Path(path)
    if not manifest.exists():
        candidate = manifest_path(manifest)
        if candidate.exists():
            manifest = candidate
    return json.loads(manifest.read_text(encoding="utf-8"))


def combine_text(*parts: str | None) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())
