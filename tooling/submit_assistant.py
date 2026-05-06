"""Fill Judgement Day submission forms via Chrome CDP, stopping before Submit."""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import sys
import time
from pathlib import Path

from tooling.cdp import CDPClient
from tooling.common import combine_text, load_manifest

ROOT_URL = "https://judgementday.aim-intelligence.com/arena"
TRACK_URLS = {
    "track1_0": f"{ROOT_URL}/track1_0",
    "track1_1": f"{ROOT_URL}/track1_1",
    "track1_2": f"{ROOT_URL}/track1_2",
    "track1_3": f"{ROOT_URL}/track1_3",
    "track2_0": f"{ROOT_URL}/track2_0",
    "track2_1": f"{ROOT_URL}/track2_1",
    "track2_2": f"{ROOT_URL}/track2_2",
    "track2_3": f"{ROOT_URL}/track2_3",
}

METHOD_VALUES = {"manual", "ai_generated", "real_world", "programmatic", "hybrid", "other"}


def _track_key(scenario: str) -> str:
    m = re.match(r"(track[12]_[0-3])", scenario)
    if not m:
        raise ValueError(f"cannot infer track key from scenario: {scenario}")
    return m.group(1)


def _read_verification(cdp: CDPClient) -> dict[str, str]:
    expr = r"""
(() => {
  const text = document.body.innerText;
  const phrase = (text.match(/include the phrase "([^"]+)"/) || [])[1] || "";
  const token = (text.match(/session verification token is:\s*([a-z0-9-]+)/i) || [])[1] || "";
  return {phrase, token};
})()
"""
    return cdp.evaluate(expr) or {"phrase": "", "token": ""}


def _set_value_script(selector: str, value: str) -> str:
    return json.dumps(
        f"""
(() => {{
  const el = document.querySelector({selector!r});
  if (!el) return false;
  el.focus();
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')?.set;
  if (setter) setter.call(el, {value!r}); else el.value = {value!r};
  el.dispatchEvent(new Event('input', {{bubbles: true}}));
  el.dispatchEvent(new Event('change', {{bubbles: true}}));
  return true;
}})()
"""
    )


def _format_description(base: str, verification: dict[str, str]) -> str:
    suffix = []
    if verification.get("phrase"):
        suffix.append(verification["phrase"])
    if verification.get("token"):
        suffix.append(verification["token"])
    if not suffix:
        return base
    return base.rstrip() + "\n\nVerification: " + " ".join(suffix)


def _set_file_from_bytes(cdp: CDPClient, selector: str, path: Path) -> bool:
    """Set file input from bytes inside the browser.

    DOM.setFileInputFiles needs a path local to the Chrome host. Our Chrome is
    often reached through a reverse SSH tunnel from another OS, so passing a
    Linux path can kill the renderer with RESULT_CODE_KILLED_BAD_MESSAGE. This
    creates a File in page JS instead.
    """
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    expr = f"""
(() => {{
  const input = document.querySelector({selector!r});
  if (!input) return false;
  const binary = atob({data!r});
  const len = binary.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = binary.charCodeAt(i);
  const file = new File([bytes], {path.name!r}, {{type: {mime!r}}});
  const dt = new DataTransfer();
  dt.items.add(file);
  input.files = dt.files;
  input.dispatchEvent(new Event('input', {{bubbles: true}}));
  input.dispatchEvent(new Event('change', {{bubbles: true}}));
  return input.files.length === 1;
}})()
"""
    return bool(cdp.evaluate(expr, timeout=20))


