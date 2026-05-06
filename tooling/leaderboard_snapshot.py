"""Read the current leaderboard via the logged-in Chrome CDP session."""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from tooling.cdp import CDPClient


def parse_rows(raw: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    i = 0
    while i + 2 < len(raw):
        rank = raw[i].replace("🥇", "1").replace("🥈", "2").replace("🥉", "3")
        name = raw[i + 1]
        tail = raw[i + 2].split("\t")
        if rank.isdigit() and len(tail) >= 4:
            rows.append(
                {
                    "rank": int(rank),
                    "name": name,
                    "affiliation": tail[0],
                    "score": float(tail[1]),
                    "submissions": int(tail[2]),
                    "breaches": int(tail[3]),
                }
            )
            i += 3
        else:
            i += 1
    return rows


def snapshot(endpoint: str) -> dict[str, Any]:
    with CDPClient(endpoint) as cdp:
        cdp.navigate("https://judgementday.aim-intelligence.com/leaderboard")
        data = cdp.evaluate(
            r"""
(() => {
  const lines = document.body.innerText.split('\n').map(s=>s.trim()).filter(Boolean);
  const header = 'RANK\tNAME\tAFFILIATION\tSCORE\tSUBMISSIONS\tBREACHES';
  const i = lines.indexOf(header);
  const raw = [];
  if (i >= 0) {
    for (let j=i+1; j<lines.length && lines[j] !== 'BOUNTY MATRIX' && !lines[j].startsWith('Show '); j++) raw.push(lines[j]);
  }
  return {
    captured_at: new Date().toISOString(),
    page_time: lines.find(s => /^\d{2}:\d{2}:\d{2}/.test(s)) || '',
    logged_in: document.body.innerText.includes('Sign Out'),
    raw
  };
})()
"""
        )
    data["rows"] = parse_rows(data.get("raw", []))
    data.pop("raw", None)
    return data


def main() -> int:
    p = argparse.ArgumentParser(description="Capture leaderboard snapshot from Chrome CDP.")
    p.add_argument("--endpoint", default="http://127.0.0.1:9222")
    p.add_argument("--out")
    args = p.parse_args()
    data = snapshot(args.endpoint)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
