"""Layer A: runtime evidence in one process, plus the reference index.

The PowerShell here-string is deliberately pure ASCII (it runs on both
machines). Only the state dir is written; no scanned root is touched (PRD I2).
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from . import core, inventory

PS_TEMPLATE = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
function Emit($kind, $value) { if ($value) { Write-Output ($kind + "`t" + $value) } }
Get-CimInstance Win32_Service | ForEach-Object { Emit 'svc' $_.PathName }
Get-CimInstance Win32_Process | ForEach-Object { Emit 'proc' $_.ExecutablePath }
Get-ScheduledTask | ForEach-Object {
  $task = $_
  $task.Actions | ForEach-Object {
    Emit 'task' $_.Execute
    Emit 'task' $_.Arguments
    Emit 'taskwd' $_.WorkingDirectory
  }
}
$uninstallKeys = @(
  'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
  'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
  'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
  'HKCU:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
)
foreach ($k in $uninstallKeys) {
  Get-ChildItem $k | ForEach-Object {
    $p = Get-ItemProperty $_.PSPath
    foreach ($f in @('InstallLocation','DisplayIcon','UninstallString','ModifyPath')) {
      Emit 'uninst' $p.$f
    }
  }
}
$runKeys = @(
  'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run',
  'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce',
  'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run',
  'HKLM:\Software\Microsoft\Windows\CurrentVersion\RunOnce'
)
foreach ($k in $runKeys) {
  if (Test-Path $k) {
    $item = Get-Item $k
    foreach ($name in $item.Property) { Emit 'run' ($item.GetValue($name)) }
  }
}
foreach ($d in @([Environment]::GetFolderPath('Startup'), [Environment]::GetFolderPath('CommonStartup'))) {
  Get-ChildItem -LiteralPath $d -Force | ForEach-Object { Emit 'startup' $_.FullName }
}
Emit 'path' [Environment]::GetEnvironmentVariable('Path', 'Machine')
Emit 'path' [Environment]::GetEnvironmentVariable('Path', 'User')
if (Test-Path 'HKCU:\Environment') {
  $envItem = Get-Item 'HKCU:\Environment'
  foreach ($name in $envItem.Property) { Emit 'env' ($envItem.GetValue($name)) }
}
""".strip()

QUOTED = re.compile(r'"([^"]+)"')
TOKEN_SPLIT = re.compile(r"[\s,;]+")
EXE_LIKE = re.compile(r"\.(exe|cmd|bat|ps1|msi|com|vbs)$", re.I)


def extract_paths(value: str) -> list[str]:
    """Pull plausible filesystem paths out of a registry / task value."""
    if not value:
        return []
    out: list[str] = []
    for cand in QUOTED.findall(value) or TOKEN_SPLIT.split(value.strip()):
        c = (cand or "").strip().strip('"').strip("'")
        if len(c) < 3:
            continue
        if c.startswith("\\\\") or re.match(r"^[A-Za-z]:[\\/]", c):
            out.append(c)
    return out


@dataclass
class EvidenceRow:
    kind: str
    raw: str
    paths: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "raw": self.raw, "paths": self.paths}


def collect(state_dir: str, *, reparse_roots: Sequence[str] = (),
            reparse_depth: int = 4, timeout_s: int = 240,
            powershell: str = "powershell.exe",
            rules=None) -> tuple[list[EvidenceRow], dict]:
    """Run one PowerShell process and persist its output as NDJSON."""
    started = time.monotonic()
    script_dir = os.path.join(core.norm(state_dir), "evidence")
    os.makedirs(script_dir, exist_ok=True)
    # fixed name: the script is rewritten in place, so a run cannot litter the
    # state dir with one collect_<pid>.ps1 each time (PRD I1 forbids deleting)
    script = os.path.join(script_dir, "collect.ps1")
    with open(script, "w", encoding="ascii", newline="\r\n") as fh:
        fh.write(PS_TEMPLATE)
    env = dict(os.environ)
    meta = {"script": script, "exit": None, "error": "", "rows": 0,
            "reparse_roots": [core.norm(r) for r in reparse_roots],
            "wall_s": 0.0}
    try:
        proc = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_s, env=env)
        meta["exit"] = proc.returncode
        meta["stderr"] = (proc.stderr or "")[:500]
        lines = (proc.stdout or "").splitlines()
    except subprocess.TimeoutExpired:
        meta["error"] = "timeout:%ds" % timeout_s
        lines = []
    except OSError as exc:
        meta["error"] = "%s: %s" % (type(exc).__name__, exc)
        lines = []
    rows = parse_output(lines)
    rows.extend(reparse_rows(reparse_roots, reparse_depth=reparse_depth,
                             rules=rules, meta=meta, budget_s=timeout_s))
    meta["rows"] = len(rows)
    meta["wall_s"] = round(time.monotonic() - started, 3)
    meta["counts"] = counts(rows)
    return rows, meta


