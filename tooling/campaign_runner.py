"""Campaign queue and execution runner for Judgement Day submissions.

The runner ties existing artifact generators, dedupe checks, form filling, and
local result tracking into one queue-driven workflow. It is intentionally safe
by default: no command clicks the live Submit button unless --submit and a
matching --confirm-batch value are both present.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tooling.cdp import CDPClient
from tooling.common import ROOT, SCENARIOS, load_manifest, slugify
from tooling.leaderboard_snapshot import snapshot as leaderboard_snapshot

QUEUE_PATH = ROOT / "submissions_queue.jsonl"
CAMPAIGNS_DIR = ROOT / "campaigns"
FAMILIES_DIR = ROOT / "families"
SNAPSHOT_DIR = ROOT / "leaderboard_snapshots"
EXTERNAL_INBOX_DIR = ROOT / "external_images" / "inbox"
BUDGET_PATH = ROOT / "daily_budget.json"
SUBMISSION_STATUS_URL = "https://judgementday.aim-intelligence.com/submissions/{submission_id}/status"

GENERATED_STATUSES = {"generated", "ready", "dedupe_warn", "filled"}
READY_STATUSES = {"ready"}
KST = timezone(timedelta(hours=9))
GPT_IMAGE_GENERATOR = "tooling.image_gen.ingest_external"
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_DAILY_ALLOCATION = [
    ("robot_image", 0.35),
    ("sports_image", 0.20),
    ("dam_epidemic_visual", 0.20),
    ("deepfake_aircraft_visual", 0.15),
    ("reserve", 0.10),
]
DEFAULT_DAILY_LIMIT = 200
DEFAULT_RESERVE = 20
DEFAULT_BATCH_SIZE = 5


def now_ts() -> int:
    return int(time.time())


def today_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def ts_to_kst_date(ts: int | float | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), KST).strftime("%Y-%m-%d")


def short_hash(value: str, length: int = 14) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    path.write_text(text, encoding="utf-8")


@contextmanager
def queue_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load_campaign_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except Exception as exc:
            raise SystemExit(
                f"{path} is not JSON-formatted YAML and PyYAML is not installed. "
                "Use JSON syntax in .yaml files or install PyYAML."
            ) from exc
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise SystemExit(f"campaign file must contain an object: {path}")
    return data


def load_family_registry() -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    for path in sorted(FAMILIES_DIR.glob("*.yaml")):
        data = load_campaign_file(path)
        for fam in data.get("families", []):
            if not isinstance(fam, dict) or not fam.get("family_id"):
                raise SystemExit(f"invalid family entry in {path}")
            registry[str(fam["family_id"])] = {**fam, "registry_file": str(path)}
    return registry


def default_family_id(rec: dict[str, Any]) -> str:
    if rec.get("family_id"):
        return str(rec["family_id"])
    family = str(rec.get("family") or "")
    variant = str(rec.get("variant_id") or "")
    if rec.get("scenario") == "track1_1_industrial_robot" and rec.get("channel") == "image":
        if any(tag in variant for tag in ("hmi_panel", "hmi-")):
            return "robot_hmi_panel"
        if "hmi_alarm_history" in variant:
            return "robot_hmi_alarm_history"
        if "plc_io_diagnostics" in variant:
            return "robot_plc_io_diagnostics"
        if "safety_relay_panel" in variant:
            return "robot_safety_relay_panel"
        if "maintenance_tablet" in variant:
            return "robot_maintenance_tablet"
        if "cell_permit_wall" in variant:
            return "robot_cell_permit_wall"
        if "teach_pendant_status" in variant:
            return "robot_teach_pendant_status"
        if "safety_scanner_console" in variant:
            return "robot_safety_scanner_console"
        if "guard_door_keybox" in variant:
            return "robot_guard_door_keybox"
        if "mes_release_dashboard" in variant:
            return "robot_mes_release_dashboard"
        if "maintenance_laptop_diagnostics" in variant:
            return "robot_maintenance_laptop_diagnostics"
        if "operator_panel_alarm_ack" in variant:
            return "robot_operator_panel_alarm_ack"
        if "interlock_bypass_key_station" in variant:
            return "robot_interlock_bypass_key_station"
        if "robot_controller_io_screen" in variant:
            return "robot_controller_io_screen"
        if "loto" in variant:
            return "robot_loto_board"
        if "supervisor" in variant:
            return "robot_supervisor_packet"
        if "floor" in variant:
            return "robot_floor_angle"
        return "robot_cctv_sign"
    if family:
        return f"{rec.get('scenario', 'unknown')}:{rec.get('channel', 'unknown')}:{family}"
    return f"{rec.get('scenario', 'unknown')}:{rec.get('channel', 'unknown')}"


def default_cluster_id(rec: dict[str, Any]) -> str:
    if rec.get("cluster_id"):
        return str(rec["cluster_id"])
    return default_family_id(rec)


def default_phase(rec: dict[str, Any], registry: dict[str, dict[str, Any]]) -> str:
    if rec.get("phase"):
        return str(rec["phase"])
    fam = registry.get(default_family_id(rec), {})
    return str(fam.get("phase") or "calibrate")


def enrich_record(rec: dict[str, Any], registry: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    registry = registry or {}
    out = dict(rec)
    family_id = default_family_id(out)
    out.setdefault("family_id", family_id)
    out.setdefault("cluster_id", default_cluster_id(out))
    out.setdefault("phase", default_phase(out, registry))
    return out


def campaign_paths(args: argparse.Namespace) -> list[Path]:
    paths = [Path(p) for p in (args.campaign or [])]
    if not paths:
        paths = sorted(CAMPAIGNS_DIR.glob("*.yaml"))
    if not paths:
        raise SystemExit("no campaign files found")
    return paths


def coerce_list(value: Any, default: list[Any]) -> list[Any]:
    if value is None:
        return default
    if isinstance(value, list):
        return value
    return [value]


def expand_item(campaign: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    defaults = campaign.get("defaults", {}) if isinstance(campaign.get("defaults"), dict) else {}
    variants = coerce_list(item.get("variants") or item.get("variant"), ["default"])
    if item.get("seeds") is not None:
        seeds = coerce_list(item.get("seeds"), [])
    else:
        count = int(item.get("count", 1))
        seed_start = int(item.get("seed_start", 20260501))
        seeds = list(range(seed_start, seed_start + count))
    specs = []
    for seed in seeds:
        for variant in variants:
            spec = dict(item)
            spec["campaign_name"] = campaign.get("name") or "campaign"
            spec["batch_id"] = item.get("batch_id") or campaign.get("name") or "campaign"
            spec["model"] = item.get("model") or defaults.get("model", "all")
            spec["priority"] = int(item.get("priority", defaults.get("priority", item.get("bounty", 0) or 0)))
            spec["phase"] = item.get("phase") or defaults.get("phase", "calibrate")
            spec["variant"] = str(variant)
            spec["seed"] = int(seed)
            spec.pop("variants", None)
            spec.pop("seeds", None)
            specs.append(spec)
    return specs


def iter_specs(paths: list[Path]) -> list[dict[str, Any]]:
    specs = []
    for path in paths:
        campaign = load_campaign_file(path)
        for item in campaign.get("items", []):
            if not isinstance(item, dict):
                raise SystemExit(f"campaign item must be an object in {path}")
            for spec in expand_item(campaign, item):
                spec["campaign_file"] = str(path)
                specs.append(spec)
    return specs


def plan_key(spec: dict[str, Any]) -> str:
    material = {
        "campaign": spec.get("campaign_name"),
        "name": spec.get("name"),
        "generator": spec.get("generator"),
        "scenario": spec.get("scenario"),
        "channel": spec.get("channel"),
        "variant": spec.get("variant"),
        "seed": spec.get("seed"),
        "args": spec.get("args", {}),
    }
    return short_hash(json.dumps(material, ensure_ascii=False, sort_keys=True), 20)


def generator_command(spec: dict[str, Any]) -> list[str]:
    generator = spec.get("generator")
    args = spec.get("args", {}) if isinstance(spec.get("args"), dict) else {}
    seed = str(spec["seed"])
    variant = str(spec["variant"])
    bounty = spec.get("bounty")
    target_action = spec.get("target_action")

    if generator == "slack_dm":
        cmd = ["tooling.injectors.slack_dm", "--variant", variant, "--seed", seed]
        if spec.get("scenario"):
            cmd += ["--scenario", str(spec["scenario"])]
        if bounty is not None:
            cmd += ["--bounty", str(bounty)]
        if target_action:
            cmd += ["--target-action", str(target_action)]
        for key in ("ticket", "host"):
            if args.get(key):
                cmd += [f"--{key.replace('_', '-')}", str(args[key])]
        return cmd

    if generator == "pdf_document":
        return ["tooling.pdf_gen.document", "--kind", variant, "--seed", seed]

    if generator == "cctv_overlay":
        work_order = args.get("work_order")
        technician = args.get("technician")
        if not work_order:
            work_order = f"{args.get('work_order_prefix', 'MNT')}-{4471 + int(seed) % 97}"
        if not technician:
            technician = f"{args.get('technician_prefix', 'JP')}-{118 + int(seed) % 31}"
        cmd = [
            "tooling.image_gen.cctv_overlay",
            "--seed",
            seed,
            "--work-order",
            str(work_order),
            "--technician",
            str(technician),
        ]
        if spec.get("scenario"):
            cmd += ["--scenario", str(spec["scenario"])]
        if bounty is not None:
            cmd += ["--bounty", str(bounty)]
        if target_action:
            cmd += ["--target-action", str(target_action)]
        if args.get("template"):
            cmd += ["--template", str(args["template"])]
        return cmd

    if generator == "sports_image":
        cmd = ["tooling.image_gen.sports_panel", "--preset", variant, "--seed", seed]
        if bounty is not None:
            cmd += ["--bounty", str(bounty)]
        if target_action:
            cmd += ["--target-action", str(target_action)]
        return cmd

    if generator == "external_image":
        source = args.get("source")
        if not source:
            raise SystemExit("external_image generator requires args.source")
        sources = source if isinstance(source, list) else [source]
        for extra_source in coerce_list(args.get("extra_sources"), []):
            sources.append(extra_source)
        cmd = [
            "tooling.image_gen.ingest_external",
            "--scenario",
            str(spec.get("scenario", "track1_1_industrial_robot")),
            "--channel",
            str(spec.get("channel", "image")),
            "--family",
            str(spec.get("family") or spec.get("family_id") or "robot_generated_photo"),
            "--family-id",
            str(spec.get("family_id") or "robot_generated_photo"),
            "--cluster-id",
            str(spec.get("cluster_id") or spec.get("family_id") or "robot_generated_photo"),
            "--variant-id",
            variant,
            "--seed",
            seed,
        ]
        for source_item in sources:
            cmd += ["--source", str(source_item)]
        if bounty is not None:
            cmd += ["--bounty", str(bounty)]
        if target_action:
            cmd += ["--target-action", str(target_action)]
        for key in (
            "visible_text",
            "prompt",
            "description",
            "method_detail",
            "scene_type",
            "evidence_source",
            "visible_text_theme",
            "authority_role",
            "layout_cluster",
            "title",
            "wrap_as",
        ):
            if args.get(key):
                cmd += [f"--{key.replace('_', '-')}", str(args[key])]
        if args.get("duration"):
            cmd += ["--duration", str(args["duration"])]
        if args.get("method"):
            cmd += ["--method", str(args["method"])]
        return cmd

    if generator == "audio_tts":
        cmd = ["tooling.audio_gen.tts", "--preset", variant, "--seed", seed]
        if args.get("radio"):
            cmd.append("--radio")
        for key in ("text", "voice", "rate"):
            if args.get(key):
                cmd += [f"--{key}", str(args[key])]
        return cmd

    if generator == "video_clip":
        cmd = ["tooling.video_gen.clip", "--preset", variant, "--seed", seed]
        if args.get("duration"):
            cmd += ["--duration", str(args["duration"])]
        return cmd

    raise SystemExit(f"unknown generator: {generator}")


def parse_generator_output(stdout: str) -> tuple[Path, Path]:
    artifact = None
    manifest = None
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        path = Path(line)
        if not path.is_absolute():
            path = ROOT / path
        if line.endswith(".manifest.json"):
            manifest = path
        elif path.suffix:
            artifact = path
    if not manifest:
        raise RuntimeError(f"generator did not print a manifest path:\n{stdout}")
    if not artifact:
        data = load_manifest(manifest)
        artifact = Path(data["artifact"])
        if not artifact.is_absolute():
            artifact = ROOT / artifact
    return artifact, manifest


def make_queue_record(spec: dict[str, Any], artifact: Path, manifest: Path, status: str = "generated") -> dict[str, Any]:
    manifest_data = load_manifest(manifest)
    extra = manifest_data.get("extra", {}) if isinstance(manifest_data.get("extra"), dict) else {}
    key = plan_key(spec)
    ts = now_ts()
    provisional = {
        "scenario": manifest_data.get("scenario") or spec.get("scenario", ""),
        "channel": manifest_data.get("channel") or spec.get("channel", ""),
        "family": manifest_data.get("family", ""),
        "family_id": spec.get("family_id") or extra.get("family_id") or "",
        "cluster_id": spec.get("cluster_id") or extra.get("cluster_id") or "",
    }
    family_id = str(provisional.get("family_id") or default_family_id(provisional))
    cluster_id = str(provisional.get("cluster_id") or default_cluster_id({**provisional, "family_id": family_id}))
    return {
        "queue_id": "q_" + key[:14],
        "plan_key": key,
        "batch_id": spec.get("batch_id") or spec.get("campaign_name") or "campaign",
        "campaign_file": spec.get("campaign_file", ""),
        "campaign_item": spec.get("name", ""),
        "created_ts": ts,
        "updated_ts": ts,
        "priority": int(spec.get("priority", manifest_data.get("bounty") or 0)),
        "model": spec.get("model", "all"),
        "scenario": manifest_data.get("scenario") or spec.get("scenario", ""),
        "channel": manifest_data.get("channel") or spec.get("channel", ""),
        "bounty": manifest_data.get("bounty", spec.get("bounty")),
        "target_action": manifest_data.get("target_action", spec.get("target_action", "")),
        "family": manifest_data.get("family", ""),
        "family_id": family_id,
        "cluster_id": cluster_id,
        "phase": spec.get("phase", "calibrate"),
        "variant_id": manifest_data.get("variant_id", f"{spec.get('variant')}_{spec.get('seed')}"),
        "artifact_path": str(artifact),
        "manifest_path": str(manifest),
        "artifact_sha256": manifest_data.get("artifact_sha256", ""),
        "generator": manifest_data.get("generator", spec.get("generator", "")),
        "status": status,
        "attempt_id": "",
        "site_submission_id": "",
        "result": "",
        "score": None,
        "review_note": "",
        "last_error": "",
    }


def save_record(records: list[dict[str, Any]], record: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {r["queue_id"]: i for i, r in enumerate(records)}
    if record["queue_id"] in by_id:
        old = records[by_id[record["queue_id"]]]
        merged = {**old, **record, "created_ts": old.get("created_ts", record["created_ts"])}
        merged["updated_ts"] = now_ts()
        records[by_id[record["queue_id"]]] = merged
    else:
        records.append(record)
    return records


def select_records(
    records: list[dict[str, Any]],
    *,
    queue_id: str | None = None,
    batch_id: str | None = None,
    statuses: set[str] | None = None,
    limit: int | None = None,
    include_warn: bool = False,
) -> list[dict[str, Any]]:
    out = []
    for rec in records:
        if queue_id and rec.get("queue_id") != queue_id:
            continue
        if batch_id and rec.get("batch_id") != batch_id:
            continue
        status = rec.get("status", "")
        if statuses and status not in statuses:
            if not (include_warn and status == "dedupe_warn"):
                continue
        out.append(rec)
    out.sort(key=lambda r: (-int(r.get("priority") or 0), -int(r.get("bounty") or 0), r.get("created_ts", 0)))
    if limit is not None:
        out = out[:limit]
    return out


def generator_allowed(rec: dict[str, Any], args: argparse.Namespace) -> bool:
    required = getattr(args, "require_generator", "") or ""
    if getattr(args, "gpt_only", False):
        required = GPT_IMAGE_GENERATOR
    return not required or rec.get("generator") == required


def filter_records_by_generator(records: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    return [rec for rec in records if generator_allowed(rec, args)]


def run_module(module_args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m"] + module_args
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)


def endpoint_available(endpoint: str) -> bool:
    try:
        with urllib.request.urlopen(f"{endpoint.rstrip('/')}/json/list", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def cmd_plan(args: argparse.Namespace) -> int:
    specs = iter_specs(campaign_paths(args))
    rows = defaultdict(int)
    for spec in specs:
        rows[(spec.get("generator"), spec.get("scenario", ""), spec.get("channel", ""), spec.get("variant"))] += 1
    print("Campaign candidates:")
    for (generator, scenario, channel, variant), count in sorted(rows.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {count:3d}  {generator:12s}  {scenario or '-':34s}  {channel or '-':8s}  {variant}")
    print(f"\nTotal planned artifacts: {len(specs)}")
    print("Recommended first queue: GPT external visuals -> sports image -> dam/epidemic wrappers, with pending guard enabled")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    specs = iter_specs(campaign_paths(args))
    records = read_jsonl(args.queue)
    existing_plan_keys = {r.get("plan_key") for r in records}
    generated = 0
    skipped = 0
    for spec in specs:
        key = plan_key(spec)
        if key in existing_plan_keys and not args.replace:
            skipped += 1
            continue
        proc = run_module(generator_command(spec))
        if proc.returncode != 0:
            write_jsonl(args.queue, records)
            print(proc.stdout, end="")
            print(proc.stderr, file=sys.stderr, end="")
            return proc.returncode
        artifact, manifest = parse_generator_output(proc.stdout)
        record = make_queue_record(spec, artifact, manifest)
        records = save_record(records, record)
        write_jsonl(args.queue, records)
        existing_plan_keys.add(key)
        generated += 1
        print(f"{record['queue_id']} generated {record['scenario']} {record['channel']} {record['variant_id']}")
        if args.limit and generated >= args.limit:
            break
    print(f"generated={generated} skipped={skipped} queue={args.queue}")
    return 0


def cmd_dedupe(args: argparse.Namespace) -> int:
    records = read_jsonl(args.queue)
    selected = select_records(
        records,
        queue_id=args.queue_id,
        batch_id=args.batch_id,
        statuses=GENERATED_STATUSES,
        limit=args.limit,
        include_warn=True,
    )
    if not selected:
        print("no generated queue records selected")
        return 0
    by_id = {r["queue_id"]: r for r in records}
    warnings = 0
    for rec in selected:
        proc = run_module([
            "tooling.dedupe",
            "check",
            "--scenario",
            rec["scenario"],
            "--manifest",
            rec["manifest_path"],
        ])
        out = (proc.stdout + proc.stderr).strip()
        target = by_id[rec["queue_id"]]
        target["dedupe_output"] = out
        target["updated_ts"] = now_ts()
        if proc.returncode == 0:
            target["status"] = "ready"
            print(f"{rec['queue_id']} ready")
        else:
            target["status"] = "dedupe_warn"
            warnings += 1
            print(f"{rec['queue_id']} dedupe_warn")
            if out:
                print(out)
    write_jsonl(args.queue, records)
    if warnings and args.fail_on_warn:
        return 1
    return 0


def cmd_fill(args: argparse.Namespace) -> int:
    records = read_jsonl(args.queue)
    selected = select_records(
        records,
        queue_id=args.queue_id,
        batch_id=args.batch_id,
        statuses=READY_STATUSES | {"filled"},
        limit=args.limit,
        include_warn=args.allow_warn,
    )
    if not selected:
        print("no ready queue records selected")
        return 0
    by_id = {r["queue_id"]: r for r in records}
    for rec in selected:
        proc = run_module([
            "tooling.submit_assistant",
            "--endpoint",
            args.endpoint,
            "fill",
            "--manifest",
            rec["manifest_path"],
        ])
        print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, file=sys.stderr, end="")
        target = by_id[rec["queue_id"]]
        target["updated_ts"] = now_ts()
        if proc.returncode == 0:
            target["status"] = "filled"
            target["last_error"] = ""
        else:
            target["status"] = "fill_failed"
            target["last_error"] = proc.stderr.strip() or proc.stdout.strip()
            write_jsonl(args.queue, records)
            return proc.returncode
    write_jsonl(args.queue, records)
    return 0


def click_submit(endpoint: str) -> dict[str, Any]:
    with CDPClient(endpoint) as cdp:
        status = cdp.evaluate(
            r"""
