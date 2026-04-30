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
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios"


def attempt_id(scenario: str, payload: str) -> str:
    h = hashlib.sha256(f"{scenario}|{payload}|{time.time_ns()}".encode()).hexdigest()
    return h[:12]


def log(args: argparse.Namespace) -> int:
    scen_dir = SCENARIOS / args.scenario
    if not scen_dir.is_dir():
        print(f"unknown scenario: {args.scenario}", file=sys.stderr)
        return 2
    payload = args.payload_text or ""
    if args.artifact and Path(args.artifact).exists():
        payload += "::" + str(Path(args.artifact).resolve())
    aid = attempt_id(args.scenario, payload or args.note or "")
    record = {
        "id": aid,
        "ts": int(time.time()),
        "scenario": args.scenario,
        "model": args.model,
        "channel": args.channel,
        "family": args.family,
        "artifact": args.artifact,
        "result": args.result,
        "score": args.score,
        "note": args.note,
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
        c = {"total": 0, "breach": 0, "denied": 0, "pending": 0}
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
    pl.add_argument("--scenario", required=True)
    pl.add_argument("--model", required=True)
    pl.add_argument("--channel", required=True)
    pl.add_argument("--family", required=True)
    pl.add_argument("--artifact")
    pl.add_argument("--payload-text")
    pl.add_argument("--result", default="pending")
    pl.add_argument("--score", type=float)
    pl.add_argument("--note", default="")
    pl.set_defaults(func=log)

    pr = sub.add_parser("resolve")
    pr.add_argument("--id", required=True)
    pr.add_argument("--result", required=True, choices=["breach", "denied", "rejected", "pending"])
    pr.add_argument("--score", type=float)
    pr.add_argument("--note")
    pr.set_defaults(func=resolve)

    ps = sub.add_parser("stats")
    ps.set_defaults(func=stats)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
