"""Decision layer: risk_class / rename_safety / evidence completeness.

Pure function of (record, reference index, policy). No side effects, no
import of the action layer (PRD I2 / DESIGN 3.3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import core
from .config import (AUTO_SAFE, NEVER_TOUCH, PROTECTED, Policy,
                     REVIEW_REQUIRED)

NO_RULE = ""

# Reference kinds that mean "a running or scheduled thing points here". env,
# uninst and reparse are configuration pointers and frequently name a whole
# container (an uninstall entry with InstallLocation C:\Users\<name>\AppData\
# Local, measured 2026-09-22), which otherwise marks every file under it as
# referenced and makes the AUTO_SAFE class unreachable - including inside zones
# the operator explicitly declared regenerable.
RUNTIME_REFERENCE_KINDS = frozenset(("proc", "svc", "task", "taskwd", "startup",
                                     "run", "path"))

# Files that live directly in a volume root and are part of the OS itself.
# pagefile.sys alone measured 21.1 GiB on C: (2026-10-03) and was reaching the
# review list under "installed-binary" — i.e. the largest single object on the
# box was a cleanup candidate, which is exactly backwards: it is the one file
# whose removal destabilises Windows, and the one a human under disk pressure
# would hit first. A volume root is not a user directory; nothing here is
# ever a cleanup target.
VOLUME_ROOT_SYSTEM_FILES = frozenset((
    "pagefile.sys", "swapfile.sys", "hiberfil.sys", "bootmgr", "bootmgr.bak",
    "bootnxt", "ntldr.sys", "ntdetect.com", "boottel.dat", "dumpstack.log",
    "dumpstack.log.tmp", "config.sys", "io.sys", "msdos.sys", "ntldr",
))


def at_volume_root(path: str) -> bool:
    """True for a file sitting directly in C:\\ or D:\\ — not inside a folder."""
    import os as _os
    parent = _os.path.dirname(core.norm(path))
    return len(parent) <= 3 and parent[1:2] == ":"


@dataclass
class Decision:
    path: str
    risk_class: str = REVIEW_REQUIRED
    rule_ids: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    rename_safety: str = "TAG_WITH_REVIEW"
    suggested_action: str = "NONE"
    unknown: bool = False

    def as_dict(self) -> dict:
        return {"path": self.path, "risk_class": self.risk_class,
                "rule_ids": list(self.rule_ids), "evidence": dict(self.evidence),
                "rename_safety": self.rename_safety,
                "suggested_action": self.suggested_action,
                "unknown": self.unknown}


def rename_safety(name: str, ext: str, pol: Policy) -> str:
    """Three tiers (PRD C4'/I4). Unknown extension is never auto-safe."""
    e = (ext or core.ext_of(name))
    if not e:
        return "NEVER_RENAME"
    if e in pol.never_rename_ext:
        return "NEVER_RENAME"
    if e in pol.review_ext:
        return "TAG_WITH_REVIEW"
    if e in pol.safe_ext:
        return "FILENAME_TAG_SAFE"
    return "TAG_WITH_REVIEW"


def matches(rule: dict, rec: core.Record, pol: Policy,
            prep: Optional[dict] = None) -> bool:
    """Rule matching is deliberately conjunctive: every given field must hold.

    `prep` carries the compiled / casefolded form of this rule (Policy
    prepared_rules); without it the work is redone per record, which profiling
    put at a third of classify's time.
    """
    if prep is None:
        prep = pol.prepared_rules.get(rule.get("rule_id")) or {}
    prefixes = prep.get("scope_low")
    if prefixes:
        if not core.under_prefix(rec.path, prefixes):
            return False
    rx = prep.get("path_rx")
    if rx and not rx.search(rec.path.replace("\\", "/")):
        return False
    nrx = prep.get("name_rx")
    if nrx and not nrx.search(rec.name):
        return False
    exts = prep.get("ext_fold")
    if exts and rec.ext not in exts:
        return False
    min_age = rule.get("min_age_days")
    if min_age and rec.age_days < float(min_age):
        return False
    max_age = rule.get("max_age_days")
    if max_age and rec.age_days > float(max_age):
        return False
    cap = rule.get("max_size_bytes")
    if cap and rec.size > int(cap):
        return False
    return True


def regenerable(rec: core.Record, rule: dict, pol: Policy) -> bool:
    """Is this location rebuildable by the owning program (PRD I6 gate)?"""
    if core.under_prefix(rec.path, pol.regenerable_prefixes_low):
        return True
    kinds = rule.get("evidence_kind") or ""
    return kinds == "managed_zone" and core.under_prefix(
        rec.path, (core.low(pol.managed_root),))


def evidence_complete(rec: core.Record, rule: dict, refs, pol: Policy,
                      *, referenced: Optional[list] = None,
                      ignored: Optional[Sequence[str]] = None) -> tuple[bool, dict]:
    """The four-piece gate: rule + age + no reference + regenerable zone."""
    if referenced is None:
        referenced = list(refs.lookup(rec.path)) if refs else []
    parts = {
        "rule_hit": True,
        "age_days": round(rec.age_days, 2),
        "min_age_days": float(rule.get("min_age_days") or 0),
        "age_ok": (not rule.get("min_age_days"))
                  or rec.age_days >= float(rule["min_age_days"]),
        "referenced": referenced,
        "no_reference_ok": not referenced,
        "regenerable_ok": (not rule.get("requires_regenerable"))
                          or regenerable(rec, rule, pol),
        "size": rec.size,
        "mtime": rec.mtime,
    }
    if ignored:
        # disclosed, not silently dropped: the container-level inference that a
        # declared regenerable zone overrides (PRD I6 / FR4)
        parts["container_reference_ignored"] = list(ignored)
    ok = bool(parts["age_ok"] and parts["no_reference_ok"] and parts["regenerable_ok"])
    return ok, parts


def references(path: str, refs, pol: Policy) -> tuple[list, list]:
    """(blocking kinds, container kinds a declared zone overrides)."""
    if not refs:
        return [], []
    files, prefixes = refs.lookup_kinds(path)
    every = sorted(set(files) | set(prefixes))
    if not core.under_prefix(path, pol.regenerable_prefixes_low):
        return every, []
    runtime = sorted(set(files) | (set(prefixes) & RUNTIME_REFERENCE_KINDS))
    return runtime, sorted(set(every) - set(runtime))


def classify(rec: core.Record, refs, pol: Policy,
             now: Optional[float] = None) -> Decision:
    """Fixed decision order (DESIGN 3.3); first match wins for risk_class."""
    path = rec.path
    tier = rename_safety(rec.name, rec.ext, pol)
    referenced, ignored = references(path, refs, pol)
    dec = Decision(path=path, rename_safety=tier, suggested_action="NONE")

    frozen = core.under_prefix(path, pol.frozen_prefixes_low)
    protected_prefix = core.under_prefix(path, pol.protected_prefixes_low)
    if frozen:
        dec.risk_class = NEVER_TOUCH
        dec.rule_ids = ["frozen_zone"]
        dec.evidence = {"reason": "frozen_zone_prefix"}
        return dec
    if rec.ext in pol.never_touch_ext:
        dec.risk_class = NEVER_TOUCH
        dec.rule_ids = ["never_touch_ext:%s" % rec.ext]
        dec.evidence = {"reason": "extension_is_never_touchable", "ext": rec.ext,
                        "referenced": referenced}
        return dec
    if at_volume_root(path):
        name_low = (rec.name or "").lower()
        if (name_low in VOLUME_ROOT_SYSTEM_FILES
                or (rec.ext or "").lower() == "sys"
                or name_low.startswith("$")):
            dec.risk_class = NEVER_TOUCH
            dec.rule_ids = ["volume_root_system_file"]
            dec.evidence = {"reason": "os_system_file_in_volume_root",
                            "name": rec.name, "referenced": referenced}
            return dec
    if protected_prefix or referenced:
        dec.risk_class = PROTECTED
        dec.rule_ids = (["protected_prefix"] if protected_prefix else [])
        dec.evidence = {"reason": "runtime_reference_or_protected_prefix",
                        "referenced": referenced,
                        "container_reference_ignored": ignored,
                        "protected_prefix": bool(protected_prefix)}
        return dec

    prep = pol.prepared_rules
    hits = [r for r in pol.rules if matches(r, rec, pol, prep.get(r.get("rule_id")))]
    cleanup = [r for r in hits if not r.get("rank_only")]
    auto: list[dict] = []
    for rule in cleanup:
        if rule.get("action_class") != AUTO_SAFE:
            continue
        complete, parts = evidence_complete(rec, rule, refs, pol,
                                            referenced=referenced,
                                            ignored=ignored)
        if complete:
            if not auto:  # the first completing rule owns the evidence
                dec.evidence = parts
            auto.append(rule)
    if auto:
        dec.risk_class = AUTO_SAFE
        dec.rule_ids = [r["rule_id"] for r in auto]
        dec.suggested_action = auto[0].get("suggested_action", "MOVE")
        dec.evidence.setdefault("matched_rules", [r["rule_id"] for r in auto])
        return dec
    if cleanup:
        rule = cleanup[0]
        complete, parts = evidence_complete(rec, rule, refs, pol,
                                           referenced=referenced,
                                           ignored=ignored)
        dec.risk_class = REVIEW_REQUIRED
        dec.rule_ids = [r["rule_id"] for r in cleanup]
        dec.evidence = parts
        dec.evidence["gate_failed"] = [k for k in
                                       ("age_ok", "no_reference_ok", "regenerable_ok")
                                       if not parts[k]]
        return dec
    if hits:
        dec.risk_class = REVIEW_REQUIRED
        dec.rule_ids = [r["rule_id"] for r in hits]
        dec.evidence = {"reason": "rank_only_rule", "size": rec.size,
                        "age_days": round(rec.age_days, 2)}
        return dec

    known = rec.ext in pol.safe_ext or rec.ext in pol.review_ext
    dec.risk_class = REVIEW_REQUIRED
    dec.unknown = not known
    dec.rule_ids = [NO_RULE]
    dec.evidence = {"reason": "no_rule_matched" if known else "unknown_extension",
                    "ext": rec.ext, "size": rec.size,
                    "age_days": round(rec.age_days, 2)}
    if tier == "NEVER_RENAME":
        dec.suggested_action = "NONE"
    return dec


def is_cleanable(dec: Decision) -> bool:
    return dec.risk_class == AUTO_SAFE


def needs_ai(dec: Decision) -> bool:
    """Only UNKNOWN / REVIEW / tag-suggestion records may be asked (PRD 12)."""
    return dec.risk_class in (REVIEW_REQUIRED,) and dec.unknown
