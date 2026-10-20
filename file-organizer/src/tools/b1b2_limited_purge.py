"""B1/B2 limited purge executor (sanitized post-review revision).

This is the sanitized copy of the executor that ran the 2026-10-20 B1/B2 batch.
All machine-specific paths are derived from environment variables at runtime;
no user name, host name or absolute profile path is embedded here.

Safety invariants:
- Only whitelisted roots; every file's realpath must stay inside its root.
- Reparse points (junctions/symlinks) are never traversed and never deleted,
  including during empty-directory pruning.
- Regular files only. Directories are removed only with rmdir when empty.
- Per-file cap, total batch cap, and a minimum file age (active-write guard).
- Locked files are recorded as FAILED; the run continues.
- Updater roots fail closed: files are deleted only when the installed app
  version is >= the version staged in the updater directory. When either
  version is unknown, nothing under that root is touched.
- Every decision is journaled to JSONL next to the run summary.

Review fix (2026-10-20): the first version pruned empty directories with
os.walk + os.rmdir, which can remove a junction reparse point. This revision
re-checks the reparse attribute before rmdir and refuses. The pending-upgrade
gate is also new: v1 could delete a downloaded-but-not-installed update.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import stat
import sys
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
DEFAULT_PER_FILE_CAP = 600 * 1024 * 1024
DEFAULT_TOTAL_CAP = 6 * 1024 * 1024 * 1024
DEFAULT_MIN_AGE_SECONDS = 600

# Registry display names used to resolve the installed version of apps that
# stage updates in LOCALAPPDATA. Missing entries fail closed.
APP_DISPLAY_NAMES = {
    "C07_raccoon_updater": "Raccoon AI",
    "C08_cherry_updater": "Cherry Studio",
    "C09_deepseek_updater": "DeepSeek Harness",
    "C10_nanomuse_updater": "nanoMuse",
}


@dataclass(frozen=True)
class Root:
    root_id: str
    path: str
    only_suffix: Optional[str] = None
    requires_completed_upgrade: bool = False


@dataclass(frozen=True)
class Limits:
    per_file_cap: int = DEFAULT_PER_FILE_CAP
    total_cap: int = DEFAULT_TOTAL_CAP
    min_age_seconds: int = DEFAULT_MIN_AGE_SECONDS


def default_roots(local: Optional[str] = None) -> list[Root]:
    local = local or os.environ.get("LOCALAPPDATA", "")
    return [
        Root("C01_chrome_cache", os.path.join(local, "Google", "Chrome", "User Data", "Default", "Cache")),
        Root("C01_chrome_codecache", os.path.join(local, "Google", "Chrome", "User Data", "Default", "Code Cache")),
        # Only the abandoned .partial bundle, never sibling adopted bundles.
        Root("C06_claude_partial",
             os.path.join(local, "Claude-3p", "vm_bundles", "claudevm.bundle"),
             only_suffix=".partial"),
        Root("C07_raccoon_updater", os.path.join(local, "raccoon-ai-updater"),
             requires_completed_upgrade=True),
        Root("C08_cherry_updater", os.path.join(local, "cherrystudio-updater"),
             requires_completed_upgrade=True),
        Root("C09_deepseek_updater", os.path.join(local, "@deepseek-aidsh-desktop-updater"),
             requires_completed_upgrade=True),
        Root("C10_nanomuse_updater", os.path.join(local, "nanomuse-desktop-updater"),
             requires_completed_upgrade=True),
    ]


def is_reparse(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def volume(drive: str) -> dict:
    free = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    ctypes.windll.kernel32.GetDiskFreeSpaceExW(
        ctypes.c_wchar_p(drive), ctypes.byref(free), ctypes.byref(total), None
    )
    return {"total": int(total.value), "free": int(free.value),
            "used": int(total.value) - int(free.value)}


def iter_files(root: str, only_suffix: Optional[str] = None) -> Iterable[tuple[str, int]]:
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            st = os.stat(cur, follow_symlinks=False)
        except OSError:
            continue
        if is_reparse(st):
            continue
        if stat.S_ISDIR(st.st_mode):
            try:
                with os.scandir(cur) as it:
                    for entry in it:
                        try:
                            est = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if is_reparse(est):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            if only_suffix and not entry.name.endswith(only_suffix):
                                continue
                            yield entry.path, int(est.st_size)
            except OSError:
                continue
        elif stat.S_ISREG(st.st_mode):
            if only_suffix and not os.path.basename(cur).endswith(only_suffix):
                continue
            yield cur, int(st.st_size)


def prune_empty_dirs(root: str) -> int:
    root_real = os.path.realpath(root)
    removed = 0
    for cur, _dirs, _files in os.walk(root, topdown=False):
        if os.path.realpath(cur) == root_real:
            continue
        try:
            st = os.lstat(cur)
        except OSError:
            continue
        if is_reparse(st) or not stat.S_ISDIR(st.st_mode):
            continue
        try:
            os.rmdir(cur)
            removed += 1
        except OSError:
            pass
    return removed


def read_staged_version(root: str) -> Optional[str]:
    """Version of the update staged in an electron-updater directory."""
    info = os.path.join(root, "pending", "update-info.json")
    try:
        with open(info, encoding="utf-8") as fh:
            version = json.load(fh).get("version")
        if version:
            return str(version).strip()
    except (OSError, ValueError):
        pass
    pending_dir = os.path.join(root, "pending")
    try:
        names = os.listdir(pending_dir)
    except OSError:
        return None
    best = None
    for name in names:
        match = re.search(r"(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)", name)
        if match and (best is None or version_key(match.group(1)) > version_key(best)):
            best = match.group(1)
    return best


def version_key(text: str) -> tuple:
    core, _, pre = text.partition("-")
    nums = [int(x) for x in re.findall(r"\d+", core)][:3]
    while len(nums) < 3:
        nums.append(0)
    pre_parts = tuple(re.findall(r"[\w]+", pre)) if pre else ()
    return tuple(nums), pre_parts


def version_at_least(installed: str, staged: str) -> bool:
    try:
        ins, pre_i = version_key(installed)
        sty, pre_s = version_key(staged)
    except ValueError:
        return False
    if ins != sty:
        return ins > sty
    if not pre_s:
        return not pre_i
    if not pre_i:
        return True
    return pre_i >= pre_s


def installed_version(root_id: str) -> Optional[str]:
    """Installed version from the Windows uninstall registry, highest match."""
    try:
        import winreg
    except ImportError:
        return None
    target = APP_DISPLAY_NAMES.get(root_id)
    if not target:
        return None
    found = []
    views = [(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_READ)]
    for hive in (winreg.HKEY_LOCAL_MACHINE,):
        for flag in (winreg.KEY_READ | winreg.KEY_WOW64_64KEY, winreg.KEY_READ | winreg.KEY_WOW64_32KEY):
            views.append((hive, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", flag))
    for hive, subkey, access in views:
        try:
            with winreg.OpenKey(hive, subkey, 0, access) as key:
                for index in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        name = winreg.EnumKey(key, index)
                        with winreg.OpenKey(key, name) as entry:
                            display = winreg.QueryValueEx(entry, "DisplayName")[0]
                            version = winreg.QueryValueEx(entry, "DisplayVersion")[0]
                    except OSError:
                        continue
                    if str(display).strip().lower().startswith(target.lower()):
                        found.append(str(version).strip())
        except OSError:
            continue
    if not found:
        return None
    return max(found, key=version_key)


def upgrade_gate(root_id: str, root: str,
                 probe: Callable[[str], Optional[str]] = installed_version) -> tuple[bool, str, dict]:
    staged = read_staged_version(root)
    info = {"staged_version": staged}
    if staged is None:
        return False, "STAGED_VERSION_UNKNOWN", info
    installed = probe(root_id)
    info["installed_version"] = installed
    if installed is None:
        return False, "INSTALLED_VERSION_UNKNOWN", info
    if not version_at_least(installed, staged):
        return False, "UPGRADE_NOT_INSTALLED", info
    return True, "OK", info


def containment_ok(root_real: str, path: str) -> bool:
    try:
        return os.path.commonpath([root_real, os.path.realpath(path)]) == root_real
    except ValueError:
        return False


def safe_to_delete(root_real: str, path: str, limits: Limits) -> tuple[bool, str]:
    try:
        st = os.lstat(path)
    except OSError:
        return False, "STAT_FAILED"
    if is_reparse(st):
        return False, "REPARSE"
    if not stat.S_ISREG(st.st_mode):
        return False, "NOT_REGULAR"
    if not containment_ok(root_real, path):
        return False, "ESCAPE"
    if int(st.st_size) > limits.per_file_cap:
        return False, "OVER_CAP"
    return True, "OK"


def run(roots: list[Root], run_dir: str, dry_run: bool = False,
        limits: Limits = Limits(),
        probe: Callable[[str], Optional[str]] = installed_version,
        now: Optional[float] = None) -> dict:
    os.makedirs(run_dir, exist_ok=True)
    journal_name = "journal_dryrun.jsonl" if dry_run else "journal.jsonl"
    summary = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dry_run": dry_run,
        "limits": {"per_file_cap": limits.per_file_cap,
                   "total_cap": limits.total_cap,
                   "min_age_seconds": limits.min_age_seconds},
        "roots": {},
        "deleted_files": 0,
        "deleted_bytes": 0,
        "failed_files": 0,
        "skipped_files": 0,
        "gate_refused_roots": 0,
        "removed_empty_dirs": 0,
    }
    summary["volumes_before"] = {"C": volume("C:\\"), "D": volume("D:\\")}
    now = time.time() if now is None else now
    total = 0

    with open(os.path.join(run_dir, journal_name), "w", encoding="utf-8") as journal:
        for root in roots:
            if not os.path.isdir(root.path) or is_reparse(os.lstat(root.path)):
                summary["roots"][root.root_id] = {"status": "MISSING_OR_REPARSE"}
                continue
            root_real = os.path.realpath(root.path)
            stats = {"status": "OK", "deleted": 0, "bytes": 0,
                     "failed": 0, "skipped": 0, "removed_empty_dirs": 0}

            if root.requires_completed_upgrade:
                allowed, reason, info = upgrade_gate(root.root_id, root.path, probe)
                stats.update(info)
                if not allowed:
                    stats["status"] = "REFUSED_PENDING_UPGRADE"
                    stats["reason"] = reason
                    summary["roots"][root.root_id] = stats
                    summary["gate_refused_roots"] += 1
                    continue

            for path, size in iter_files(root.path, root.only_suffix):
                record = {"root": root.root_id, "path": path, "bytes": size}

                def skip(code: str) -> None:
                    record["result"] = code
                    stats["skipped"] += 1
                    summary["skipped_files"] += 1
                    journal.write(json.dumps(record, ensure_ascii=False) + "\n")

                if not containment_ok(root_real, path):
                    skip("SKIP_ESCAPE")
                    continue
                try:
                    age = now - os.stat(path, follow_symlinks=False).st_mtime
                except OSError:
                    age = limits.min_age_seconds + 1
                if age < limits.min_age_seconds:
                    skip("SKIP_RECENT")
                    continue
                if size > limits.per_file_cap:
                    skip("SKIP_OVER_CAP")
                    continue
                if total + size > limits.total_cap:
                    skip("SKIP_TOTAL_CAP")
                    continue
                ok, why = safe_to_delete(root_real, path, limits)
                if not ok:
                    skip("SKIP_" + why)
                    continue
                try:
                    if not dry_run:
                        os.remove(path)
                except OSError as exc:
                    record["result"] = "FAILED"
                    record["error"] = "%s: %s" % (type(exc).__name__, exc)
                    stats["failed"] += 1
                    summary["failed_files"] += 1
                else:
                    record["result"] = "WOULD_DELETE" if dry_run else "DELETED"
                    stats["deleted"] += 1
                    stats["bytes"] += size
                    summary["deleted_files"] += 1
                    summary["deleted_bytes"] += size
                    total += size
                journal.write(json.dumps(record, ensure_ascii=False) + "\n")

            if not dry_run:
                stats["removed_empty_dirs"] = prune_empty_dirs(root.path)
            summary["removed_empty_dirs"] += stats["removed_empty_dirs"]
            summary["roots"][root.root_id] = stats

    summary["volumes_after"] = {"C": volume("C:\\"), "D": volume("D:\\")}
    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    out_name = "summary_dryrun.json" if dry_run else "summary.json"
    with open(os.path.join(run_dir, out_name), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    return summary


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="B1/B2 limited purge executor")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-dir", default=os.path.join(os.getcwd(), ".state", "exec", "b1b2"))
    parser.add_argument("--per-file-cap", type=int, default=DEFAULT_PER_FILE_CAP)
    parser.add_argument("--total-cap", type=int, default=DEFAULT_TOTAL_CAP)
    parser.add_argument("--min-age-seconds", type=int, default=DEFAULT_MIN_AGE_SECONDS)
    args = parser.parse_args(argv)
    summary = run(
        default_roots(), args.run_dir, args.dry_run,
        Limits(args.per_file_cap, args.total_cap, args.min_age_seconds),
    )
    printable = {k: v for k, v in summary.items() if k != "roots"}
    print(json.dumps(printable, indent=2))
    print(json.dumps(summary["roots"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
