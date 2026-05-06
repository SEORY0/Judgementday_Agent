"""유사도 사전 검사. 같은 시나리오 안에서 기존 제출 텍스트와 cosine ≥ THRESHOLD면 경고.

사용 예:
    python -m tooling.dedupe check \\
        --scenario track1_0_emergency_triage \\
        --text "이번 referral letter 본문..."

기본 모델은 가벼운 sentence-transformers (all-MiniLM-L6-v2). 미설치 시 SimHash로 fallback.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from tooling.common import combine_text, load_manifest

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios"
THRESHOLD = 0.80


def _load_attempts_text(scen_dir: Path) -> list[tuple[str, str]]:
    log_path = scen_dir / "attempts.jsonl"
    if not log_path.exists():
        return []
    out: list[tuple[str, str]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        manifest_text = ""
        artifact = rec.get("artifact")
        if artifact:
            manifest_path = Path(str(artifact) + ".manifest.json")
            if manifest_path.exists():
                try:
                    manifest = load_manifest(manifest_path)
                    manifest_text = combine_text(
                        manifest.get("visible_text", ""),
                        manifest.get("payload_text", ""),
                    )
                except Exception:
                    manifest_text = ""
        text = combine_text(
            rec.get("visible_text", ""),
            rec.get("payload_text", ""),
            manifest_text,
        )
        if not text:
            text = combine_text(
                rec.get("description", ""),
                rec.get("method_detail", ""),
                rec.get("note", ""),
            )
        if not text:
            text = combine_text(
                rec.get("description", ""),
                rec.get("method_detail", ""),
            )
        out.append((rec.get("id", ""), text))
    return out


def _manifest_for_artifact(artifact: str | None) -> dict | None:
    if not artifact:
        return None
    manifest_path = Path(str(artifact) + ".manifest.json")
    if not manifest_path.exists():
        return None
    try:
        return load_manifest(manifest_path)
    except Exception:
        return None


def _load_prior_manifests(scen_dir: Path) -> list[tuple[str, dict]]:
    log_path = scen_dir / "attempts.jsonl"
    if not log_path.exists():
        return []
    out: list[tuple[str, dict]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        manifest = _manifest_for_artifact(rec.get("artifact"))
        if manifest:
            out.append((rec.get("id", ""), manifest))
    return out


def _image_hash(path: str | Path) -> int | None:
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        img = Image.open(path).convert("L").resize((8, 8))
    except Exception:
        return None
    pixels = list(img.getdata())
    avg = sum(pixels) / len(pixels)
    bits = 0
    for i, px in enumerate(pixels):
        if px >= avg:
            bits |= 1 << i
    return bits


def _hash_similarity(a: int, b: int) -> float:
    return 1.0 - (bin(a ^ b).count("1") / 64.0)


def _image_similarity_warnings(current: dict, priors: list[tuple[str, dict]]) -> list[tuple[str, float, str]]:
    artifact = current.get("artifact")
    if not artifact:
        return []
    current_hash = _image_hash(artifact)
    if current_hash is None:
        return []
    current_extra = current.get("extra") or {}
    current_scene = str(current_extra.get("scene_type") or current_extra.get("template") or "")
    current_layout = str(current_extra.get("layout_cluster") or current_extra.get("cluster_id") or "")
    current_theme = str(current_extra.get("visible_text_theme") or "")
    warnings: list[tuple[str, float, str]] = []
    for aid, prior in priors:
        prior_hash = _image_hash(prior.get("artifact", ""))
        if prior_hash is None:
            continue
        sim = _hash_similarity(current_hash, prior_hash)
        prior_extra = prior.get("extra") or {}
        prior_scene = str(prior_extra.get("scene_type") or prior_extra.get("template") or "")
        prior_layout = str(prior_extra.get("layout_cluster") or prior_extra.get("cluster_id") or "")
        prior_theme = str(prior_extra.get("visible_text_theme") or "")
        same_scene = current_scene and prior_scene and current_scene == prior_scene
        same_layout = current_layout and prior_layout and current_layout == prior_layout
        same_theme = current_theme and prior_theme and current_theme == prior_theme
        if sim >= 0.95 or (same_scene and sim >= 0.90) or (same_layout and sim >= 0.88) or (same_theme and sim >= 0.90):
            if sim >= 0.95:
                reason = "image_phash"
            elif same_layout and sim >= 0.88:
                reason = f"same_layout:{current_layout}"
            elif same_theme and sim >= 0.90:
                reason = f"same_theme:{current_theme}"
            else:
                reason = f"same_scene:{current_scene}"
            warnings.append((aid, sim, reason))
    return warnings


def _try_st(text: str, prior: list[str]) -> list[float] | None:
    try:
        from sentence_transformers import SentenceTransformer, util  # type: ignore
    except Exception:
        return None
    model = SentenceTransformer("all-MiniLM-L6-v2")
    emb = model.encode([text] + prior, convert_to_tensor=True)
    sims = util.cos_sim(emb[0:1], emb[1:]).cpu().numpy().tolist()[0]
    return [float(s) for s in sims]


def _simhash_fallback(text: str, prior: list[str]) -> list[float]:
    try:
        from simhash import Simhash  # type: ignore
    except Exception:
        # 마지막 fallback: token Jaccard
        def jacc(a: str, b: str) -> float:
            sa = set(a.lower().split())
            sb = set(b.lower().split())
            if not sa or not sb:
                return 0.0
            return len(sa & sb) / len(sa | sb)
        return [jacc(text, p) for p in prior]
    h = Simhash(text)
    return [1 - (h.distance(Simhash(p)) / 64.0) for p in prior]


def check(args: argparse.Namespace) -> int:
    scen_dir = SCENARIOS / args.scenario
    if not scen_dir.is_dir():
        print(f"unknown scenario: {args.scenario}", file=sys.stderr)
        return 2
    text = args.text
    manifest = None
    if args.manifest:
        manifest = load_manifest(args.manifest)
        text = combine_text(
            text,
            manifest.get("visible_text", ""),
            manifest.get("payload_text", ""),
        )
        if not text:
            text = combine_text(
                manifest.get("description", ""),
                manifest.get("method_detail", ""),
            )
    prior = _load_attempts_text(scen_dir)
    if not prior:
        print("no prior attempts. ok.")
        return 0
    prior_texts = [p[1] for p in prior]
    semantic = args.semantic or os.environ.get("JD_DEDUPE_SEMANTIC") == "1"
    sims = (_try_st(text, prior_texts) if semantic else None) or _simhash_fallback(text, prior_texts)
    flagged = [(prior[i][0], sims[i]) for i in range(len(prior)) if sims[i] >= THRESHOLD]
    image_flagged = _image_similarity_warnings(manifest, _load_prior_manifests(scen_dir)) if manifest else []
    if flagged or image_flagged:
        print("WARN: similar to prior attempts (>= {:.2f}):".format(THRESHOLD))
        for aid, s in sorted(flagged, key=lambda x: -x[1]):
            print(f"  {aid}  sim={s:.3f}")
        for aid, s, reason in sorted(image_flagged, key=lambda x: -x[1]):
            print(f"  {aid}  image_sim={s:.3f}  reason={reason}")
        return 1
    top = max(range(len(sims)), key=lambda i: sims[i])
    print(f"ok. closest prior: {prior[top][0]} sim={sims[top]:.3f}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Submission similarity dedupe")
    sub = p.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("check")
    pc.add_argument("--scenario", required=True)
    pc.add_argument("--text", default="")
    pc.add_argument("--manifest")
    pc.add_argument("--semantic", action="store_true", help="Use sentence-transformers if available. Slower; fallback is always available.")
    pc.set_defaults(func=check)
    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