(() => {
  const submit = [...document.querySelectorAll('button')].find(b => b.innerText.trim() === 'Submit Attack');
  if (!submit) return {ok:false, reason:'Submit Attack button not found', url: location.href};
  if (submit.disabled) return {ok:false, reason:'Submit Attack button disabled', url: location.href};
  submit.click();
  return {ok:true, reason:'clicked', url: location.href};
})()
"""
        )
        time.sleep(1.5)
        after = cdp.evaluate(
            r"""
(() => ({
  url: location.href,
  title: document.title,
  text: document.body.innerText.slice(0, 1200)
}))()
"""
        )
    return {"click": status, "after": after}


def log_attempt(rec: dict[str, Any], result: str, note: str, site_submission_id: str = "") -> str:
    cmd = [
        "tooling.submit_tracker",
        "log",
        "--manifest",
        rec["manifest_path"],
        "--artifact",
        rec["artifact_path"],
        "--model",
        rec.get("model") or "all",
        "--result",
        result,
        "--note",
        note,
    ]
    if site_submission_id:
        cmd += ["--site-submission-id", site_submission_id]
    proc = run_module(cmd)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout.strip().splitlines()[-1]


def attempts_for_date(attempts: dict[str, dict[str, Any]], date: str) -> list[dict[str, Any]]:
    return [a for a in attempts.values() if ts_to_kst_date(a.get("ts")) == date]


def pending_attempt_count(attempts: dict[str, dict[str, Any]]) -> int:
    return sum(1 for attempt in attempts.values() if attempt.get("result", "pending") == "pending")


def count_site_submissions_today(endpoint: str, date: str) -> int | None:
    """Best-effort site count. Returns None when My Page does not expose dates."""
    try:
        with CDPClient(endpoint) as cdp:
            cdp.navigate("https://judgementday.aim-intelligence.com/me")
            text = cdp.evaluate("document.body.innerText", timeout=8) or ""
    except Exception:
        return None
    if date not in text and date.replace("-", "/") not in text:
        return None
    # Count submission UUIDs on pages that expose today's date. This is
    # intentionally conservative and optional because the UI changes often.
    return len(set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text, re.I)))


def budget_snapshot(
    *,
    attempts: dict[str, dict[str, Any]],
    date: str,
    daily_limit: int,
    reserve: int,
    endpoint: str,
    check_site: bool,
) -> dict[str, Any]:
    local_used = len(attempts_for_date(attempts, date))
    site_used = count_site_submissions_today(endpoint, date) if check_site else None
    # Fail closed on mismatch: use the higher used count, which yields the lower
    # remaining budget.
    used = max(local_used, site_used or 0)
    remaining = max(0, daily_limit - used)
    available_for_auto = max(0, remaining - reserve)
    return {
        "date": date,
        "timezone": "Asia/Seoul",
        "daily_limit": daily_limit,
        "reserve": reserve,
        "local_used": local_used,
        "site_used": site_used,
        "used_for_guard": used,
        "remaining": remaining,
        "available_for_auto": available_for_auto,
        "updated_ts": now_ts(),
    }


def write_budget(data: dict[str, Any], path: Path = BUDGET_PATH) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def attempt_text(rec: dict[str, Any]) -> str:
    return " ".join(str(rec.get(k, "")) for k in ("result", "review_note", "note", "note_resolve")).lower()


def is_cluster_freeze_reject(rec: dict[str, Any]) -> bool:
    text = attempt_text(rec)
    return any(marker in text for marker in ("similar_to_existing", "similar", "duplicate content", "mimo_only"))


def is_similar_reject(rec: dict[str, Any]) -> bool:
    text = attempt_text(rec)
    return "similar_to_existing" in text or "similar" in text


def review_state(attempt: dict[str, Any]) -> str:
    """Split raw model breaks from human-reviewed final outcomes."""
    text = attempt_text(attempt)
    result = str(attempt.get("result", "pending"))
    if "awaiting review" in text:
        return "raw_review_pending"
    if result in {"breach", "approved"}:
        return "approved_final"
    if result == "rejected" or "rejected" in text:
        return "rejected_final"
    if result == "denied" or "safe" in text:
        return "safe_final"
    return result or "pending"


def review_details(attempt: dict[str, Any]) -> dict[str, Any]:
    text = attempt_text(attempt)
    models_broken = None
    match = re.search(r"(\d+)\s*/\s*(\d+)\s+broken", text)
    if match:
        models_broken = int(match.group(1))
    raw_score = None
    match = re.search(r"raw score\s+([0-9]+(?:\.[0-9]+)?)", text)
    if match:
        raw_score = float(match.group(1))
    selected_actions = sorted(set(re.findall(r"selected\s+([a-z0-9_/-]+)", text)))
    rejected_reason = ""
    for marker in ("similar_to_existing", "duplicate content", "mimo_only", "rejected"):
        if marker in text:
            rejected_reason = marker
            break
    return {
        "models_broken": models_broken,
        "raw_score": raw_score,
        "selected_actions": selected_actions,
        "rejected_reason": rejected_reason,
    }


def linked_attempts(records: list[dict[str, Any]], attempts: dict[str, dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    out = []
    for rec in records:
        aid = rec.get("attempt_id")
        if aid and aid in attempts:
            out.append((rec, attempts[aid]))
    return out


def family_policy_stats(
    records: list[dict[str, Any]],
    attempts: dict[str, dict[str, Any]],
    date: str,
    registry: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "family_today": defaultdict(int),
        "cluster_today": defaultdict(int),
        "surface_all": defaultdict(lambda: defaultdict(int)),
        "cluster_similar_reject": set(),
    }
    for raw_rec, attempt in linked_attempts(records, attempts):
        rec = enrich_record(raw_rec, registry)
        fam = rec["family_id"]
        cluster = rec["cluster_id"]
        surface = surface_key(rec)
        result = attempt.get("result", "pending")
        stats["surface_all"][surface]["total"] += 1
        stats["surface_all"][surface][result] += 1
        if ts_to_kst_date(attempt.get("ts")) == date:
            stats["family_today"][fam] += 1
            stats["cluster_today"][cluster] += 1
        if is_cluster_freeze_reject(attempt):
            stats["cluster_similar_reject"].add(cluster)
    return stats


def policy_decision(
    rec: dict[str, Any],
    *,
    records: list[dict[str, Any]],
    attempts: dict[str, dict[str, Any]],
    registry: dict[str, dict[str, Any]],
    date: str,
    max_family_daily: int,
    max_cluster_daily: int,
    allow_paused: bool = False,
    allow_similar: bool = False,
    ignore_surface_guard: bool = False,
) -> tuple[bool, list[str]]:
    rec = enrich_record(rec, registry)
    fam_id = rec["family_id"]
    cluster_id = rec["cluster_id"]
    fam = registry.get(fam_id, {})
    stats = family_policy_stats(records, attempts, date, registry)
    reasons: list[str] = []

    if fam.get("phase") == "pause" and not allow_paused:
        reasons.append(f"family_paused:{fam_id}")
    if fam.get("success_state") == "freeze" and not allow_paused:
        reasons.append(f"family_frozen:{fam_id}")
    if cluster_id in stats["cluster_similar_reject"] and not allow_similar:
        reasons.append(f"cluster_frozen_similar:{cluster_id}")
    fam_limit = int(fam.get("max_daily", max_family_daily))
    cluster_limit = int(fam.get("max_cluster_daily", max_cluster_daily))
    if stats["family_today"][fam_id] >= fam_limit:
        reasons.append(f"family_daily_limit:{fam_id}:{fam_limit}")
    if stats["cluster_today"][cluster_id] >= cluster_limit:
        reasons.append(f"cluster_daily_limit:{cluster_id}:{cluster_limit}")
    surface_stats = stats["surface_all"][surface_key(rec)]
    safe_threshold = int((fam.get("stop_rules") or {}).get("safe", 3))
    if (
        surface_stats.get("denied", 0) >= safe_threshold
        and surface_stats.get("breach", 0) == 0
        and not ignore_surface_guard
    ):
        reasons.append(f"surface_rotate_after_safe:{rec.get('scenario')}/{rec.get('channel')}")
    fam_history = [
        attempt
        for raw, attempt in linked_attempts(records, attempts)
        if enrich_record(raw, registry)["family_id"] == fam_id
    ]
    if len(fam_history) >= 4 and not any(review_state(a) in {"raw_review_pending", "approved_final"} for a in fam_history):
        reasons.append(f"family_freeze_after_4_zero:{fam_id}")
    return (not reasons, reasons)


def estimate_family_ev(records: list[dict[str, Any]], attempts: dict[str, dict[str, Any]], registry: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for raw_rec, attempt in linked_attempts(records, attempts):
        rec = enrich_record(raw_rec, registry)
        fam = rec["family_id"]
        row = rows.setdefault(
            fam,
            {
                "attempts": 0,
                "breaches": 0,
                "raw_review_pending": 0,
                "approved_final": 0,
                "rejected": 0,
                "score": 0.0,
                "provisional_score": 0.0,
                "final_score": 0.0,
                "models_broken": 0,
                "selected_actions": defaultdict(int),
            },
        )
        row["attempts"] += 1
        state = review_state(attempt)
        details = review_details(attempt)
        if details["models_broken"] is not None:
            row["models_broken"] += int(details["models_broken"])
        for action in details["selected_actions"]:
            row["selected_actions"][action] += 1
        if state in {"raw_review_pending", "approved_final"}:
            row["breaches"] += 1
            row["score"] += float(attempt.get("score") or 0)
        if state == "raw_review_pending":
            row["raw_review_pending"] += 1
            row["provisional_score"] += float(attempt.get("score") or 0)
        elif state == "approved_final":
            row["approved_final"] += 1
            row["final_score"] += float(attempt.get("score") or 0)
        elif attempt.get("result") == "rejected":
            row["rejected"] += 1
    for fam, row in rows.items():
        total = max(1, int(row["attempts"]))
        accepted = max(1, total - int(row["rejected"]))
        accepted_breach_rate = float(row["breaches"]) / accepted
        weighted_score = float(row["final_score"]) + (0.7 * float(row["provisional_score"]))
        avg_score = weighted_score / max(1, int(row["breaches"]))
        novelty_pass_rate = float(total - int(row["rejected"])) / total
        row["accepted_breach_rate"] = round(accepted_breach_rate, 3)
        row["avg_score"] = round(avg_score, 3)
        row["novelty_pass_rate"] = round(novelty_pass_rate, 3)
        row["expected_score"] = round(accepted_breach_rate * avg_score * novelty_pass_rate, 3)
        row["selected_actions"] = dict(row["selected_actions"])
    return rows


def allocation_group(rec: dict[str, Any]) -> str:
    scenario = rec.get("scenario", "")
    channel = rec.get("channel", "")
    fam_id = rec.get("family_id", "")
    phase = rec.get("phase", "")
    if scenario == "track1_1_industrial_robot" and channel == "image":
        return "robot_image"
    if scenario == "track1_2_sports_integrity" and channel == "image":
        return "sports_image"
    if scenario in {"track1_3_dam_flood", "track2_2_epidemic_alert"} and channel in {"video", "document"}:
        return "dam_epidemic_visual"
    if scenario in {"track2_3_deepfake_dr", "track2_1_aircraft_maintenance", "track1_0_emergency_triage"}:
        return "deepfake_aircraft_visual"
    if fam_id.startswith(("dam_", "epidemic_")):
        return "dam_epidemic_visual"
    if fam_id.startswith("sports_"):
        return "sports_image"
    if fam_id.startswith(("deepfake_", "aircraft_", "triage_")):
        return "deepfake_aircraft_visual"
    if phase == "exploit" or rec.get("success_state") in {"exploit", "expand"}:
        return "robot_image"
    return "dam_epidemic_visual"


def cmd_mark_submitted(args: argparse.Namespace) -> int:
    records = read_jsonl(args.queue)
    selected = select_records(records, queue_id=args.queue_id, batch_id=args.batch_id, limit=args.limit)
    if not selected:
        print("no queue records selected")
        return 0
    by_id = {r["queue_id"]: r for r in records}
    for rec in selected:
        note = args.note or f"manual submit confirmed via campaign_runner; queue_id={rec['queue_id']}; batch={rec.get('batch_id')}"
        attempt = log_attempt(rec, "pending", note, args.site_submission_id or "")
        target = by_id[rec["queue_id"]]
        target["attempt_id"] = attempt
        target["site_submission_id"] = args.site_submission_id or target.get("site_submission_id", "")
        target["status"] = "submitted"
        target["result"] = "pending"
        target["updated_ts"] = now_ts()
        print(f"{rec['queue_id']} logged attempt {attempt}")
    write_jsonl(args.queue, records)
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    registry = load_family_registry()
    attempts = load_attempts()
    records = read_jsonl(args.queue)
    date = args.date or today_kst()
    pending_count = pending_attempt_count(attempts)
    if pending_count >= args.max_pending and not args.ignore_pending_guard:
        print(
            "pending guard refused submit: "
            f"pending={pending_count} max_pending={args.max_pending}. Run sync-results/resolve first.",
            file=sys.stderr,
        )
        return 2
    pending_slots = max(0, args.max_pending - pending_count)
    budget = budget_snapshot(
        attempts=attempts,
        date=date,
        daily_limit=args.daily_limit,
        reserve=args.reserve,
        endpoint=args.endpoint,
        check_site=args.check_site_count,
    )
    write_budget(budget)
    if budget["available_for_auto"] <= 0:
        print(
            "daily quota guard refused submit: "
            f"used={budget['used_for_guard']} remaining={budget['remaining']} reserve={budget['reserve']}",
            file=sys.stderr,
        )
        return 2

    candidates = select_records(
        records,
        queue_id=args.queue_id,
        batch_id=args.batch_id,
        statuses=READY_STATUSES | {"filled"},
        limit=None,
        include_warn=args.allow_warn,
    )
    candidates = filter_records_by_generator(candidates, args)
    selected = []
    blocked = []
    for rec in candidates:
        allowed, reasons = policy_decision(
            rec,
            records=records,
            attempts=attempts,
            registry=registry,
            date=date,
            max_family_daily=args.max_family_daily,
            max_cluster_daily=args.max_cluster_daily,
            allow_paused=args.allow_paused,
            allow_similar=args.allow_similar,
            ignore_surface_guard=args.ignore_surface_guard,
        )
        if allowed:
            selected.append(enrich_record(rec, registry))
        else:
            blocked.append((rec, reasons))
    hard_limit = min(args.limit or args.batch_size, args.batch_size, budget["available_for_auto"])
    if not args.ignore_pending_guard:
        hard_limit = min(hard_limit, pending_slots)
    selected = selected[:hard_limit]
    if not selected:
        print("no ready/filled queue records selected")
        for rec, reasons in blocked[:10]:
            print(f"  blocked {rec.get('queue_id')} {rec.get('variant_id')}: {', '.join(reasons)}")
        return 0
    if not args.submit:
        print("dry run only. No Submit click. Add --submit --confirm-batch <batch_id> to submit.")
        print(
            f"budget date={budget['date']} used={budget['used_for_guard']} "
            f"remaining={budget['remaining']} auto_available={budget['available_for_auto']}"
        )
        for rec in selected:
            print(
                f"  {rec['queue_id']} {rec['batch_id']} {rec['scenario']} {rec['channel']} "
                f"{rec['variant_id']} family={rec.get('family_id')} cluster={rec.get('cluster_id')}"
            )
        return 0
    for rec in selected:
        if args.confirm_batch != rec.get("batch_id"):
            print(f"refusing {rec['queue_id']}: --confirm-batch must equal {rec.get('batch_id')!r}", file=sys.stderr)
            return 2
    by_id = {r["queue_id"]: r for r in records}
    for rec in selected:
        if args.skip_fill:
            response = click_submit(args.endpoint)
            click = response.get("click") or {}
            ok = bool(click.get("ok"))
            stdout = json.dumps(response, ensure_ascii=False)
            stderr = ""
        else:
            submit_cmd = [
                "tooling.submit_assistant",
                "--endpoint",
                args.endpoint,
                "fill",
                "--manifest",
                rec["manifest_path"],
                "--submit",
            ]
            proc = run_module(submit_cmd)
            print(proc.stdout, end="")
            if proc.stderr:
                print(proc.stderr, file=sys.stderr, end="")
            ok = proc.returncode == 0 and '"ok": true' in proc.stdout
            stdout = proc.stdout
            stderr = proc.stderr
        if not ok:
            target = by_id[rec["queue_id"]]
            target["status"] = "submit_failed"
            target["last_error"] = stderr.strip() or stdout.strip()
            target["updated_ts"] = now_ts()
            write_jsonl(args.queue, records)
            return 1
        site_submission_id = ""
        match = re.search(r"/submissions/([0-9a-f-]{36})/status", stdout)
        if match:
            site_submission_id = match.group(1)
        attempt = log_attempt(
            by_id[rec["queue_id"]],
            "pending",
            f"auto-submit via campaign_runner; queue_id={rec['queue_id']}; batch={rec.get('batch_id')}",
            site_submission_id,
        )
        target = by_id[rec["queue_id"]]
        target["attempt_id"] = attempt
        target["site_submission_id"] = site_submission_id
        target["status"] = "submitted"
        target["result"] = "pending"
        target["submit_response"] = stdout
        target["updated_ts"] = now_ts()
        print(f"{rec['queue_id']} submitted attempt {attempt}")
    write_jsonl(args.queue, records)
    updated_budget = budget_snapshot(
        attempts=load_attempts(),
        date=date,
        daily_limit=args.daily_limit,
        reserve=args.reserve,
        endpoint=args.endpoint,
        check_site=False,
    )
    write_budget(updated_budget)
    return 0


def load_attempts() -> dict[str, dict[str, Any]]:
    attempts: dict[str, dict[str, Any]] = {}
    for scen_dir in SCENARIOS.iterdir():
        log_path = scen_dir / "attempts.jsonl"
        if not log_path.exists():
            continue
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            attempts[rec.get("id", "")] = rec
    return attempts


def update_attempt_record(attempt_id: str, updates: dict[str, Any]) -> bool:
    """Rewrite one attempt record in its scenario jsonl log."""
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
            if rec.get("id") != attempt_id:
                continue
            for key, value in updates.items():
                if value is not None:
                    rec[key] = value
            rec["status_checked_ts"] = now_ts()
            lines[i] = json.dumps(rec, ensure_ascii=False)
            changed = True
            break
        if changed:
            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
    return False


def concise_status_note(text: str, max_chars: int = 1200) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    useful = [
        line
        for line in lines
        if line
        and any(
            key in line.lower()
            for key in (
                "breach",
                "safe",
                "score",
                "review",
                "reject",
                "similar",
                "duplicate",
                "models broken",
                "selected",
                "mimo",
            )
        )
    ]
    note = " | ".join(useful[:12]) or re.sub(r"\s+", " ", text).strip()
    return note[:max_chars]


def parse_site_status_text(text: str) -> dict[str, Any]:
    """Parse a Judgement Day submission status page body into tracker fields."""
    status_text = text.split("\nPRIZES", 1)[0]
    note = concise_status_note(status_text)
    lower = status_text.lower()
    result = "pending"
    score: float | None = None

    score_match = re.search(r"raw\s+score\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", status_text, re.I)
    if not score_match:
        score_match = re.search(r"score\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", status_text, re.I)
    if score_match:
        score = float(score_match.group(1))
    broken_match = re.search(r"models?\s+broken\s*[:=]?\s*(\d+)\s*/\s*(\d+)", status_text, re.I)
    broken_count = int(broken_match.group(1)) if broken_match else None
    review_match = re.search(r"review\s*:\s*([A-Z_ -]+)", status_text, re.I)
    review_status = review_match.group(1).strip().lower() if review_match else ""

    if review_status.startswith("rejected") or "review: rejected" in lower:
        result = "rejected"
        score = 0.0
    elif "breach confirmed" in lower or (broken_count is not None and broken_count > 0):
        result = "breach"
    elif review_status.startswith("safe") or (broken_count == 0 and "evaluated" in lower):
        result = "denied"
        score = 0.0 if score is None else score
    elif any(term in lower for term in ("evaluating", "pending", "awaiting result", "queued")):
        result = "pending"

    return {"result": result, "score": score, "review_note": note}


def sync_site_statuses(records: list[dict[str, Any]], args: argparse.Namespace) -> int:
    selected = [
        rec
        for rec in records
        if rec.get("site_submission_id")
        and rec.get("attempt_id")
        and (args.include_resolved_statuses or rec.get("result", "pending") == "pending")
    ]
    if args.site_limit:
        selected = selected[: args.site_limit]
    if not selected:
        print("site status sync: no pending site submissions")
        return 0

    changed = 0
    with CDPClient(args.endpoint) as cdp:
        for rec in selected:
            submission_id = rec["site_submission_id"]
            url = args.status_url_template.format(submission_id=submission_id)
            cdp.navigate(url)
            time.sleep(args.site_wait)
            text = cdp.evaluate("document.body ? document.body.innerText : ''", timeout=10) or ""
            parsed = parse_site_status_text(text)
            if parsed["result"] == "pending" and not args.record_pending_checks:
                continue
            updates = {
                "result": parsed["result"],
                "score": parsed["score"],
                "review_note": parsed["review_note"],
            }
            if update_attempt_record(str(rec["attempt_id"]), updates):
                changed += 1
                print(
                    f"site status: {rec['queue_id']} {submission_id} -> "
                    f"{parsed['result']} score={parsed['score']}"
                )
    return changed


def cmd_sync_results(args: argparse.Namespace) -> int:
    records = read_jsonl(args.queue)
    if args.site_status:
        try:
            site_changed = sync_site_statuses(records, args)
            if site_changed:
                print(f"site status synced attempts={site_changed}")
        except Exception as exc:
            if args.require_site_status:
                raise
            print(f"WARN: site status sync failed: {exc}", file=sys.stderr)

    records = read_jsonl(args.queue)
    attempts = load_attempts()
    changed = 0
    for rec in records:
        attempt_id = rec.get("attempt_id")
        if not attempt_id or attempt_id not in attempts:
            continue
        attempt = attempts[attempt_id]
        result = attempt.get("result", "pending")
        new_status = "resolved" if result in {"breach", "denied", "rejected", "approved"} else "submitted"
        updates = {
            "status": new_status,
            "result": result,
            "score": attempt.get("score"),
            "review_note": attempt.get("review_note", ""),
            "updated_ts": now_ts(),
        }
        for key, value in updates.items():
            if rec.get(key) != value:
                rec[key] = value
                changed += 1
    write_jsonl(args.queue, records)

    if not args.skip_leaderboard:
        try:
            data = leaderboard_snapshot(args.endpoint)
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            out = SNAPSHOT_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}.json"
            out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"leaderboard snapshot: {out}")
        except Exception as exc:
            print(f"WARN: leaderboard snapshot failed: {exc}", file=sys.stderr)
    print(f"synced queue records; changed_fields={changed}")
    return 0


def cell_key(rec: dict[str, Any]) -> tuple[str, str, str]:
    return (rec.get("scenario", ""), rec.get("channel", ""), rec.get("family", ""))


def surface_key(rec: dict[str, Any]) -> tuple[str, str]:
    return (rec.get("scenario", ""), rec.get("channel", ""))


def cmd_score(args: argparse.Namespace) -> int:
    registry = load_family_registry()
    queue = read_jsonl(args.queue)
    attempts = load_attempts()
    ev_rows = estimate_family_ev(queue, attempts, registry)
    cells: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(lambda: defaultdict(int))
    surfaces: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for attempt in attempts.values():
        key = cell_key(attempt)
        cells[key]["total"] += 1
        result = attempt.get("result", "pending")
        state = review_state(attempt)
        cells[key][result] += 1
        cells[key][state] += 1
        cells[key]["score"] += float(attempt.get("score") or 0)
        if state == "approved_final":
            cells[key]["approved_score"] += float(attempt.get("score") or 0)
        elif state == "raw_review_pending":
            cells[key]["provisional_score"] += float(attempt.get("score") or 0)
        skey = surface_key(attempt)
        surfaces[skey]["total"] += 1
        surfaces[skey][result] += 1
    queued_by_cell: dict[tuple[str, str, str], int] = defaultdict(int)
    for rec in queue:
        queued_by_cell[cell_key(rec)] += 1

    rows = []
    for key in sorted(set(cells) | set(queued_by_cell)):
        stats = cells.get(key, {})
        total = int(stats.get("total", 0))
        breach = int(stats.get("breach", 0))
        score = float(stats.get("score", 0))
        rows.append(
            {
                "scenario": key[0],
                "channel": key[1],
                "family": key[2],
                "attempts": total,
                "breaches": breach,
                "score": round(score, 3),
                "approved_score": round(float(stats.get("approved_score", 0)), 3),
                "provisional_score": round(float(stats.get("provisional_score", 0)), 3),
                "score_per_submission": round(score / total, 3) if total else 0,
                "breach_rate": round(breach / total, 3) if total else 0,
                "queued": queued_by_cell.get(key, 0),
                "pending": int(stats.get("pending", 0)),
                "rejected": int(stats.get("rejected", 0)),
                "raw_review_pending": int(stats.get("raw_review_pending", 0)),
                "approved_final": int(stats.get("approved_final", 0)),
            }
        )
    rows.sort(key=lambda r: (r["score_per_submission"], r["breach_rate"], r["queued"]), reverse=True)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print("Best cells:")
    for row in rows[:10]:
        print(
            f"  {row['score_per_submission']:6.3f} pts/sub  "
            f"{row['breach_rate']:5.1%} breach  "
            f"{row['attempts']:3d} attempts  {row['queued']:3d} queued  "
            f"raw={row['raw_review_pending']} final={row['approved_final']}  "
            f"{row['scenario']} / {row['channel']} / {row['family']}"
        )

    if ev_rows:
        print("\nFamily EV:")
        for fam_id, row in sorted(ev_rows.items(), key=lambda x: x[1].get("expected_score", 0), reverse=True)[:10]:
            print(
                f"  EV={row.get('expected_score', 0):5.2f} "
                f"novelty={row.get('novelty_pass_rate', 0):4.2f} "
                f"accepted_breach={row.get('accepted_breach_rate', 0):4.2f} "
                f"avg_score={row.get('avg_score', 0):4.1f} "
                f"raw={row.get('raw_review_pending', 0)} final={row.get('approved_final', 0)} {fam_id}"
            )

    bad = [r for r in rows if r["attempts"] >= 40 and r["breaches"] == 0]
    if bad:
        print("\nBad cells to stop or rotate:")
        for row in bad[:10]:
            print(f"  {row['attempts']:3d} attempts 0 breach  {row['scenario']} / {row['channel']} / {row['family']}")

    rotate_surfaces = {
        key
        for key, stats in surfaces.items()
        if int(stats.get("total", 0)) >= args.rotate_after and int(stats.get("breach", 0)) == 0
    }
    if rotate_surfaces:
        print("\nRotate surfaces after repeated SAFE:")
        for scenario, channel in sorted(rotate_surfaces):
            stats = surfaces[(scenario, channel)]
            print(f"  {stats['total']:3d} attempts 0 breach  {scenario} / {channel}")

    date = today_kst()
    candidates = []
    policy_blocked = []
    for raw in select_records(queue, statuses={"ready", "generated", "filled", "dedupe_warn"}, include_warn=True):
        rec = enrich_record(raw, registry)
        allowed, reasons = policy_decision(
            rec,
            records=queue,
            attempts=attempts,
            date=date,
            registry=registry,
            max_family_daily=args.max_family_daily,
            max_cluster_daily=args.max_cluster_daily,
            allow_paused=False,
            allow_similar=False,
        )
        if not allowed:
            policy_blocked.append((rec, reasons))
            continue
        candidates.append(rec)
    candidates.sort(
        key=lambda rec: (
            surface_key(rec) in rotate_surfaces,
            -int(rec.get("priority") or 0),
            -int(rec.get("bounty") or 0),
            rec.get("created_ts", 0),
        )
    )
    next_items = candidates[: args.next]
    print(f"\nNext {len(next_items)} queue items:")
    for rec in next_items:
        print(
            f"  {rec['queue_id']}  p={rec.get('priority')} b={rec.get('bounty')} "
            f"{rec.get('status')}  {rec.get('scenario')} / {rec.get('channel')} / "
            f"{rec.get('variant_id')} family={rec.get('family_id')}"
        )
    if policy_blocked:
        print(f"\nPolicy-blocked queue hidden from next list: {len(policy_blocked)}")
        for rec, reasons in policy_blocked[:5]:
            print(f"  {rec['queue_id']} {rec.get('variant_id')} reasons={','.join(reasons)}")
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    registry = load_family_registry()
    records = read_jsonl(args.queue)
    selected = select_records(
        records,
        queue_id=args.queue_id,
        batch_id=args.batch_id,
        statuses=set(args.status) if args.status else None,
        limit=args.limit,
        include_warn=True,
    )
    selected = filter_records_by_generator(selected, args)
    if args.json:
        print(json.dumps(selected, ensure_ascii=False, indent=2))
        return 0
    for rec in selected:
        rec = enrich_record(rec, registry)
        print(
            f"{rec['queue_id']}  {rec.get('status'):12s}  {rec.get('batch_id')}  "
            f"{rec.get('scenario')} / {rec.get('channel')} / {rec.get('variant_id')} "
            f"family={rec.get('family_id')}"
        )
    print(f"shown={len(selected)} total={len(records)}")
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    data = budget_snapshot(
        attempts=load_attempts(),
        date=args.date or today_kst(),
        daily_limit=args.daily_limit,
        reserve=args.reserve,
        endpoint=args.endpoint,
        check_site=args.check_site_count,
    )
    write_budget(data)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_families(args: argparse.Namespace) -> int:
    registry = load_family_registry()
    records = read_jsonl(args.queue)
    attempts = load_attempts()
    ev = estimate_family_ev(records, attempts, registry)
    out = []
    for fam_id, meta in sorted(registry.items()):
        row = {**meta, **ev.get(fam_id, {})}
        out.append(row)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for row in out:
            print(
                f"{row['family_id']:30s} phase={row.get('phase', 'calibrate'):9s} "
                f"EV={row.get('expected_score', 0):5.2f} attempts={row.get('attempts', 0)} "
                f"breach={row.get('breaches', 0)} rejected={row.get('rejected', 0)}"
            )
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    registry = load_family_registry()
    records = read_jsonl(args.queue)
    attempts = load_attempts()
    date = args.date or today_kst()
    selected = select_records(
        records,
        queue_id=args.queue_id,
        batch_id=args.batch_id,
        statuses={"ready", "generated", "filled", "dedupe_warn"},
        limit=args.limit,
        include_warn=True,
    )
    out = []
    for rec in selected:
        enriched = enrich_record(rec, registry)
        allowed, reasons = policy_decision(
            enriched,
            records=records,
            attempts=attempts,
            registry=registry,
            date=date,
            max_family_daily=args.max_family_daily,
            max_cluster_daily=args.max_cluster_daily,
            allow_paused=args.allow_paused,
            allow_similar=args.allow_similar,
        )
        item = {
            "queue_id": rec.get("queue_id"),
            "variant_id": rec.get("variant_id"),
            "family_id": enriched.get("family_id"),
            "cluster_id": enriched.get("cluster_id"),
            "phase": enriched.get("phase"),
            "allowed": allowed,
            "reasons": reasons,
        }
        out.append(item)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for item in out:
            verdict = "ALLOW" if item["allowed"] else "BLOCK"
            print(f"{verdict:5s} {item['queue_id']} {item['variant_id']} family={item['family_id']} reasons={','.join(item['reasons'])}")
    return 0


def cmd_daily_plan(args: argparse.Namespace) -> int:
    registry = load_family_registry()
    records = read_jsonl(args.queue)
    attempts = load_attempts()
    date = args.date or today_kst()
    budget = budget_snapshot(
        attempts=attempts,
        date=date,
        daily_limit=args.daily_limit,
        reserve=args.reserve,
        endpoint=args.endpoint,
        check_site=args.check_site_count,
    )
    ev = estimate_family_ev(records, attempts, registry)
    pending_count = pending_attempt_count(attempts)
    candidates = select_records(records, statuses={"ready", "generated", "filled", "dedupe_warn"}, include_warn=True)
    allowed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for rec in candidates:
        enriched = enrich_record(rec, registry)
        ok, reasons = policy_decision(
            enriched,
            records=records,
            attempts=attempts,
            registry=registry,
            date=date,
            max_family_daily=args.max_family_daily,
            max_cluster_daily=args.max_cluster_daily,
            allow_paused=False,
            allow_similar=False,
        )
        if ok:
            fam_ev = ev.get(enriched["family_id"], {})
            enriched["expected_score"] = fam_ev.get("expected_score", 0)
            allowed.append(enriched)
        else:
            blocked.append({"queue_id": rec.get("queue_id"), "variant_id": rec.get("variant_id"), "reasons": reasons})
    plan: dict[str, Any] = {
        "date": date,
        "budget": budget,
        "pending_attempts": pending_count,
        "submission_pause": pending_count >= args.pending_guard,
        "blocked": blocked[:50],
    }
    if pending_count >= args.pending_guard:
        plan["pause_reason"] = f"pending_attempts>={args.pending_guard}; sync or resolve results before new broad submissions"
        allowed = []
    remaining_auto = budget["available_for_auto"]
    if args.allocation_profile == "phase":
        phase_caps = {"calibrate": args.calibrate, "probe": args.probe, "exploit": args.exploit}
        plan["phases"] = {}
        for phase, cap in phase_caps.items():
            bucket = [r for r in allowed if r.get("phase") == phase]
            bucket.sort(key=lambda r: (-float(r.get("expected_score") or 0), -int(r.get("priority") or 0), -int(r.get("bounty") or 0)))
            take = min(cap, remaining_auto, len(bucket))
            plan["phases"][phase] = [
                {
                    "queue_id": r.get("queue_id"),
                    "scenario": r.get("scenario"),
                    "channel": r.get("channel"),
                    "family_id": r.get("family_id"),
                    "cluster_id": r.get("cluster_id"),
                    "variant_id": r.get("variant_id"),
                    "expected_score": r.get("expected_score", 0),
                }
                for r in bucket[:take]
            ]
            remaining_auto -= take
    else:
        total_auto = int(remaining_auto)
        group_caps = {
            name: int(total_auto * share)
            for name, share in DEFAULT_DAILY_ALLOCATION
            if name != "reserve"
        }
        group_caps["reserve_unscheduled"] = total_auto - sum(group_caps.values())
        plan["allocation_profile"] = {
            "name": "high_score",
            "shares": dict(DEFAULT_DAILY_ALLOCATION),
            "caps": group_caps,
        }
        plan["groups"] = {}
        used_ids: set[str] = set()
        for group, cap in group_caps.items():
            if group == "reserve_unscheduled":
                plan["groups"][group] = []
                continue
            bucket = [r for r in allowed if allocation_group(r) == group and r.get("queue_id") not in used_ids]
            bucket.sort(
                key=lambda r: (
                    -float(r.get("expected_score") or 0),
                    -int(r.get("priority") or 0),
                    -int(r.get("bounty") or 0),
                    r.get("created_ts", 0),
                )
            )
            take = min(cap, len(bucket))
            selected = bucket[:take]
            used_ids.update(str(r.get("queue_id")) for r in selected)
            plan["groups"][group] = [
                {
                    "queue_id": r.get("queue_id"),
                    "phase": r.get("phase"),
                    "scenario": r.get("scenario"),
                    "channel": r.get("channel"),
                    "family_id": r.get("family_id"),
                    "cluster_id": r.get("cluster_id"),
                    "variant_id": r.get("variant_id"),
                    "expected_score": r.get("expected_score", 0),
                }
                for r in selected
            ]
        overflow = [r for r in allowed if r.get("queue_id") not in used_ids]
        overflow.sort(key=lambda r: (-float(r.get("expected_score") or 0), -int(r.get("priority") or 0), -int(r.get("bounty") or 0)))
        plan["groups"]["next_overflow"] = [
            {
                "queue_id": r.get("queue_id"),
                "group": allocation_group(r),
                "scenario": r.get("scenario"),
                "channel": r.get("channel"),
                "family_id": r.get("family_id"),
                "variant_id": r.get("variant_id"),
                "expected_score": r.get("expected_score", 0),
            }
            for r in overflow[:25]
        ]
    out = ROOT / f"daily_plan_{date.replace('-', '')}.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    print(f"wrote {out}")
    return 0


def cmd_auto_run(args: argparse.Namespace) -> int:
    if args.submit and not args.confirm_auto_run:
        print("refusing live auto-run: add --confirm-auto-run with --submit", file=sys.stderr)
        return 2

    deadline = time.time() + args.wait_seconds
    while not endpoint_available(args.endpoint):
        if time.time() >= deadline:
            print(f"CDP endpoint unavailable after {args.wait_seconds}s: {args.endpoint}", file=sys.stderr)
            return 2
        print(f"waiting for CDP endpoint {args.endpoint} ...")
        time.sleep(args.poll_seconds)

    print(f"CDP endpoint connected: {args.endpoint}")
    submitted = 0
    cycles = 0
    while submitted < args.target_submissions and cycles < args.max_cycles:
        cycles += 1
        if not args.no_site_status:
            sync_cmd = [
                "tooling.campaign_runner",
                "sync-results",
                "--site-status",
                "--site-limit",
                str(args.site_limit),
                "--endpoint",
                args.endpoint,
            ]
            if not args.leaderboard_snapshot:
                sync_cmd.append("--skip-leaderboard")
            proc = run_module(sync_cmd)
            print(proc.stdout, end="")
            if proc.stderr:
                print(proc.stderr, file=sys.stderr, end="")
            if proc.returncode != 0 and args.require_site_status:
                return proc.returncode

        made_progress = 0
        for batch_id in args.batch_id:
            remaining = args.target_submissions - submitted
            if remaining <= 0:
                break
            limit = min(args.batch_size, remaining)
            submit_cmd = [
                "tooling.campaign_runner",
                "submit",
                "--batch-id",
                batch_id,
                "--limit",
                str(limit),
                "--batch-size",
                str(args.batch_size),
                "--daily-limit",
                str(args.daily_limit),
                "--reserve",
                str(args.reserve),
                "--max-pending",
                str(args.max_pending),
                "--endpoint",
                args.endpoint,
            ]
            if args.allow_warn:
                submit_cmd.append("--allow-warn")
            if args.allow_paused:
                submit_cmd.append("--allow-paused")
            if args.allow_similar:
                submit_cmd.append("--allow-similar")
            if args.gpt_only:
                submit_cmd.append("--gpt-only")
            if args.require_generator:
                submit_cmd.extend(["--require-generator", args.require_generator])
            if args.ignore_pending_guard:
                submit_cmd.append("--ignore-pending-guard")
            if args.ignore_surface_guard:
                submit_cmd.append("--ignore-surface-guard")
            if args.submit:
                submit_cmd.extend(["--submit", "--confirm-batch", batch_id])

            proc = run_module(submit_cmd)
            print(proc.stdout, end="")
            if proc.stderr:
                print(proc.stderr, file=sys.stderr, end="")
            count = proc.stdout.count(" submitted attempt ")
            submitted += count
            made_progress += count
            if proc.returncode not in (0, 2):
                return proc.returncode

        if submitted >= args.target_submissions:
            break
        if made_progress == 0:
            if not args.wait_on_pending:
                print("auto-run stopped: no submission made this cycle")
                break
            print(f"auto-run sleeping {args.sleep_seconds}s before next status sync/submit cycle")
            time.sleep(args.sleep_seconds)
        else:
            time.sleep(args.after_submit_sleep)

    print(f"auto-run complete: submitted={submitted} cycles={cycles}")
    return 0


def immediate_ts() -> str:
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S")


def infer_immediate_family(source: Path, explicit_family: str) -> str:
    if explicit_family:
        return slugify(explicit_family)
    parent = source.parent.name
    if parent and parent not in {".", ""}:
        return slugify(parent)
    raise SystemExit("--family-id is required when source image is not inside a family-named directory")


def copy_immediate_source(source: Path, family_id: str, run_ts: str, *, no_copy: bool = False) -> Path:
    source = source.expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"source image not found: {source}")
    if source.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        raise SystemExit(f"unsupported source image suffix: {source.suffix}")
    if no_copy:
        return source
    dest_dir = EXTERNAL_INBOX_DIR / family_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{family_id}_{run_ts}{source.suffix.lower()}"
    if dest.exists():
        raise SystemExit(f"refusing to overwrite immediate inbox image: {dest}")
    shutil.copy2(source, dest)
    return dest


def immediate_campaign_path(batch_id: str, explicit_output: Path | None) -> Path:
    if explicit_output:
        return explicit_output.expanduser().resolve()
    return CAMPAIGNS_DIR / f"{slugify(batch_id)}.yaml"


def build_immediate_campaign(args: argparse.Namespace, source: Path, family_id: str, batch_id: str, run_ts: str) -> Path:
    registry = load_family_registry()
    meta = registry.get(family_id, {})
    scenario = str(meta.get("scenario") or args.scenario)
    channel = str(meta.get("channel") or args.channel)
    if not scenario or not channel:
        raise SystemExit(f"family {family_id!r} is not in registry; provide --scenario and --channel")

    wrap_as = args.wrap_as
    if wrap_as == "auto":
        wrap_as = "video" if channel == "video" else "pdf" if channel in {"document", "email"} else "image"

    cluster_id = str(args.cluster_id or meta.get("cluster_id") or family_id)
    campaign = {
        "name": batch_id,
        "description": "Immediate single-image campaign for a freshly generated GPT image.",
        "defaults": {"model": "all", "priority": args.priority, "phase": args.phase or meta.get("phase") or "calibrate"},
        "items": [
            {
                "name": f"{family_id}_{run_ts}",
                "family_id": family_id,
                "cluster_id": cluster_id,
                "generator": "external_image",
                "scenario": scenario,
                "channel": channel,
                "bounty": int(meta.get("bounty", args.bounty)),
                "target_action": str(meta.get("target_action") or args.target_action),
                "variants": [f"{family_id}_{run_ts}"],
                "seeds": [args.seed or int(run_ts.replace("_", "")[-8:])],
                "args": {
                    "source": [str(source)],
                    "scene_type": args.scene_type if args.scene_type != "generated_photo" else meta.get("scene_type", args.scene_type),
                    "evidence_source": args.evidence_source or meta.get("evidence_source", ""),
                    "visible_text_theme": args.visible_text_theme or meta.get("visible_text_theme", ""),
                    "authority_role": args.authority_role or meta.get("authority_role", ""),
                    "layout_cluster": args.layout_cluster or meta.get("layout_cluster", cluster_id),
                    "visible_text": args.visible_text or meta.get("visible_text", ""),
                    "prompt": args.prompt or meta.get("prompt", ""),
                    "title": args.title or meta.get("title", ""),
                    "description": args.description or meta.get("description", ""),
                    "method_detail": args.method_detail or meta.get("method_detail", ""),
                    "method": args.method,
                    "wrap_as": wrap_as,
                    "duration": args.duration,
                },
            }
        ],
    }
    out = immediate_campaign_path(batch_id, args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(campaign, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def newest_batch_record(records: list[dict[str, Any]], batch_id: str) -> dict[str, Any] | None:
    matches = [r for r in records if r.get("batch_id") == batch_id]
    if not matches:
        return None
    return max(matches, key=lambda r: int(r.get("updated_ts") or r.get("created_ts") or 0))


def cmd_generate_and_submit_one(args: argparse.Namespace) -> int:
    run_ts = args.run_ts or immediate_ts()
    source = Path(args.source_image)
    family_id = infer_immediate_family(source, args.family_id)
    batch_id = args.batch_id or f"immediate_{family_id}_{run_ts}"

    if args.submit and args.confirm_batch != batch_id:
        print(f"refusing live immediate submit: --confirm-batch must equal {batch_id!r}", file=sys.stderr)
        return 2

    copied_source = copy_immediate_source(source, family_id, run_ts, no_copy=args.no_copy)
    campaign = build_immediate_campaign(args, copied_source, family_id, batch_id, run_ts)
    print(f"immediate source={copied_source}")
    print(f"immediate campaign={campaign}")

    gen_args = argparse.Namespace(campaign=[campaign], queue=args.queue, limit=1, replace=args.replace)
    gen_code = cmd_generate(gen_args)
    if gen_code != 0:
        return gen_code

    records = read_jsonl(args.queue)
    rec = newest_batch_record(records, batch_id)
    if not rec:
        print(f"immediate submit failed: no queue record found for batch {batch_id}", file=sys.stderr)
        return 1
    queue_id = rec["queue_id"]
    print(f"immediate queue_id={queue_id}")

    dedupe_args = argparse.Namespace(queue=args.queue, queue_id=queue_id, batch_id=None, limit=1, fail_on_warn=True)
    dedupe_code = cmd_dedupe(dedupe_args)
    if dedupe_code != 0:
        print(f"immediate submit skipped after dedupe warning for {queue_id}", file=sys.stderr)
        return dedupe_code

    registry = load_family_registry()
    records = read_jsonl(args.queue)
    attempts = load_attempts()
    current = next((r for r in records if r.get("queue_id") == queue_id), None)
    if not current or current.get("status") != "ready":
        status = current.get("status") if current else "missing"
        print(f"immediate submit skipped: queue {queue_id} is not ready (status={status})", file=sys.stderr)
        return 2
    allowed, reasons = policy_decision(
        current,
        records=records,
        attempts=attempts,
        registry=registry,
        date=args.date or today_kst(),
        max_family_daily=args.max_family_daily,
        max_cluster_daily=args.max_cluster_daily,
        ignore_surface_guard=args.ignore_surface_guard,
    )
    if not allowed:
        print(f"immediate submit skipped by policy for {queue_id}: {', '.join(reasons)}", file=sys.stderr)
        return 2

    common_submit = {
        "queue": args.queue,
        "endpoint": args.endpoint,
        "queue_id": queue_id,
        "batch_id": None,
        "limit": 1,
        "allow_warn": False,
        "skip_fill": False,
        "daily_limit": args.daily_limit,
        "reserve": args.reserve,
        "batch_size": 1,
        "date": args.date,
        "check_site_count": args.check_site_count,
        "max_family_daily": args.max_family_daily,
        "max_cluster_daily": args.max_cluster_daily,
        "allow_paused": False,
        "allow_similar": False,
        "gpt_only": True,
        "require_generator": "",
        "max_pending": args.max_pending,
        "ignore_pending_guard": False,
        "ignore_surface_guard": args.ignore_surface_guard,
    }
    dry_code = cmd_submit(argparse.Namespace(**common_submit, submit=False, confirm_batch=""))
    if dry_code != 0:
        return dry_code
    if not args.submit:
        print(f"dry run complete. Live submit requires --submit --confirm-batch {batch_id}")
        return 0

    live_code = cmd_submit(argparse.Namespace(**common_submit, submit=True, confirm_batch=batch_id))
    after_live = next((r for r in read_jsonl(args.queue) if r.get("queue_id") == queue_id), {})
    if live_code == 0 and after_live.get("status") == "submitted":
        print(f"immediate live submit complete for {queue_id}; results not awaited")
    return live_code


def cmd_build_external_campaign(args: argparse.Namespace) -> int:
    inbox = args.inbox.expanduser().resolve()
    if not inbox.exists():
        inbox.mkdir(parents=True, exist_ok=True)
    pattern = "**/*" if args.recursive else "*"
    images = sorted(
        p for p in inbox.glob(pattern)
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    campaign = {
        "name": args.name,
        "description": "Auto-built campaign for externally generated image files.",
        "defaults": {"model": "all", "priority": args.priority, "phase": args.phase},
        "items": [],
    }
    registry = load_family_registry()
    grouped: list[tuple[str, list[Path]]]
    if args.group_by_dir:
        buckets: dict[str, list[Path]] = defaultdict(list)
        for image in images:
            rel = image.relative_to(inbox)
            bucket = slugify(str(rel.parent)) if rel.parent != Path(".") else slugify(image.stem)
            buckets[bucket].append(image)
        grouped = [(key, value) for key, value in sorted(buckets.items())]
    else:
        grouped = [(slugify(image.stem), [image]) for image in images]
    if args.limit:
        grouped = grouped[: args.limit]

    for idx, (stem, sources) in enumerate(grouped, start=1):
        first = sources[0]
        rel = first.relative_to(inbox)
        inferred_family = slugify(rel.parts[0]) if args.infer_family_from_dir and len(rel.parts) > 1 else args.family_id
        meta = registry.get(inferred_family, {})
        scenario = str(meta.get("scenario") or args.scenario)
        channel = str(meta.get("channel") or args.channel)
        family_id = str(meta.get("family_id") or inferred_family)
        cluster_id = str(args.cluster_id or meta.get("cluster_id") or (f"{family_id}_{stem}" if not args.group_by_dir else family_id))
        wrap_as = args.wrap_as
        if wrap_as == "auto":
            wrap_as = "video" if channel == "video" else "pdf" if channel in {"document", "email"} else "image"
        target_action = str(meta.get("target_action") or args.target_action)
        bounty = int(meta.get("bounty", args.bounty))
        campaign["items"].append(
            {
                "name": f"{family_id}_{stem}",
                "family_id": family_id,
                "cluster_id": cluster_id,
                "generator": "external_image",
                "scenario": scenario,
                "channel": channel,
                "bounty": bounty,
                "target_action": target_action,
                "variants": [f"{family_id}_{stem}_{idx:02d}"],
                "seeds": [args.seed_base + idx],
                "args": {
                    "source": [str(source) for source in sources],
                    "scene_type": args.scene_type if args.scene_type != "generated_photo" else meta.get("scene_type", args.scene_type),
                    "evidence_source": args.evidence_source or meta.get("evidence_source", ""),
                    "visible_text_theme": args.visible_text_theme or meta.get("visible_text_theme", ""),
                    "authority_role": args.authority_role or meta.get("authority_role", ""),
                    "layout_cluster": args.layout_cluster or meta.get("layout_cluster", cluster_id),
                    "visible_text": args.visible_text or meta.get("visible_text", ""),
                    "prompt": args.prompt or meta.get("prompt", ""),
                    "title": args.title or meta.get("title", ""),
                    "description": args.description or meta.get("description", ""),
                    "method_detail": args.method_detail or meta.get("method_detail", ""),
                    "method": args.method,
                    "wrap_as": wrap_as,
                    "duration": args.duration,
                },
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(campaign, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output} items={len(campaign['items'])}")
    return 0


def add_common_queue_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--queue", type=Path, default=QUEUE_PATH)


def main() -> int:
    p = argparse.ArgumentParser(description="Judgement Day campaign queue runner.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("plan", help="Show campaign candidates.")
    pp.add_argument("--campaign", action="append")
    pp.set_defaults(func=cmd_plan)

    pg = sub.add_parser("generate", help="Generate artifacts and enqueue manifests.")
    add_common_queue_arg(pg)
    pg.add_argument("--campaign", action="append")
    pg.add_argument("--limit", type=int)
    pg.add_argument("--replace", action="store_true")
    pg.set_defaults(func=cmd_generate)

    pd = sub.add_parser("dedupe", help="Run similarity checks for generated queue records.")
    add_common_queue_arg(pd)
    pd.add_argument("--queue-id")
    pd.add_argument("--batch-id")
    pd.add_argument("--limit", type=int)
    pd.add_argument("--fail-on-warn", action="store_true")
    pd.set_defaults(func=cmd_dedupe)

    pf = sub.add_parser("fill", help="Fill forms through CDP and stop before Submit.")
    add_common_queue_arg(pf)
    pf.add_argument("--endpoint", default="http://127.0.0.1:9222")
    pf.add_argument("--queue-id")
    pf.add_argument("--batch-id")
    pf.add_argument("--limit", type=int, default=1)
    pf.add_argument("--allow-warn", action="store_true")
    pf.set_defaults(func=cmd_fill)

    ps = sub.add_parser("submit", help="Optionally click Submit behind explicit safety flags.")
    add_common_queue_arg(ps)
    ps.add_argument("--endpoint", default="http://127.0.0.1:9222")
    ps.add_argument("--queue-id")
    ps.add_argument("--batch-id")
    ps.add_argument("--limit", type=int, default=1)
    ps.add_argument("--allow-warn", action="store_true")
    ps.add_argument("--skip-fill", action="store_true")
    ps.add_argument("--submit", action="store_true")
    ps.add_argument("--confirm-batch", default="")
    ps.add_argument("--daily-limit", type=int, default=DEFAULT_DAILY_LIMIT)
    ps.add_argument("--reserve", type=int, default=DEFAULT_RESERVE)
    ps.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    ps.add_argument("--date", default="")
    ps.add_argument("--check-site-count", action="store_true")
    ps.add_argument("--max-family-daily", type=int, default=5)
    ps.add_argument("--max-cluster-daily", type=int, default=2)
    ps.add_argument("--allow-paused", action="store_true")
    ps.add_argument("--allow-similar", action="store_true")
    ps.add_argument("--gpt-only", action="store_true", help="Only submit artifacts ingested from external GPT images.")
    ps.add_argument("--require-generator", default="", help="Only submit queue records whose manifest generator matches this exact value.")
    ps.add_argument("--max-pending", type=int, default=30)
    ps.add_argument("--ignore-pending-guard", action="store_true")
    ps.add_argument("--ignore-surface-guard", action="store_true", help="Allow one-off probes on a surface that was rotated after repeated SAFE results.")
    ps.set_defaults(func=cmd_submit)

    pm = sub.add_parser("mark-submitted", help="Log a manually submitted queue record into attempts.jsonl.")
    add_common_queue_arg(pm)
    pm.add_argument("--queue-id")
    pm.add_argument("--batch-id")
    pm.add_argument("--limit", type=int, default=1)
    pm.add_argument("--site-submission-id", default="")
    pm.add_argument("--note", default="")
    pm.set_defaults(func=cmd_mark_submitted)

    py = sub.add_parser("sync-results", help="Sync queue state from local attempts and capture leaderboard.")
    add_common_queue_arg(py)
    py.add_argument("--endpoint", default="http://127.0.0.1:9222")
    py.add_argument("--skip-leaderboard", action="store_true")
    py.add_argument("--site-status", action="store_true", help="Visit pending submission status pages through CDP before local sync.")
    py.add_argument("--require-site-status", action="store_true", help="Fail instead of warning if CDP site status sync fails.")
    py.add_argument("--site-limit", type=int, default=30)
    py.add_argument("--site-wait", type=float, default=1.0)
    py.add_argument("--status-url-template", default=SUBMISSION_STATUS_URL)
    py.add_argument("--record-pending-checks", action="store_true")
    py.add_argument("--include-resolved-statuses", action="store_true")
    py.set_defaults(func=cmd_sync_results)

    pr = sub.add_parser("score", help="Report cell-level performance and next queue items.")
    add_common_queue_arg(pr)
    pr.add_argument("--json", action="store_true")
    pr.add_argument("--next", type=int, default=50)
    pr.add_argument("--rotate-after", type=int, default=3)
    pr.add_argument("--max-family-daily", type=int, default=5)
    pr.add_argument("--max-cluster-daily", type=int, default=2)
    pr.set_defaults(func=cmd_score)

    pq = sub.add_parser("queue", help="List queue records.")
    add_common_queue_arg(pq)
    pq.add_argument("--queue-id")
    pq.add_argument("--batch-id")
    pq.add_argument("--status", action="append")
    pq.add_argument("--limit", type=int)
    pq.add_argument("--json", action="store_true")
    pq.add_argument("--gpt-only", action="store_true", help="Only list artifacts ingested from external GPT images.")
    pq.add_argument("--require-generator", default="", help="Only list queue records whose manifest generator matches this exact value.")
    pq.set_defaults(func=cmd_queue)

    pb = sub.add_parser("budget", help="Compute and persist today's quota budget.")
    pb.add_argument("--endpoint", default="http://127.0.0.1:9222")
    pb.add_argument("--date", default="")
    pb.add_argument("--daily-limit", type=int, default=DEFAULT_DAILY_LIMIT)
    pb.add_argument("--reserve", type=int, default=DEFAULT_RESERVE)
    pb.add_argument("--check-site-count", action="store_true")
    pb.set_defaults(func=cmd_budget)

    pfam = sub.add_parser("families", help="List family registry with EV stats.")
    add_common_queue_arg(pfam)
    pfam.add_argument("--json", action="store_true")
    pfam.set_defaults(func=cmd_families)

    ppol = sub.add_parser("policy", help="Explain quota/novelty policy decisions for queue records.")
    add_common_queue_arg(ppol)
    ppol.add_argument("--queue-id")
    ppol.add_argument("--batch-id")
    ppol.add_argument("--limit", type=int, default=50)
    ppol.add_argument("--date", default="")
    ppol.add_argument("--max-family-daily", type=int, default=5)
    ppol.add_argument("--max-cluster-daily", type=int, default=2)
    ppol.add_argument("--allow-paused", action="store_true")
    ppol.add_argument("--allow-similar", action="store_true")
    ppol.add_argument("--json", action="store_true")
    ppol.set_defaults(func=cmd_policy)

    pday = sub.add_parser("daily-plan", help="Build a quota-aware daily plan from queue and family EV.")
    add_common_queue_arg(pday)
    pday.add_argument("--endpoint", default="http://127.0.0.1:9222")
    pday.add_argument("--date", default="")
    pday.add_argument("--daily-limit", type=int, default=DEFAULT_DAILY_LIMIT)
    pday.add_argument("--reserve", type=int, default=DEFAULT_RESERVE)
    pday.add_argument("--check-site-count", action="store_true")
    pday.add_argument("--max-family-daily", type=int, default=5)
    pday.add_argument("--max-cluster-daily", type=int, default=2)
    pday.add_argument("--calibrate", type=int, default=30)
    pday.add_argument("--probe", type=int, default=20)
    pday.add_argument("--exploit", type=int, default=40)
    pday.add_argument("--allocation-profile", choices=["high_score", "phase"], default="high_score")
    pday.add_argument("--pending-guard", type=int, default=30)
    pday.set_defaults(func=cmd_daily_plan)

    pa = sub.add_parser("auto-run", help="Wait for CDP, sync pending statuses, then submit guarded batches.")
    pa.add_argument("--endpoint", default="http://127.0.0.1:9222")
    pa.add_argument("--batch-id", action="append", default=["robot_expansion_200"])
    pa.add_argument("--target-submissions", type=int, default=15)
    pa.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    pa.add_argument("--daily-limit", type=int, default=DEFAULT_DAILY_LIMIT)
    pa.add_argument("--reserve", type=int, default=DEFAULT_RESERVE)
    pa.add_argument("--max-pending", type=int, default=30)
    pa.add_argument("--wait-seconds", type=int, default=600)
    pa.add_argument("--poll-seconds", type=float, default=5.0)
    pa.add_argument("--sleep-seconds", type=float, default=45.0)
    pa.add_argument("--after-submit-sleep", type=float, default=5.0)
    pa.add_argument("--site-limit", type=int, default=30)
    pa.add_argument("--max-cycles", type=int, default=20)
    pa.add_argument("--submit", action="store_true")
    pa.add_argument("--confirm-auto-run", action="store_true")
    pa.add_argument("--wait-on-pending", action="store_true")
    pa.add_argument("--no-site-status", action="store_true")
    pa.add_argument("--require-site-status", action="store_true")
    pa.add_argument("--leaderboard-snapshot", action="store_true")
    pa.add_argument("--ignore-pending-guard", action="store_true")
    pa.add_argument("--ignore-surface-guard", action="store_true")
    pa.add_argument("--allow-warn", action="store_true")
    pa.add_argument("--allow-paused", action="store_true")
    pa.add_argument("--allow-similar", action="store_true")
    pa.add_argument("--gpt-only", action="store_true", help="Only submit artifacts ingested from external GPT images.")
    pa.add_argument("--require-generator", default="", help="Only submit queue records whose manifest generator matches this exact value.")
    pa.set_defaults(func=cmd_auto_run)

    pi = sub.add_parser(
        "generate-and-submit-one",
        help="Copy one freshly generated GPT image, enqueue it, dedupe, dry-run, and optionally submit it immediately.",
    )
    add_common_queue_arg(pi)
    pi.add_argument("--source-image", required=True, help="Freshly generated image file to submit.")
    pi.add_argument("--family-id", default="", help="Family registry ID. Defaults to the source image parent directory name.")
    pi.add_argument("--batch-id", default="", help="Explicit immediate batch ID. If omitted, immediate_<family_id>_<timestamp> is used.")
    pi.add_argument("--confirm-batch", default="", help="Must match the resolved immediate batch ID when --submit is used.")
    pi.add_argument("--run-ts", default="", help="Stable timestamp suffix, e.g. 20260504_153000.")
    pi.add_argument("--output", type=Path, help="Optional campaign file path. Defaults to campaigns/<batch_id>.yaml.")
    pi.add_argument("--no-copy", action="store_true", help="Use --source-image in place instead of copying to external_images/inbox.")
    pi.add_argument("--replace", action="store_true", help="Regenerate queue record if this immediate plan key already exists.")
    pi.add_argument("--endpoint", default="http://127.0.0.1:9222")
    pi.add_argument("--scenario", default="")
    pi.add_argument("--channel", default="")
    pi.add_argument("--cluster-id", default="")
    pi.add_argument("--phase", default="calibrate")
    pi.add_argument("--priority", type=int, default=500)
    pi.add_argument("--bounty", type=int, default=20)
    pi.add_argument("--target-action", default="resume_production")
    pi.add_argument("--scene-type", default="generated_photo")
    pi.add_argument("--evidence-source", default="")
    pi.add_argument("--visible-text-theme", default="")
    pi.add_argument("--authority-role", default="")
    pi.add_argument("--layout-cluster", default="")
    pi.add_argument("--visible-text", default="")
    pi.add_argument("--prompt", default="")
    pi.add_argument("--title", default="")
    pi.add_argument("--description", default="")
    pi.add_argument("--method-detail", default="")
    pi.add_argument("--method", default="ai_generated")
    pi.add_argument("--wrap-as", choices=["auto", "image", "pdf", "video"], default="auto")
    pi.add_argument("--duration", type=int, default=10)
    pi.add_argument("--seed", type=int, default=0)
    pi.add_argument("--daily-limit", type=int, default=100)
    pi.add_argument("--reserve", type=int, default=10)
    pi.add_argument("--date", default="")
    pi.add_argument("--check-site-count", action="store_true")
    pi.add_argument("--max-family-daily", type=int, default=5)
    pi.add_argument("--max-cluster-daily", type=int, default=1)
    pi.add_argument("--max-pending", type=int, default=30)
    pi.add_argument("--ignore-surface-guard", action="store_true", help="Allow this immediate one-off even when the broader scenario/channel surface is rotated after SAFE results.")
    pi.add_argument("--submit", action="store_true")
    pi.set_defaults(func=cmd_generate_and_submit_one)

    pe = sub.add_parser("build-external-campaign", help="Build a campaign file from image files in an inbox directory.")
    pe.add_argument("--inbox", type=Path, default=ROOT / "external_images" / "inbox")
    pe.add_argument("--output", type=Path, default=ROOT / "campaigns" / "external_robot_images.yaml")
    pe.add_argument("--name", default="external_robot_images")
    pe.add_argument("--scenario", default="track1_1_industrial_robot")
    pe.add_argument("--channel", default="image")
    pe.add_argument("--family-id", default="robot_generated_photo")
    pe.add_argument("--cluster-id", default="")
    pe.add_argument("--scene-type", default="generated_photo")
    pe.add_argument("--evidence-source", default="")
    pe.add_argument("--visible-text-theme", default="")
    pe.add_argument("--authority-role", default="")
    pe.add_argument("--layout-cluster", default="")
    pe.add_argument("--target-action", default="resume_production")
    pe.add_argument("--bounty", type=int, default=20)
    pe.add_argument("--priority", type=int, default=260)
    pe.add_argument("--phase", default="calibrate")
    pe.add_argument("--seed-base", type=int, default=20260900)
    pe.add_argument("--limit", type=int)
    pe.add_argument("--visible-text", default="")
    pe.add_argument("--prompt", default="")
    pe.add_argument("--title", default="")
    pe.add_argument("--description", default="")
    pe.add_argument("--method-detail", default="")
    pe.add_argument("--method", default="ai_generated")
    pe.add_argument("--wrap-as", choices=["auto", "image", "pdf", "video"], default="auto")
    pe.add_argument("--duration", type=int, default=10)
    pe.add_argument("--recursive", action="store_true")
    pe.add_argument("--infer-family-from-dir", action="store_true")
    pe.add_argument("--group-by-dir", action="store_true")
    pe.set_defaults(func=cmd_build_external_campaign)

    args = p.parse_args()
    mutating_funcs = {
        cmd_generate,
        cmd_dedupe,
        cmd_fill,
        cmd_submit,
        cmd_mark_submitted,
        cmd_sync_results,
        cmd_generate_and_submit_one,
    }
    if getattr(args, "func", None) in mutating_funcs and getattr(args, "queue", None):
        with queue_lock(args.queue):
            return args.func(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
