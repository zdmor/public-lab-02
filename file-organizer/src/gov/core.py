"""Path and safety primitives, budgets, record structure.

Layer A only: nothing in this module performs a write side effect on a
scanned root (PRD I2/I3, DESIGN 3.1/3.2).
"""
from __future__ import annotations

import hashlib
import os
import platform
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Iterator, Optional, Sequence

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
MAX_TARGET_PATH = 240
BUDGET_CHECK_EVERY = 512

RISK_CLASSES = ("AUTO_SAFE", "REVIEW_REQUIRED", "PROTECTED", "NEVER_TOUCH")
RENAME_TIERS = ("FILENAME_TAG_SAFE", "TAG_WITH_REVIEW", "NEVER_RENAME")
PRUNE_REASONS = ("hard_skip", "cloud_mount", "reparse", "frozen_zone")


def machine_name() -> str:
    return platform.node() or "UNKNOWN-HOST"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def now_ts() -> float:
    return time.time()


def date_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def norm(path: str) -> str:
    """Absolute, normalized, separator-consistent path (case preserved)."""
    return os.path.normpath(os.path.abspath(os.fspath(path)))


def low(path: str) -> str:
    return norm(path).casefold()


def drive_of(path: str) -> str:
    return os.path.splitdrive(norm(path))[0].upper()


def ext_of(name: str) -> str:
    base, e = os.path.splitext(name)
    return e[1:].casefold() if e else ""


def prefixed(path_low: str, pref_low: str) -> bool:
    """`path_low` is the prefix itself or lives under it (separator boundary).

    Handles both "D:\\proj" and "D:\\" style prefixes, and never lets 'C:ab'
    match a 'C:a' prefix.
    """
    if not pref_low:
        return False
    if path_low == pref_low:
        return True
    base = pref_low if pref_low.endswith(os.sep) else pref_low + os.sep
    return path_low.startswith(base)


def is_cloud_prefix(abspath: str, prefixes: Sequence[str]) -> bool:
    p = low(abspath)
    return any(prefixed(p, pref) for pref in prefixes)


def under_prefix(abspath: str, prefixes: Sequence[str]) -> bool:
    """Path-equality-aware prefix test (never 'C:ab' matching 'C:a')."""
    p = low(abspath)
    return any(prefixed(p, pref) for pref in prefixes)


def read_reparse_target(abspath: str) -> Optional[str]:
    try:
        t = os.readlink(abspath)
    except OSError:
        return None
    if not os.path.isabs(t):
        t = os.path.normpath(os.path.join(os.path.dirname(abspath), t))
    return t


def lstat_attrs(abspath: str) -> int:
    try:
        st = os.lstat(abspath)
    except OSError:
        return 0
    return int(getattr(st, "st_file_attributes", 0))


def is_reparse(abspath: str) -> bool:
    return bool(lstat_attrs(abspath) & FILE_ATTRIBUTE_REPARSE_POINT)


def sha256_file(path: str, max_bytes: int = 4 * 1024 ** 3) -> Optional[str]:
    h = hashlib.sha256()
    read = 0
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 256)
                if not chunk:
                    break
                read += len(chunk)
                if read > max_bytes:
                    return None
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def cache_key(path: str, size: int, mtime: float) -> str:
    return hashlib.sha256(
        ("%s|%s|%s" % (low(path), size, int(mtime))).encode("utf-8", "replace")
    ).hexdigest()


def sanitize_path_for_ai(path: str, keep: int = 2) -> str:
    """Keep at most `keep` trailing paths components; drop drive and user dirs."""
    parts = [p for p in norm(path).replace("/", "\\").split("\\") if p]
    tail = parts[-keep:] if keep > 0 else []
    return "/".join(tail) if tail else "<path>"


@dataclass
class Budget:
    """Wall/file/byte ceilings; hitting one stops the run and is recorded."""

    max_wall_s: float = 540.0
    max_files: int = 2_500_000
    max_bytes: int = 200 * 1024 ** 3
    started_mono: float = field(default_factory=time.monotonic, repr=False)
    files: int = 0
    bytes_total: int = 0
    hits: list = field(default_factory=list)

    def add(self, size: int) -> None:
        self.files += 1
        self.bytes_total += int(size)

    def hit(self) -> Optional[str]:
        if self.files > self.max_files:
            return "max_files"
        if self.bytes_total > self.max_bytes:
            return "max_bytes"
        if (time.monotonic() - self.started_mono) > self.max_wall_s:
            return "max_wall_s"
        return None

    @property
    def wall_s(self) -> float:
        return time.monotonic() - self.started_mono

    def as_dict(self) -> dict:
        return {
            "max_wall_s": self.max_wall_s,
            "max_files": self.max_files,
            "max_bytes": self.max_bytes,
            "files": self.files,
            "bytes": self.bytes_total,
            "wall_s": round(self.wall_s, 3),
            "hits": list(self.hits),
        }