def reparse_rows(roots: Sequence[str], *, reparse_depth: int = 4, rules=None,
                 meta: Optional[dict] = None,
                 budget_s: float = 240.0) -> list[EvidenceRow]:
    """Enumerate reparse points with the inventory walk, not with PowerShell.

    `Get-ChildItem -Recurse` follows junctions on Windows PowerShell 5.1, which
    can pull a cloud mount or a link target tree into a read-only scan. The walk
    records a reparse point and never descends through it (PRD I2, AC3).
    """
    meta = meta if meta is not None else {}
    if not roots:
        meta["reparse_skipped"] = "no roots configured"
        return []
    if rules is None:
        meta["reparse_skipped"] = "no scan rules passed"
        return []
    stats = inventory.ScanStats()
    budget = core.Budget(max_wall_s=min(float(budget_s), 600.0))
    for r in roots:
        inventory.walk(r, rules=rules, budget=budget, stats=stats,
                       max_depth=int(reparse_depth or 0))
    meta["reparse_scan"] = {"dirs_visited": stats.dirs_visited,
                            "files_seen": stats.files, "prunes": dict(stats.prunes),
                            "budget_hit": stats.budget_hit,
                            "wall_s": round(budget.wall_s, 3)}
    return [EvidenceRow(kind="reparse", raw=p[0], paths=[p[0]])
            for p in stats.reparse]


def parse_output(lines: Iterable[str]) -> list[EvidenceRow]:
    rows = []
    for line in lines:
        if not line.strip():
            continue
        kind, _, raw = line.partition("\t")
        raw = (raw or "").strip()
        if not raw:
            continue
        if kind == "path":
            paths = [p for p in re.split(r"[;]", raw) if p.strip()]
        else:
            paths = extract_paths(raw)
        rows.append(EvidenceRow(kind=kind.strip(), raw=raw, paths=paths))
    return rows


def counts(rows: Sequence[EvidenceRow]) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        out[r.kind] = out.get(r.kind, 0) + 1
    return out


def save(state_dir: str, rows: Sequence[EvidenceRow], meta: dict) -> str:
    path = os.path.join(core.norm(state_dir), "evidence",
                        "%s-%s.ndjson" % (core.machine_name(),
                                          time.strftime("%Y%m%d")))
    core.append_ndjson(path, {"kind": "header", "meta": meta,
                              "machine": core.machine_name(), "ts": core.now_iso()})
    core.append_ndjson_many(path, (r.as_dict() for r in rows))
    return path


def load(state_dir: str, machine: Optional[str] = None) -> Optional[str]:
    ed = os.path.join(core.norm(state_dir), "evidence")
    if not os.path.isdir(ed):
        return None
    host = machine or core.machine_name()
    names = sorted(n for n in os.listdir(ed)
                   if n.startswith(host + "-") and n.endswith(".ndjson"))
    return os.path.join(ed, names[-1]) if names else None


@dataclass
class ReferencedIndex:
    """Directories and files named by runtime evidence (PRD FR4)."""

    files: dict = field(default_factory=dict)      # low path -> kinds
    prefixes: dict = field(default_factory=dict)    # low path -> kinds

    @classmethod
    def from_rows(cls, rows: Sequence[EvidenceRow], *, min_prefix_parts: int = 2,
                  ) -> "ReferencedIndex":
        idx = cls()
        for row in rows:
            for raw in row.paths:
                try:
                    p = core.norm(raw)
                except (OSError, ValueError):
                    continue
                if EXE_LIKE.search(p):
                    idx.add_file(p, row.kind)
                    parent = core.norm(os.path.dirname(p))
                    idx.add_prefix(parent, row.kind, min_prefix_parts)
                elif os.path.isdir(p) or p.endswith(("\\", ":")):
                    idx.add_prefix(p, row.kind, min_prefix_parts)
                else:
                    idx.add_prefix(core.norm(os.path.dirname(p)), row.kind,
                                   min_prefix_parts)
                    idx.add_file(p, row.kind)
        return idx

    def add_file(self, path: str, kind: str) -> None:
        self.files.setdefault(core.low(path), set()).add(kind)

    def add_prefix(self, path: str, kind: str, min_parts: int) -> None:
        p = core.norm(path)
        if len(p.rstrip("\\").split("\\")) - 1 < min_parts:
            return  # never let 'C:\' mark the whole volume as referenced
        self.prefixes.setdefault(core.low(p), set()).add(kind)

    def lookup_kinds(self, path: str) -> tuple[list[str], list[str]]:
        """(exact-file kinds, directory-prefix kinds) referencing this path.

        Split in two because a prefix hit is much weaker evidence than the file
        being named outright: an `env` or `uninst` row often points at a whole
        container (C:\\Users\\<name>\\AppData\\Local), which would otherwise mark
        every file under it as referenced.
        """
        p = core.norm(path)
        files: set[str] = set(self.files.get(p.casefold(), ()))
        prefixes: set[str] = set()
        cur = os.path.dirname(p)
        while True:
            k = cur.casefold()
            if k in self.prefixes:
                prefixes |= self.prefixes[k]
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        return sorted(files), sorted(prefixes)

    def lookup(self, path: str) -> list[str]:
        """Kinds that reference this exact path or a referencing directory."""
        files, prefixes = self.lookup_kinds(path)
        return sorted(set(files) | set(prefixes))

    def is_referenced(self, path: str) -> bool:
        return bool(self.lookup(path))

    def stats(self) -> dict:
        kinds: dict[str, int] = {}
        for d in (self.files, self.prefixes):
            for s in d.values():
                for k in s:
                    kinds[k] = kinds.get(k, 0) + 1
        return {"files": len(self.files), "prefixes": len(self.prefixes),
                "by_kind": kinds}
