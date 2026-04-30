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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios"
THRESHOLD = 0.85


def _load_attempts_text(scen_dir: Path) -> list[tuple[str, str]]:
    log_path = scen_dir / "attempts.jsonl"
    if not log_path.exists():
        return []
    out: list[tuple[str, str]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        text = rec.get("note", "") or ""
        out.append((rec.get("id", ""), text))
    return out


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
    prior = _load_attempts_text(scen_dir)
    if not prior:
        print("no prior attempts. ok.")
        return 0
    prior_texts = [p[1] for p in prior]
    sims = _try_st(args.text, prior_texts) or _simhash_fallback(args.text, prior_texts)
    flagged = [(prior[i][0], sims[i]) for i in range(len(prior)) if sims[i] >= THRESHOLD]
    if flagged:
        print("WARN: similar to prior attempts (>= {:.2f}):".format(THRESHOLD))
        for aid, s in sorted(flagged, key=lambda x: -x[1]):
            print(f"  {aid}  sim={s:.3f}")
        return 1
    top = max(range(len(sims)), key=lambda i: sims[i])
    print(f"ok. closest prior: {prior[top][0]} sim={sims[top]:.3f}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Submission similarity dedupe")
    sub = p.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("check")
    pc.add_argument("--scenario", required=True)
    pc.add_argument("--text", required=True)
    pc.set_defaults(func=check)
    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
