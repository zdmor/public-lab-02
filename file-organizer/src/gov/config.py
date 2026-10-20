"""Three-layer policy loading with fail-closed merge semantics.

Layer order (PRD section 14): policy/common.toml -> policy/role_*.toml ->
config/machine_<hostname>.toml. The machine layer may only ADD and TIGHTEN.

Merge model:
  map        -> recursive merge
  scalar     -> lower layer wins
  rules      -> whole-entry replacement keyed by rule_id; severity may not move
  scan_root  -> whole-entry replacement keyed by normalized path
  plain list -> may NOT be redefined; use `<key>_add` / `<key>_remove`
"""
from __future__ import annotations

import functools
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from . import core

AUTO_SAFE = "AUTO_SAFE"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
PROTECTED = "PROTECTED"
NEVER_TOUCH = "NEVER_TOUCH"
TIGHTEN_ORDER = {AUTO_SAFE: 0, REVIEW_REQUIRED: 1, PROTECTED: 2, NEVER_TOUCH: 3}

RULE_FIELDS = {
    "rule_id", "version", "action_class", "reason", "scope_prefixes",
    "path_regex", "name_regex", "ext_in", "min_age_days", "max_age_days",
    "max_size_bytes", "requires_no_reference", "requires_regenerable",
    "rank_only", "suggested_action", "dest_zone", "evidence_kind",
}
ROOT_FIELDS = {"path", "role", "purpose", "allow_volume_root", "kinds"}

# P0-P4 archive lifecycle classes (Issue #22 / NODE_STORAGE_LIFECYCLE_V1).
# Independent of the risk-class axis: lifecycle says "which retention bucket
# this artifact belongs to", risk_class says "may the cleaner touch it".
# Coded as policy, not prose, per N1 checklist section C.
LIFECYCLE_FIELDS = {
    "class_id", "min_retention_days", "archive", "retire_via_trash",
    "google_drive", "description",
}
# Canonical class ids and their frozen retention floors (v3.1 / lifecycle V1).
LIFECYCLE_CLASSES = ("P0", "P1", "P2", "P3", "P4")


class PolicyError(Exception):
    """Raised for any ambiguous or loosening configuration."""


class RootError(PolicyError):
    """Raised when a requested root is not authorized (PRD I3)."""


def read_toml(path: str) -> dict:
    if not os.path.isfile(path):
        raise PolicyError("policy file missing: %s" % path)
    with open(path, "rb") as fh:
        try:
            return tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise PolicyError("bad TOML %s: %s" % (path, exc)) from exc


def _folded(vals: Iterable[Any]) -> frozenset:
    return frozenset(str(v).casefold() for v in vals)


def _rules_by_id(rules: Iterable[dict]) -> dict:
    return {r["rule_id"]: r for r in rules
            if isinstance(r, dict) and r.get("rule_id")}


def _merge(upper: dict, lower: dict, layer: str) -> dict:
    """map = recursive, scalar = lower wins, lists = explicit directive only."""
    out = dict(upper)
    for key, lv in lower.items():
        if key.endswith("_add") or key.endswith("_remove"):
            adding = key.endswith("_add")
            base = key[:-len("_add")] if adding else key[:-len("_remove")]
            if not isinstance(lv, list):
                raise PolicyError("%s: directive '%s' must be an array" % (layer, key))
            if base not in out:
                raise PolicyError("%s: directive '%s' targets '%s', which no upper "
                                  "layer defines; write the list itself instead"
                                  % (layer, key, base))
            cur = list(out[base])
            if adding:
                out[base] = cur + [item for item in lv if item not in cur]
            else:
                drop = set(lv)
                out[base] = [c for c in cur if c not in drop]
            continue
        uv = out.get(key)
        if isinstance(lv, dict):
            out[key] = _merge(uv if isinstance(uv, dict) else {}, lv, layer)
        elif key == "rules":
            out[key] = _merge_rules(uv or [], lv, layer)
        elif key == "scan_root":
            out[key] = _merge_roots(uv or [], lv)
        elif isinstance(lv, list):
            if key in out:
                raise PolicyError("%s: list '%s' is redefined; use '%s_add' / "
                                  "'%s_remove'" % (layer, key, key, key))
            out[key] = list(lv)
        else:
            out[key] = lv
    return out


