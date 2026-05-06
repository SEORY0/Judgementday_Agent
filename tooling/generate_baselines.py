"""Generate a small first-wave artifact set for the highest-priority cells."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(args: list[str]) -> None:
    cmd = [sys.executable, "-m"] + args
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    p = argparse.ArgumentParser(description="Generate baseline artifacts for first Judgement Day submissions.")
    p.add_argument("--seed", type=int, default=20260501)
    p.add_argument("--skip-audio", action="store_true", help="Skip edge-tts generation.")
    args = p.parse_args()

    seeds = [args.seed, args.seed + 1, args.seed + 2]
    for seed in seeds:
        run(["tooling.injectors.slack_dm", "--variant", "json_tool", "--seed", str(seed)])
        run(["tooling.injectors.slack_dm", "--variant", "thread_quote", "--seed", str(seed)])
    for seed in seeds:
        run(["tooling.image_gen.cctv_overlay", "--seed", str(seed), "--work-order", f"MNT-{4471 + seed % 97}", "--technician", f"JP-{118 + seed % 31}"])
    for kind in ["sil", "ministerial", "forensic"]:
        for seed in seeds[:2]:
            run(["tooling.pdf_gen.document", "--kind", kind, "--seed", str(seed)])
    for preset in ["dam_drone", "epidemic_briefing"]:
        run(["tooling.video_gen.clip", "--preset", preset, "--duration", "8", "--seed", str(args.seed)])
    if not args.skip_audio:
        for preset in ["soc_cmdb", "aircraft_bpat", "robot_override"]:
            run(["tooling.audio_gen.tts", "--preset", preset, "--radio", "--seed", str(args.seed)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
