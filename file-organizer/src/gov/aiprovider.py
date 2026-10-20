"""Pluggable AI judgment. `off` is the default and has zero side effects.

Failure anywhere (missing binary, timeout, bad JSON, wrong keys, enum value
out of range) returns None so the caller can fail closed to REVIEW_REQUIRED.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Optional

from . import core

JUDGMENT_KEYS = ("category", "tags", "project_or_topic", "rename_safety",
                 "confidence", "reason", "suggested_action")
# Defaults only; the real vocabulary comes from [tag].categories in policy.
CATEGORIES = ("文档", "表格", "演示", "图片", "视频", "压缩包", "工程资料", "其他")
ACTIONS = ("NONE", "MOVE", "RENAME", "REVIEW")
PROMPT_VERSION = "gov-judge-1"

PROMPT_BODY = """You are a file-classification helper for a local Windows file
governance tool. Reply with ONE JSON object and nothing else.

Keys, exactly these seven:
{"category": one of %s,
 "tags": array of strings formatted "group-value",
 "project_or_topic": string,
 "rename_safety": one of %s,
 "confidence": number between 0 and 1,
 "reason": string,
 "suggested_action": one of %s}

Rules: never invent paths; the record only carries a sanitized name, extension,
size and age. If information is insufficient answer category "%s",
suggested_action "REVIEW", confidence 0.0.