def _merge_rules(existing: list, incoming: list, layer: str) -> list:
    by_id = {r["rule_id"]: dict(r) for r in existing}
    order = [r["rule_id"] for r in existing]
    for raw in incoming:
        rule = dict(raw)
        rid = rule.get("rule_id")
        old = by_id.get(rid)
        if old is None:
            by_id[rid] = rule
            order.append(rid)
            continue
        old_cls, new_cls = old.get("action_class"), rule.get("action_class")
        if new_cls is not None and new_cls != old_cls:
            raise PolicyError(
                "%s: rule '%s' moves action_class %s -> %s; a rule_id replaces the "
                "whole entry, but severity may never move between layers"
                % (layer, rid, old_cls, new_cls))
        by_id[rid] = rule
    return [by_id[r] for r in order]


def _merge_roots(existing: list, incoming: list) -> list:
    by_path = {core.low(r["path"]): dict(r) for r in existing}
    order = [core.low(r["path"]) for r in existing]
    for raw in incoming:
        p = core.low(raw["path"])
        if p not in by_path:
            order.append(p)
        by_path[p] = dict(raw)
    return [by_path[p] for p in order]


def _check_gate_monotonic(cur: dict, rule: dict) -> None:
    """A machine override may not weaken the AUTO_SAFE evidence it inherits.

    `validate()` already forces every AUTO_SAFE rule to state its gates, so the
    open door is a *smaller* number: 90 days -> 1 day, or a wider size cap.
    """
    if cur.get("action_class") != AUTO_SAFE or rule.get("action_class") != AUTO_SAFE:
        return
    rid = rule.get("rule_id")
    old_age = float(cur.get("min_age_days") or 0)
    if "min_age_days" in rule and float(rule["min_age_days"] or 0) < old_age:
        raise PolicyError("machine rule '%s' shortens min_age_days %s -> %s (PRD I6)"
                          % (rid, old_age, rule["min_age_days"]))
    old_cap = cur.get("max_size_bytes")
    if old_cap is not None and "max_size_bytes" in rule:
        new_cap = rule["max_size_bytes"]
        if new_cap is None or int(new_cap) > int(old_cap):
            raise PolicyError("machine rule '%s' widens max_size_bytes %s -> %s"
                              % (rid, old_cap, new_cap))


def _check_loosening(base: dict, delta: dict, layer: str) -> None:
    """Machine layer may add reviews and tighten, never loosen."""
    if layer != "machine":
        return
    cur_rules = _rules_by_id(base.get("rules", []))
    for rule in delta.get("rules", []):
        if not isinstance(rule, dict):
            continue
        rid, cls = rule.get("rule_id"), rule.get("action_class", REVIEW_REQUIRED)
        if cls not in TIGHTEN_ORDER:
            raise PolicyError("machine rule '%s' has bad action_class %r" % (rid, cls))
        cur = cur_rules.get(rid)
        if cur is None:
            if TIGHTEN_ORDER[cls] < TIGHTEN_ORDER[REVIEW_REQUIRED]:
                raise PolicyError("machine layer may not add a new %s rule ('%s')"
                                  % (cls, rid))
        elif TIGHTEN_ORDER[cls] < TIGHTEN_ORDER.get(
                cur.get("action_class", REVIEW_REQUIRED), 1):
            raise PolicyError("machine rule '%s' loosens %s -> %s"
                              % (rid, cur.get("action_class"), cls))
        else:
            _check_gate_monotonic(cur, rule)
    for section in ("scan", "budget", "rename"):
        blk = delta.get(section)
        if not isinstance(blk, dict):
            continue
        for key, new in blk.items():
            old = (base.get(section) or {}).get(key)
            if not isinstance(new, (int, float)) or isinstance(new, bool):
                continue
            if not isinstance(old, (int, float)) or isinstance(old, bool):
                continue
            widened = key.startswith("max_") and new > old
            if widened:
                raise PolicyError("machine layer widens %s.%s %s -> %s"
                                  % (section, key, old, new))


@dataclass
class RootDecl:
    path: str
    role: str = "UNKNOWN"
    purpose: str = ""
    allow_volume_root: bool = False
    kinds: tuple = ("scan",)

    @property
    def is_volume_root(self) -> bool:
        return re.match(r"^[A-Za-z]:\\?$", self.path) is not None


