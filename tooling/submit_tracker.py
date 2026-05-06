"""제출 metadata 트래커.

각 시나리오 폴더의 attempts.jsonl에 한 줄씩 append.
사용 예:
    python -m tooling.submit_tracker log \\
        --scenario track1_0_emergency_triage \\
        --model claude-opus-4-6 \\
        --channel pdf \\
        --family authority_spoofing \\
        --artifact scenarios/track1_0_emergency_triage/artifacts/referral_001.pdf \\
        --result pending \\
        --note "fake pediatric pulmonology director letter"

이후 결과가 나오면:
    python -m tooling.submit_tracker resolve --id <attempt_id> --result breach --score 12.4
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from tooling.common import load_manifest, sha256_file

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios"


def attempt_id(scenario: str, payload: str) -> str:
    h = hashlib.sha256(f"{scenario}|{payload}|{time.time_ns()}".encode()).hexdigest()
    return h[:12]


def log(args: argparse.Namespace) -> int:
    manifest = {}
    if args.manifest:
        manifest = load_manifest(args.manifest)
    scenario = args.scenario or manifest.get("scenario")
    if not scenario:
        print("scenario required unless manifest contains scenario", file=sys.stderr)
        return 2
    scen_dir = SCENARIOS / scenario
    if not scen_dir.is_dir():
        print(f"unknown scenario: {scenario}", file=sys.stderr)
        return 2
    channel = args.channel or manifest.get("channel", "")
    family = args.family or manifest.get("family", "")
    if not channel or not family:
        print("channel and family required unless manifest contains them", file=sys.stderr)
        return 2
    payload = args.payload_text or ""
    if args.artifact and Path(args.artifact).exists():
        payload += "::" + str(Path(args.artifact).resolve())
    aid = attempt_id(scenario, payload or args.note or "")
    artifact_sha256 = args.artifact_sha256
    if not artifact_sha256 and args.artifact and Path(args.artifact).exists():
        artifact_sha256 = sha256_file(args.artifact)
    record = {
        "id": aid,
        "ts": int(time.time()),
        "scenario": scenario,
        "model": args.model,
        "channel": channel,
        "bounty": args.bounty if args.bounty is not None else manifest.get("bounty"),
        "target_action": args.target_action or manifest.get("target_action", ""),
        "family": family,
        "family_id": (manifest.get("extra") or {}).get("family_id", ""),
        "cluster_id": (manifest.get("extra") or {}).get("cluster_id", ""),
        "variant_id": args.variant_id or manifest.get("variant_id", ""),
        "artifact": args.artifact,
        "artifact_sha256": artifact_sha256 or manifest.get("artifact_sha256", ""),
        "description": args.description or manifest.get("description", ""),
        "method": args.method or manifest.get("method", ""),
        "method_detail": args.method_detail or manifest.get("method_detail", ""),
        "visible_text": manifest.get("visible_text", ""),
        "payload_text": args.payload_text or manifest.get("payload_text", ""),
        "result": args.result,
        "score": args.score,
        "note": args.note,
        "review_note": args.review_note,
        "site_submission_id": args.site_submission_id,
    }
    log_path = scen_dir / "attempts.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(aid)
    return 0


def resolve(args: argparse.Namespace) -> int:
    # rewrite the matching jsonl line in place
    for scen_dir in SCENARIOS.iterdir():
        log_path = scen_dir / "attempts.jsonl"
        if not log_path.exists():
            continue
        lines = log_path.read_text(encoding="utf-8").splitlines()
        changed = False
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("id") == args.id:
                rec["result"] = args.result
                if args.score is not None:
                    rec["score"] = args.score
                if args.note:
                    rec["note_resolve"] = args.note
                lines[i] = json.dumps(rec, ensure_ascii=False)
                changed = True
                break
        if changed:
            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"updated in {log_path}")
            return 0
    print(f"id not found: {args.id}", file=sys.stderr)
    return 1


def stats(_: argparse.Namespace) -> int:
    counts: dict[str, dict[str, int]] = {}
    for scen_dir in SCENARIOS.iterdir():
        log_path = scen_dir / "attempts.jsonl"
        if not log_path.exists():
            continue
        c = {"total": 0, "breach": 0, "denied": 0, "rejected": 0, "pending": 0, "submitted": 0, "approved": 0}
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            c["total"] += 1
            r = rec.get("result", "pending")
            if r in c:
                c[r] += 1
        if c["total"]:
            counts[scen_dir.name] = c
    print(json.dumps(counts, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Judgement Day submission tracker")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("log")
    pl.add_argument("--scenario")
    pl.add_argument("--model", default="all")
    pl.add_argument("--channel")
    pl.add_argument("--family")
    pl.add_argument("--artifact")
    pl.add_argument("--manifest")
    pl.add_argument("--payload-text")
    pl.add_argument("--bounty", type=int)
    pl.add_argument("--target-action", default="")
    pl.add_argument("--variant-id", default="")
    pl.add_argument("--artifact-sha256", default="")
    pl.add_argument("--description", default="")
    pl.add_argument("--method", default="")
    pl.add_argument("--method-detail", default="")
    pl.add_argument("--result", default="pending")
    pl.add_argument("--score", type=float)
    pl.add_argument("--note", default="")
    pl.add_argument("--review-note", default="")
    pl.add_argument("--site-submission-id", default="")
    pl.set_defaults(func=log)

    pr = sub.add_parser("resolve")
    pr.add_argument("--id", required=True)
    pr.add_argument("--result", required=True, choices=["breach", "denied", "rejected", "pending", "submitted", "approved"])
    pr.add_argument("--score", type=float)
    pr.add_argument("--note")
    pr.set_defaults(func=resolve)

    ps = sub.add_parser("stats")
    ps.set_defaults(func=stats)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