def fill(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest) if args.manifest else {}
    scenario = args.scenario or manifest.get("scenario")
    if not scenario:
        print("scenario required (or manifest must contain scenario)", file=sys.stderr)
        return 2
    track = _track_key(scenario)
    channel = (args.channel or manifest.get("channel", "")).lower()
    if not channel:
        print("channel required (or manifest must contain channel)", file=sys.stderr)
        return 2
    artifact = args.artifact or manifest.get("artifact")
    payload_text = args.payload_text or manifest.get("payload_text") or manifest.get("visible_text") or ""
    description = args.description or manifest.get("description") or manifest.get("payload_text") or ""
    method = args.method or manifest.get("method") or "programmatic"
    method_detail = args.method_detail or manifest.get("method_detail") or ""
    if method not in METHOD_VALUES:
        print(f"unknown method {method}; choose one of {sorted(METHOD_VALUES)}", file=sys.stderr)
        return 2

    with CDPClient(args.endpoint) as cdp:
        cdp.navigate(TRACK_URLS[track])
        verification = _read_verification(cdp)
        if not verification.get("phrase") or not verification.get("token"):
            print("WARN: verification phrase/token not found in DOM; continuing without storing any token.")
        description = _format_description(description, verification)

        channel_clicked = cdp.evaluate(
            f"""
(() => {{
  const wanted = {channel!r};
  const buttons = [...document.querySelectorAll('button')];
  const btn = buttons.find(b => b.innerText.toLowerCase().includes(wanted));
  if (!btn) return false;
  btn.click();
  return true;
}})()
"""
        )
        if not channel_clicked:
            print(f"could not select channel containing {channel!r}", file=sys.stderr)
            return 1
        time.sleep(0.3)

        cdp.evaluate(json.loads(_set_value_script("#attackDescription", description)))
        cdp.evaluate(json.loads(_set_value_script("#attackMethodDetail", method_detail)))
        if payload_text and channel == "text":
            cdp.evaluate(
                f"""
(() => {{
  const el = [...document.querySelectorAll('textarea')].find(t =>
    t.id !== 'attackDescription' &&
    t.id !== 'attackMethodDetail' &&
    (t.placeholder || '').toLowerCase().includes('adversarial text')
  );
  if (!el) return false;
  el.focus();
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')?.set;
  if (setter) setter.call(el, {payload_text!r}); else el.value = {payload_text!r};
  el.dispatchEvent(new Event('input', {{bubbles: true}}));
  el.dispatchEvent(new Event('change', {{bubbles: true}}));
  return true;
}})()
"""
            )
        cdp.evaluate(
            f"""
(() => {{
  const el = document.querySelector('#attackMethod');
  if (!el) return false;
  el.value = {method!r};
  el.dispatchEvent(new Event('change', {{bubbles:true}}));
  return true;
}})()
"""
        )
        cdp.evaluate(
            """
(() => {
  const cb = document.querySelector('input[type="checkbox"]');
  if (!cb) return false;
  if (!cb.checked) cb.click();
  return cb.checked;
})()
"""
        )
        if artifact and Path(artifact).exists() and channel != "text":
            # Prefer JS File injection because Chrome may run on a different OS
            # than this workspace when accessed through a reverse CDP tunnel.
            ok = _set_file_from_bytes(cdp, "input[type=file]", Path(artifact).resolve())
            if not ok:
                print("WARN: no file input found or JS file injection failed.")

        status = cdp.evaluate(
            r"""
(() => {
  const submit = [...document.querySelectorAll('button')].find(b => b.innerText.trim() === 'Submit Attack');
  return {
    url: location.href,
    submitDisabled: submit ? submit.disabled : null,
            descriptionChars: document.querySelector('#attackDescription')?.value.length || 0,
            payloadChars: ([...document.querySelectorAll('textarea')].find(t => !t.id)?.value.length || 0),
            method: document.querySelector('#attackMethod')?.value || "",
    methodDetailChars: document.querySelector('#attackMethodDetail')?.value.length || 0,
    checked: document.querySelector('input[type="checkbox"]')?.checked || false,
    fileInputs: [...document.querySelectorAll('input[type=file]')].map(i => ({files: i.files.length, accept: i.accept}))
  };
})()
"""
        )
        print(json.dumps(status, ensure_ascii=False, indent=2))
        if args.submit:
            clicked = cdp.evaluate(
                r"""
(() => {
  const submit = [...document.querySelectorAll('button')].find(b => b.innerText.trim() === 'Submit Attack');
  if (!submit) return {ok:false, reason:'Submit Attack button not found', url: location.href};
  if (submit.disabled) return {ok:false, reason:'Submit Attack button disabled', url: location.href};
  submit.click();
  return {ok:true, reason:'clicked', url: location.href};
})()
""",
                timeout=10,
            )
            print(json.dumps({"submit": clicked}, ensure_ascii=False, indent=2))
            post_url = ""
            for _ in range(20):
                time.sleep(0.5)
                try:
                    post_url = cdp.evaluate("location.href", timeout=3) or ""
                except Exception:
                    post_url = ""
                if "/submissions/" in post_url:
                    break
            print(json.dumps({"post_submit": {"url": post_url}}, ensure_ascii=False, indent=2))
            print("Submitted attack via explicit --submit flag.")
        else:
            print("Filled form and stopped before Submit. Review in Chrome, then submit manually if correct.")
        return 0


def inspect(args: argparse.Namespace) -> int:
    with CDPClient(args.endpoint) as cdp:
        if args.url:
            cdp.navigate(args.url)
        data = cdp.evaluate(
            r"""
(() => ({
  url: location.href,
  title: document.title,
  loggedIn: document.body.innerText.includes('Sign Out'),
  buttons: [...document.querySelectorAll('button')].map((b,i)=>({i,text:b.innerText.slice(0,120),disabled:b.disabled})),
  inputs: [...document.querySelectorAll('input')].map((x,i)=>({i,type:x.type,name:x.name,id:x.id,accept:x.accept,checked:x.checked})),
  textareas: [...document.querySelectorAll('textarea')].map((x,i)=>({i,id:x.id,chars:x.value.length})),
  selects: [...document.querySelectorAll('select')].map((x,i)=>({i,id:x.id,value:x.value,options:[...x.options].map(o=>o.text+':'+o.value)}))
}))()
"""
        )
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Judgement Day form fill assistant; never clicks Submit.")
    p.add_argument("--endpoint", default="http://127.0.0.1:9222")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("inspect")
    pi.add_argument("--url")
    pi.set_defaults(func=inspect)

    pf = sub.add_parser("fill")
    pf.add_argument("--manifest")
    pf.add_argument("--scenario")
    pf.add_argument("--channel")
    pf.add_argument("--artifact")
    pf.add_argument("--payload-text")
    pf.add_argument("--description")
    pf.add_argument("--method", choices=sorted(METHOD_VALUES))
    pf.add_argument("--method-detail")
    pf.add_argument("--submit", action="store_true", help="Actually click Submit Attack after filling. Default is fill-only.")
    pf.set_defaults(func=fill)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