@dataclass
class LifecycleClass:
    """One P0-P4 archive retention bucket (Issue #22), from policy.

    `archive` says whether the class is ever promoted into D:\\Data\\Archive
    (P0/P1/P2 true; P3 staging and P4 cache are not archived).
    `retire_via_trash` says whether retirement must route through
    _System\\Trash + >=30d recoverability (all except P4, which is cleaned
    by existing reversible cache rules instead).
    """

    class_id: str
    min_retention_days: int = 0
    archive: bool = False
    retire_via_trash: bool = True
    google_drive: bool = False
    description: str = ""


@dataclass
class Policy:
    source_files: tuple = ()
    raw: dict = field(default_factory=dict)
    roots: tuple = ()
    rules: tuple = ()
    policy_id: str = ""
    policy_version: str = ""
    hostname: str = ""
    hard_skip: tuple = ()
    cloud_mount_names: tuple = ()
    cloud_prefixes: tuple = ()
    frozen_prefixes: tuple = ()
    protected_prefixes: tuple = ()
    regenerable_prefixes: tuple = ()
    never_touch_ext: frozenset = frozenset()
    never_rename_ext: frozenset = frozenset()
    review_ext: frozenset = frozenset()
    safe_ext: frozenset = frozenset()
    max_target_path: int = core.MAX_TARGET_PATH
    max_actions_per_run: int = 2000
    state_dir: str = ""
    managed_root: str = ""
    # per-volume managed roots: drive letter (lower, "c:") -> root path.
    # A MOVE must land on the source volume (executor cross_volume rule), so
    # each volume that may emit actions needs its own declared root; volumes
    # without an entry fail closed at plan time.
    managed_roots: dict = field(default_factory=dict)
    # optional second-location journal mirror (PRD 13 / P0-3): must be on a
    # different failure domain than state_dir. Empty = mirroring off.
    journal_backup_dir: str = ""
    python: str = ""
    min_python: tuple = (3, 13)
    strict_interpreter: bool = True
    budget: dict = field(default_factory=dict)
    scan: dict = field(default_factory=dict)
    rename: dict = field(default_factory=dict)
    tag: dict = field(default_factory=dict)
    ai: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    roles: dict = field(default_factory=dict)
    housekeep: dict = field(default_factory=dict)
    release: dict = field(default_factory=dict)
    # Issue #22: P0-P4 archive lifecycle classes + ExternalNodeIntake roots.
    lifecycle_classes: tuple = ()
    lifecycle: dict = field(default_factory=dict)
    external_intake_roots: tuple = ()

    # ---- core.ScanRules interface -------------------------------------
    # cached, not plain properties: the walk reads these per directory and the
    # classifier per file, and rebuilding a frozenset or a casefolded tuple
    # thousands of times measured 30 norm() calls per record in profiling.
    @functools.cached_property
    def hard_skip_folded(self) -> frozenset:
        return _folded(self.hard_skip)

    @functools.cached_property
    def cloud_roots_folded(self) -> frozenset:
        return _folded(self.cloud_mount_names)

    @functools.cached_property
    def cloud_prefixes_low(self) -> tuple:
        return tuple(core.low(x) for x in self.cloud_prefixes)

    @functools.cached_property
    def frozen_prefixes_low(self) -> tuple:
        return tuple(core.low(x) for x in self.frozen_prefixes)

    @functools.cached_property
    def managed_prefixes_low(self) -> tuple:
        """Every volume's managed root, so the walk prunes all of them.

        A MOVE parks the file inside one of these; scanning it again would
        report our own trash as user data and skew the weekly numbers.
        """
        roots = list(self.managed_roots.values()) or [self.managed_root]
        return tuple(core.low(x) for x in roots if x)

    @functools.cached_property
    def protected_prefixes_low(self) -> tuple:
        return tuple(core.low(x) for x in self.protected_prefixes)

    @functools.cached_property
    def regenerable_prefixes_low(self) -> tuple:
        return tuple(core.low(x) for x in self.regenerable_prefixes)

    @functools.cached_property
    def prepared_rules(self) -> dict:
        """Per rule_id: compiled regexes and casefolded sets for `matches`."""
        out = {}
        for rule in self.rules:
            rx, nrx = rule.get("path_regex"), rule.get("name_regex")
            exts = rule.get("ext_in")
            out[rule.get("rule_id")] = {
                "scope_low": tuple(core.low(x)
                                   for x in (rule.get("scope_prefixes") or [])),
                "path_rx": re.compile(rx, re.I) if rx else None,
                "name_rx": re.compile(nrx, re.I) if nrx else None,
                "ext_fold": frozenset(str(x).casefold() for x in exts)
                            if exts else None,
            }
        return out

    # ---- root authorization (PRD I3) ----------------------------------
    def root_for(self, path: str) -> Optional[RootDecl]:
        p = core.low(path)
        best = None
        for r in self.roots:
            lp = core.low(r.path)
            if core.prefixed(p, lp):
                if best is None or len(lp) > len(core.low(best.path)):
                    best = r
        return best

    def authorize(self, path: str, kind: str = "scan") -> RootDecl:
        if kind not in ("scan", "action"):
            raise RootError("unknown kind %r" % kind)
        r = self.root_for(path)
        if r is None:
            raise RootError("root not declared in MACHINE_OVERRIDE: %s" % path)
        if kind not in r.kinds:
            raise RootError("root %s does not allow kind=%s (declared: %s)"
                            % (r.path, kind, ",".join(r.kinds)))
        if not any(core.prefixed(core.low(path), core.low(m))
                   for m in self.managed_roots.values()):
            hit = self.excluded_dir_name(path)
            if hit:
                raise RootError("%s directory named in root refused: %s"
                                % (hit, path))
        if core.is_cloud_prefix(path, self.cloud_prefixes_low):
            raise RootError("cloud mount root refused: %s" % path)
        if core.under_prefix(path, self.frozen_prefixes_low):
            raise RootError("frozen zone root refused: %s" % path)
        if r.is_volume_root and not r.allow_volume_root:
            raise RootError("volume root refused unless allow_volume_root=true: %s"
                            % r.path)
        return r

    def excluded_dir_name(self, path: str) -> Optional[str]:
        """A root whose own path passes a prune name is never authorized.

        The walk prunes these names, so authorizing them as a root would let
        `--root D:\\...\\Nutstore` bypass the cloud/recycle-bin exclusion.
        """
        cloud, skip = self.cloud_roots_folded, self.hard_skip_folded
        for part in core.low(path).split(os.sep):
            if part in cloud:
                return "cloud mount"
            if part in skip:
                return "hard-skip"
        return None

    def action_target_ok(self, path: str) -> Optional[str]:
        """Reason this path may not receive an action; None means allowed."""
        if core.under_prefix(path, self.frozen_prefixes_low):
            return "frozen_zone"
        try:
            r = self.authorize(path, "action")
        except RootError as exc:
            return str(exc)
        if r.is_volume_root:
            return "volume_root_target"
        return None

    def new_budget(self) -> core.Budget:
        return core.Budget(
            max_wall_s=float(self.budget.get("max_wall_s", 540)),
            max_files=int(self.budget.get("max_files", 2_500_000)),
            max_bytes=int(self.budget.get("max_bytes", 200 * 1024 ** 3)),
        )

    def rule(self, rule_id: str) -> Optional[dict]:
        return _rules_by_id(self.rules).get(rule_id)

    def as_dict(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "sources": list(self.source_files),
            "roots": [r.path for r in self.roots],
            "rules": len(self.rules),
            "budget": dict(self.budget),
            "ai_provider": self.ai.get("provider", "off"),
            "max_target_path": self.max_target_path,
        }


