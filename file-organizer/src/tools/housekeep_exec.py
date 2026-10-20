"""Execute a housekeep plan. Deliberately OUTSIDE gov/ (PRD I1).

The governed package owns no delete primitive, so the only component allowed to
gzip-and-remove its own state files lives here, exactly like
``tools/release_exec.py``. It reads the plan produced by ``gov.cli housekeep``
(dry-run) and applies it, but ONLY under ``<state_dir>`` and ONLY for the
managed subdirs (inventory/snapshot/candidates/evidence).

v2 (2026-10-19, after the P0 code review) fixes the accounting: v1 counted the
whole original file as "freed" for a gzip action without deducting the .gz
product, so a 1 GB snapshot that compresses to 300 MB was reported as 1 GB
released when only 700 MB actually left the volume. v2 reports
``raw_bytes_removed`` / ``gz_bytes_created`` / ``net_bytes_delta`` plus the
volume's free bytes before and after, and writes a machine-readable summary.

v3 (2026-10-19, PHASE_SCAN_BASELINE phase decision) adds two gates:
  - MUTEX: an exclusive lock file (.state/housekeep/LOCK, pid+timestamp).
    The 17:05-17:17 incident showed an unattributed concurrent housekeep
    shrinking .state beyond what any journaled run could prove; two writers
    gzipping the same tree can corrupt accounting (and delete a file another
    run already counted). A live lock refuses the run (exit 3); a stale lock
    (dead pid) is stolen after one retry.
  - READ-ONLY PHASE GATE: while .state/PHASE_SCAN_BASELINE exists, this tool
    refuses ALL write actions even with --approve (exit 4) and prints the
    plan instead. The phase decision froze automatic deletion; the daily
    scheduler points at tools/daily_readonly.py, which only dry-runs.

Usage:
    python tools/housekeep_exec.py --approve [--keep N] [--retention-days D]
Exit codes: 0 ok | 1 action failures | 2 dry-run requested | 3 lock held
            4 read-only phase refusal
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, os.pardir))
sys.path.insert(0, REPO_ROOT)

from gov import config, core, housekeep  # noqa: E402

MANAGED = set(housekeep.MANAGED_DIRS)
LOCK = os.path.join(REPO_ROOT, ".state", "housekeep", "LOCK")
PHASE_MARKER = os.path.join(REPO_ROOT, ".state", "PHASE_SCAN_BASELINE")


def _pid_alive(pid: int) -> bool:
    """True if a process with this pid exists (Windows, same session)."""
    if pid <= 0:
        return False
    try:
        import ctypes
        k = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k.OpenProcess.restype = ctypes.c_void_p
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if h:
            k.CloseHandle(ctypes.c_void_p(h))
            return True
        return False
    except Exception:
        return False


def acquire_lock() -> str:
    """Take the exclusive housekeep lock. '' on success, reason on refusal."""
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("%d\n%s\n" % (os.getpid(), core.now_iso()))
            return ""
        except FileExistsError:
            try:
                with open(LOCK, encoding="utf-8") as f:
                    pid = int(f.readline().strip())
            except Exception:
                pid = -1
            if _pid_alive(pid):
                return "another housekeep holds the lock (pid %d)" % pid
            try:
                os.remove(LOCK)  # stale lock from a dead process
            except OSError:
                pass
    return "lock contention"


def release_lock() -> None:
    try:
        os.remove(LOCK)
    except OSError:
        pass


def _inside_state(path: str, state_dir: str) -> bool:
    p = core.low(path)
    base = core.low(state_dir)
    if not core.prefixed(p, base):
        return False
    # must be directly under one of the managed subdirs
    rel = os.path.relpath(core.norm(path), core.norm(state_dir))
    top = rel.split(os.sep)[0]
    return top in MANAGED


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--keep") and not a.startswith("--retention")]
    approve = "--approve" in args
    keep = 5
    retention = 180.0
    json_out = ""
    for i, a in enumerate(argv):
        if a == "--keep" and i + 1 < len(argv):
            keep = int(argv[i + 1])
        if a == "--retention-days" and i + 1 < len(argv):
            retention = float(argv[i + 1])
        if a == "--json" and i + 1 < len(argv):
            json_out = argv[i + 1]

    reason = acquire_lock()
    if reason:
        print("REFUSED (mutex): %s" % reason)
        return 3
    try:
        pol = config.load(REPO_ROOT)
        hp = housekeep.plan(pol, keep=keep, retention_days=retention)

        # read-only phase gate: the PHASE_SCAN_BASELINE decision froze every
        # automatic write/delete; --approve cannot override it.
        phase_active = os.path.exists(PHASE_MARKER)
        if phase_active and approve:
            print("REFUSED (read-only phase): .state/PHASE_SCAN_BASELINE is "
                  "active -- gzip/delete frozen, --approve cannot override")
            return 4
        if phase_active:
            print("PHASE_SCAN_BASELINE active: read-only phase, dry-run only")

        if not approve:
            print("dry-run: add --approve to actually gzip/delete")
            print("plan: actions=%d would_compress=%d would_delete=%d "
                  "estimated_free=%s"
                  % (len(hp.actions), hp.would_compress, hp.would_delete,
                     core.human_gb(hp.estimated_free)))
            for act in hp.actions[:20]:
                print("  [%s] %s (%s)" % (act["action"], act["path"],
                                          act.get("reason", "")))
            if len(hp.actions) > 20:
                print("  ... %d more" % (len(hp.actions) - 20))
            return 2

        vol = os.path.splitdrive(core.norm(pol.state_dir))[0] + os.sep
        free_before = shutil.disk_usage(vol).free

        done = 0
        raw_removed = 0          # bytes of originals that left the volume
        gz_created = 0           # bytes of .gz products that stay
        failures = []
        for act in hp.actions:
            p = act["path"]
            if not _inside_state(p, pol.state_dir):
                print("REFUSED (outside state dir): %s" % p)
                return 2
            if act["action"] == "delete":
                try:
                    if os.path.exists(p):
                        sz = os.path.getsize(p)
                        os.remove(p)
                        raw_removed += sz
                        done += 1
                except OSError as exc:
                    print("! %s (%s)" % (p, exc))
                    failures.append({"path": p, "error": str(exc)})
            elif act["action"] == "gzip":
                gz = p + ".gz"
                try:
                    if os.path.exists(gz):
                        sz = os.path.getsize(p)
                        os.remove(p)
                        raw_removed += sz
                        # a stale .gz from an interrupted run stays; count it so
                        # the net number is honest about what still occupies disk
                        gz_created += os.path.getsize(gz)
                    else:
                        sz = os.path.getsize(p)
                        with open(p, "rb") as fin, gzip.open(gz, "wb") as fout:
                            shutil.copyfileobj(fin, fout, length=1 << 20)
                        os.remove(p)
                        raw_removed += sz
                        gz_created += os.path.getsize(gz)
                    done += 1
                except OSError as exc:
                    print("! %s (%s)" % (p, exc))
                    failures.append({"path": p, "error": str(exc)})

        free_after = shutil.disk_usage(vol).free
        net = raw_removed - gz_created
        print("housekeep done: actions=%d raw_removed=%s gz_created=%s net_delta=%s"
              % (done, core.human_gb(raw_removed), core.human_gb(gz_created),
                 core.human_gb(net)))
        print("volume %s free: %s -> %s (delta %s)"
              % (vol, core.human_gb(free_before), core.human_gb(free_after),
                 core.human_gb(free_after - free_before)))

        if json_out:
            summary = {
                "ts": core.now_iso(), "volume": vol,
                "actions": done, "failures": failures,
                "raw_bytes_removed": raw_removed,
                "gz_bytes_created": gz_created,
                "net_bytes_delta": net,
                "free_bytes_before": free_before,
                "free_bytes_after": free_after,
                "free_bytes_delta": free_after - free_before,
            }
            os.makedirs(os.path.dirname(os.path.abspath(json_out)), exist_ok=True)
            with open(json_out, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
        return 0 if not failures else 1
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