Record:
"""


def prompt_for(categories: tuple) -> str:
    fallback = str(categories[-1]) if categories else "其他"
    return PROMPT_BODY % (list(categories), list(core.RENAME_TIERS),
                          list(ACTIONS), fallback)


class AiError(Exception):
    pass


@dataclass
class Judgment:
    category: str
    tags: list
    project_or_topic: str
    rename_safety: str
    confidence: float
    reason: str
    suggested_action: str

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in JUDGMENT_KEYS}


def _validate(payload: Any, categories: tuple) -> Optional[Judgment]:
    if not isinstance(payload, dict):
        return None
    if set(payload) != set(JUDGMENT_KEYS):
        return None
    if payload["category"] not in categories:
        return None
    if payload["rename_safety"] not in core.RENAME_TIERS:
        return None
    if payload["suggested_action"] not in ACTIONS:
        return None
    if not isinstance(payload["tags"], list) or not all(
            isinstance(t, str) for t in payload["tags"]):
        return None
    try:
        conf = float(payload["confidence"])
    except (TypeError, ValueError):
        return None
    if not 0.0 <= conf <= 1.0:
        return None
    if not isinstance(payload["reason"], str) or not isinstance(
            payload["project_or_topic"], str):
        return None
    return Judgment(payload["category"], list(payload["tags"]),
                    payload["project_or_topic"], payload["rename_safety"],
                    conf, payload["reason"], payload["suggested_action"])


def extract_json(text: str) -> Optional[dict]:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def desensitize(record: dict, *, allow_preview: bool = False) -> dict:
    out = {
        "name": core.sanitize_path_for_ai(str(record.get("path", "")), keep=1),
        "parent_hint": core.sanitize_path_for_ai(str(record.get("path", "")), keep=2),
        "ext": record.get("ext", ""),
        "size": record.get("size", 0),
        "age_days": record.get("age_days", 0),
    }
    if allow_preview:
        head = str(record.get("preview", ""))[:512]
        if head:
            out["preview"] = head
    return out


def categories_from(pol) -> tuple:
    """The judgment vocabulary comes from the policy, not from this module."""
    raw = (getattr(pol, "tag", None) or {}).get("categories") or CATEGORIES
    return tuple(str(c) for c in raw)


def _cache_file(pol, record: dict) -> str:
    key = core.cache_key(str(record.get("path", "")),
                         int(record.get("size", 0) or 0),
                         float(record.get("mtime", 0) or 0.0))
    return os.path.join(core.norm(pol.state_dir), "ai",
                        "%s-%s.json" % (PROMPT_VERSION, key))


def _cache_load(pol, record: dict, categories: tuple) -> Optional[Judgment]:
    """PRD 12: one judgment per (path, size, mtime); a stale key is a miss."""
    path = _cache_file(pol, record)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            row = json.load(fh)
    except (OSError, ValueError):
        return None
    return _validate(row.get("judgment") if isinstance(row, dict) else None,
                     categories)


def _cache_store(pol, record: dict, judgment: Judgment) -> None:
    path = _cache_file(pol, record)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"prompt_version": PROMPT_VERSION, "ts": core.now_iso(),
                   "input": desensitize(record),
                   "judgment": judgment.as_dict()},
                  fh, ensure_ascii=False, sort_keys=True)


def judge(record: dict, context: dict, pol) -> Optional[dict]:
    """Return a judgment dict, or None. `off` never reaches this path."""
    ai = dict(pol.ai or {})
    provider = str(ai.get("provider", "off")).lower()
    if provider in ("", "off", "none", "disabled"):
        return None
    model = str(ai.get("model", provider))
    categories = categories_from(pol)
    if not bool(ai.get("disable_cache", False)):
        cached = _cache_load(pol, record, categories)
        if cached is not None:
            out = cached.as_dict()
            out.update({"model": model, "prompt_version": PROMPT_VERSION,
                        "cached": True})
            return out
    if provider != "local_cli":
        return {"error": "unsupported_provider:%s" % provider, "model": model}
    timeout = float(ai.get("timeout_s", 90))
    payload = desensitize(record, allow_preview=bool(
        ai.get("allow_content_preview", False)))
    try:
        j = _call_cli(payload, ai, timeout, categories)
    except (AiError, OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                "model": model}
    if j is None:
        return {"error": "invalid_judgment", "model": model}
    _cache_store(pol, record, j)
    out = j.as_dict()
    out.update({"model": model, "prompt_version": PROMPT_VERSION})
    return out


class Runner:
    """Budgeted `ai_judge` callback for candidates.build (PRD 12 limits)."""

    def __init__(self, pol):
        ai = dict(pol.ai or {})
        self.pol = pol
        self.provider = str(ai.get("provider", "off")).lower()
        self.max_files = int(ai.get("max_ai_files", 200))
        self.max_wall_s = float(ai.get("max_wall_s", 900))
        self.started_mono = time.monotonic()
        self.calls = 0
        self.over_budget = 0

    @property
    def enabled(self) -> bool:
        return self.provider not in ("", "off", "none", "disabled")

    def __call__(self, record, dec) -> Optional[dict]:
        if not self.enabled:
            return None
        if (self.calls >= self.max_files or
                (time.monotonic() - self.started_mono) > self.max_wall_s):
            self.over_budget += 1
            return {"error": "ai_budget", "calls": self.calls}
        self.calls += 1
        return judge(record.to_dict(), dec.as_dict(), self.pol)

    def stats(self) -> dict:
        return {"provider": self.provider, "calls": self.calls,
                "over_budget": self.over_budget,
                "max_ai_files": self.max_files,
                "wall_s": round(time.monotonic() - self.started_mono, 3)}


def _prompt_for(payload: dict, categories: tuple) -> str:
    return prompt_for(categories) + json.dumps(payload, ensure_ascii=False,
                                               sort_keys=True)


def _call_cli(payload: dict, ai: dict, timeout: float,
              categories: tuple) -> Optional[Judgment]:
    """One subprocess call. The answer is read from stdout: `--output-schema`
    measured ineffective, so the schema is part of the prompt body."""
    cmd = list(ai.get("cmd") or [])
    if not cmd:
        raise AiError("ai.cmd is empty for provider=local_cli")
    exe = shutil.which(cmd[0]) or (cmd[0] if os.path.isfile(cmd[0]) else None)
    if exe is None:
        raise AiError("ai binary not found: %s" % cmd[0])
    argv = [exe] + cmd[1:] + [_prompt_for(payload, categories)]
    proc = subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          cwd=ai.get("cwd") or None)
    if proc.returncode != 0:
        raise AiError("exit %s: %s" % (proc.returncode, (proc.stderr or "")[:200]))
    return _validate(extract_json(proc.stdout or ""), categories)