def load(base_dir: str, machine: Optional[str] = None,
         extra_layers: Optional[Sequence[tuple[str, dict]]] = None) -> Policy:
    """Load common -> role_* -> machine_<host>; raise PolicyError on ambiguity."""
    host = machine or core.machine_name()
    pdir = os.path.join(base_dir, "policy")
    if not os.path.isdir(pdir):
        raise PolicyError("policy dir missing: %s" % pdir)
    layers: list[tuple[str, dict]] = []
    used: list[str] = []
    common = os.path.join(pdir, "common.toml")
    layers.append(("common", _stamp(read_toml(common), common)))
    used.append(common)
    for fn in sorted(os.listdir(pdir)):
        if fn.startswith("role_") and fn.endswith(".toml"):
            p = os.path.join(pdir, fn)
            layers.append(("role", _stamp(read_toml(p), p)))
            used.append(p)
    mpath = os.path.join(base_dir, "config", "machine_%s.toml" % host)
    if os.path.isfile(mpath):
        layers.append(("machine", _stamp(read_toml(mpath), mpath)))
        used.append(mpath)
    for layer, doc in (extra_layers or []):
        layers.append((layer, _stamp(dict(doc), layer)))
    merged: dict = {}
    for layer, doc in layers:
        _check_loosening(merged, doc, layer)
        merged = _merge(merged, doc, layer)
    return build(merged, tuple(used), base_dir, host)