@dataclass
class ScanRules:
    """Everything prune_reason() needs; config.Policy satisfies this shape."""

    hard_skip_folded: frozenset = frozenset()
    cloud_roots_folded: frozenset = frozenset()
    cloud_prefixes_low: tuple = ()
    frozen_prefixes_low: tuple = ()
    # Managed roots (managed trash, per-volume) hold files this tool moved
    # there itself. Counting them again as user data would double-report the
    # very bytes we are tracking, and on a DATA volume the trash subtree is
    # big enough to distort the growth/accumulation numbers the weekly reads.
    managed_prefixes_low: tuple = ()


def prune_reason(name: str, abspath: str, attrs: int,
                 rules: ScanRules) -> Optional[str]:
    """Single-pass, fixed-order prune decision (DESIGN 3.1).

    Returning a reason means: record the entry, then never descend into it.
    """
    n = name.casefold()
    if n in rules.hard_skip_folded:
        return "hard_skip"
    if under_prefix(abspath, rules.managed_prefixes_low):
        return "managed_root"
    if is_cloud_prefix(abspath, rules.cloud_prefixes_low):
        return "cloud_mount"
    if n in rules.cloud_roots_folded:
        return "cloud_mount"
    if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
        return "reparse"
    if under_prefix(abspath, rules.frozen_prefixes_low):
        return "frozen_zone"
    return None


@dataclass
class Record:
    """One inventory line (PRD section 7 data model)."""

    scan_id: str
    machine: str
    ts: str
    path: str
    parent: str
    name: str
    ext: str
    size: int
    mtime: float
    ctime: float
    attrs_hex: str
    is_reparse: bool
    reparse_tag: str
    target: Optional[str]
    zone_role: str
    root: str
    is_dir: bool = False
    rule_ids: list = field(default_factory=list)
    risk_class: str = ""

    @classmethod
    def from_entry(cls, *, scan_id, machine, ts, abspath, root, zone_role,
                   st, name, is_dir, reparse_tag="", target=None) -> "Record":
        attrs = int(getattr(st, "st_file_attributes", 0))
        return cls(
            scan_id=scan_id, machine=machine, ts=ts, path=norm(abspath),
            parent=norm(os.path.dirname(abspath)), name=name,
            ext="" if is_dir else ext_of(name),
            size=int(getattr(st, "st_size", 0) or 0),
            mtime=float(getattr(st, "st_mtime", 0) or 0),
            ctime=float(getattr(st, "st_ctime", 0) or 0),
            attrs_hex="0x%08x" % attrs,
            is_reparse=bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT),
            reparse_tag=reparse_tag, target=target,
            zone_role=zone_role, root=root, is_dir=is_dir,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def age_days(self) -> float:
        if not self.mtime:
            return 0.0
        return max(0.0, (time.time() - self.mtime) / 86400.0)


def iter_ndjson(path: str, *, on_bad: Optional[Any] = None) -> Iterator[dict]:
    """Yield decoded lines.

    A line that cannot be decoded is a torn write (crash residue). Reporting it
    through `on_bad` and skipping keeps one damaged line from bricking every
    reader at once (health, report, undo) - PRD I5 wants residue surfaced, not
    a second failure.
    """
    import json as _json
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = _json.loads(line)
            except ValueError:
                if on_bad is not None:
                    on_bad(line)
                continue
            if isinstance(row, dict):
                yield row


def append_ndjson(path: str, obj: Any) -> None:
    """Append one line to a state-dir file. Creates parents; never rewrites."""
    append_ndjson_many(path, (obj,))


def append_ndjson_many(path: str, objs: Iterable[Any]) -> int:
    """Append many lines with one open handle (a scan can emit 10^6 records)."""
    import json as _json
    os.makedirs(os.path.dirname(norm(path)), exist_ok=True)
    n = 0
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for obj in objs:
            fh.write(_json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
            fh.write("\n")
            n += 1
    return n


def human_gb(nbytes: float) -> str:
    return "%.3f GB" % (float(nbytes) / 1024.0 ** 3)


def check_long_path(path: str, limit: int = MAX_TARGET_PATH) -> Optional[str]:
    """Fail-closed length gate: returns a reason string, never truncates."""
    n = len(norm(path))
    if n > limit:
        return "too_long:%d>%d" % (n, limit)
    return None