def _stamp(doc: dict, src: str) -> dict:
    doc.setdefault("policy_id", os.path.basename(src))
    doc.setdefault("policy_version", "0")
    return doc


def build(flat: dict, sources: tuple, base_dir: str, host: str) -> Policy:
    for key, allowed in (("rules", RULE_FIELDS), ("scan_root", ROOT_FIELDS)):
        for entry in flat.get(key, []):
            unknown = set(entry) - allowed
            if unknown:
                raise PolicyError("%s entry %r has unknown fields %s" % (
                    key, entry.get("rule_id") or entry.get("path"), sorted(unknown)))
    roots = tuple(RootDecl(
        path=core.norm(r["path"]), role=r.get("role", "UNKNOWN"),
        purpose=r.get("purpose", ""),
        allow_volume_root=bool(r.get("allow_volume_root", False)),
        kinds=tuple(r.get("kinds", ("scan",)))) for r in flat.get("scan_root", []))
    scan = dict(flat.get("scan", {}))
    rename = dict(flat.get("rename", {}))
    machine = dict(flat.get("machine", {}))
    state_dir = core.norm(machine.get("state_dir", os.path.join(base_dir, ".state")))
    managed_root = core.norm(machine.get("managed_root",
                                         os.path.join(state_dir, "trash")))
    # Per-volume managed roots (TOML table: "C:" = path). The single managed_root
    # stays the default entry for its own volume, so existing behaviour is
    # unchanged unless a machine declares extra volumes explicitly.
    managed_roots = {str(k).strip().lower(): core.norm(v)
                     for k, v in dict(machine.get("managed_roots", {})).items()}
    managed_roots.setdefault(core.drive_of(managed_root).lower(), managed_root)
    for drive, mroot in managed_roots.items():
        if drive != core.drive_of(mroot).lower():
            raise PolicyError("managed_roots entry %r must live on its own volume "
                              "(%s is on %s)" % (drive, mroot, core.drive_of(mroot)))
    pol = Policy(
        source_files=sources, raw=flat, roots=roots,
        rules=tuple(flat.get("rules", [])),
        policy_id=str(flat.get("policy_id", "")),
        policy_version=str(flat.get("policy_version", "")),
        hostname=host,
        hard_skip=tuple(scan.get("hard_skip", [])),
        cloud_mount_names=tuple(scan.get("cloud_mount_names", [])),
        cloud_prefixes=tuple(core.norm(x) for x in scan.get("cloud_prefixes", [])),
        frozen_prefixes=tuple(core.norm(x) for x in scan.get("frozen_prefixes", [])),
        protected_prefixes=tuple(core.norm(x) for x in scan.get("protected_prefixes", [])),
        regenerable_prefixes=tuple(core.norm(x) for x in scan.get("regenerable_prefixes", [])),
        never_touch_ext=_folded(rename.get("never_touch_ext", [])),
        never_rename_ext=_folded(rename.get("never_rename_ext", [])),
        review_ext=_folded(rename.get("review_ext", [])),
        safe_ext=_folded(rename.get("safe_ext", [])),
        max_target_path=int(rename.get("max_target_path", core.MAX_TARGET_PATH)),
        max_actions_per_run=int(scan.get("max_actions_per_run", 2000)),
        state_dir=state_dir,
        managed_root=managed_root,
        managed_roots=managed_roots,
        journal_backup_dir=core.norm(machine.get("journal_backup_dir", "")) or "",
        python=str(machine.get("python", sys.executable)),
        min_python=tuple(machine.get("min_python", [3, 13])),
        strict_interpreter=bool(machine.get("strict_interpreter", True)),
        budget=dict(flat.get("budget", {})),
        scan=scan, rename=rename,
        tag=dict(flat.get("tag", {})),
        ai=dict(flat.get("ai", {})),
        evidence=dict(flat.get("evidence", {})),
        roles=dict(flat.get("role_allowed_toplevel", {})),
        housekeep=dict(flat.get("housekeep", {})),
        release=dict(flat.get("release", {})),
    )
    _build_lifecycle(pol, flat)
    validate(pol)
    return pol


def _build_lifecycle(pol: Policy, flat: dict) -> None:
    """Load [lifecycle] config + [[lifecycle_class]] tables + intake roots."""
    lc = dict(flat.get("lifecycle", {}))
    classes = []
    for raw in flat.get("lifecycle_class", []):
        unknown = set(raw) - LIFECYCLE_FIELDS
        if unknown:
            raise PolicyError("lifecycle_class %r has unknown fields %s"
                              % (raw.get("class_id"), sorted(unknown)))
        cid = raw.get("class_id")
        if cid not in LIFECYCLE_CLASSES:
            raise PolicyError("lifecycle_class bad class_id %r (must be %s)"
                              % (cid, "/".join(LIFECYCLE_CLASSES)))
        classes.append(LifecycleClass(
            class_id=cid,
            min_retention_days=int(raw.get("min_retention_days", 0)),
            archive=bool(raw.get("archive", False)),
            retire_via_trash=bool(raw.get("retire_via_trash", True)),
            google_drive=bool(raw.get("google_drive", False)),
            description=str(raw.get("description", "")),
        ))
    # A P0 permanent class may not be archived-with-retirement implicitly; the
    # floor is just a record. Duplicate class_id is a policy error.
    seen = set()
    for c in classes:
        if c.class_id in seen:
            raise PolicyError("duplicate lifecycle_class %s" % c.class_id)
        seen.add(c.class_id)
    pol.lifecycle = lc
    pol.lifecycle_classes = tuple(classes)
    intake = lc.get("external_intake_roots", [])
    pol.external_intake_roots = tuple(core.norm(x) for x in intake)


def validate(pol: Policy) -> None:
    for rule in pol.rules:
        rid = rule.get("rule_id")
        if not rid:
            raise PolicyError("rule without rule_id: %r" % (rule,))
        cls = rule.get("action_class")
        if cls not in core.RISK_CLASSES:
            raise PolicyError("rule '%s' bad action_class %r" % (rid, cls))
        for rx in ("path_regex", "name_regex"):
            if rule.get(rx) is not None:
                try:
                    re.compile(rule[rx])
                except re.error as exc:
                    raise PolicyError("rule '%s' bad %s: %s" % (rid, rx, exc)) from exc
        if cls == AUTO_SAFE and not rule.get("rank_only"):
            for field_name in ("min_age_days", "requires_no_reference",
                               "requires_regenerable"):
                if not rule.get(field_name):
                    raise PolicyError(
                        "rule '%s' cannot be AUTO_SAFE without %s (PRD I6: a name "
                        "alone is not evidence)" % (rid, field_name))
    ids = [r["rule_id"] for r in pol.rules]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise PolicyError("duplicate rule_id %s" % sorted(dupes))
    seen = set()
    for r in pol.roots:
        k = core.low(r.path)
        if k in seen:
            raise PolicyError("duplicate scan_root %s" % r.path)
        seen.add(k)


def check_interpreter(pol: Policy) -> str:
    """FR14: validate the configured interpreter before anything else runs."""
    if not pol.python or not os.path.isfile(pol.python):
        raise PolicyError("configured python not found: %r (MACHINE_OVERRIDE.python; "
                          "no PATH fallback is attempted)" % pol.python)
    if tuple(sys.version_info[:2]) < tuple(int(x) for x in pol.min_python):
        raise PolicyError("running %s is older than min_python %s"
                          % (sys.version.split()[0], ".".join(map(str, pol.min_python))))
    if pol.strict_interpreter and core.low(sys.executable) != core.low(pol.python):
        raise PolicyError("running interpreter %s != MACHINE_OVERRIDE.python %s "
                          "(set strict_interpreter=false to allow)"
                          % (sys.executable, pol.python))
    return pol.python
