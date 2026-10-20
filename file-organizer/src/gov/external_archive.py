"""External-node archive intake: read-only manifest + lifecycle classification.

Issue #22 / NODE_STORAGE_LIFECYCLE_V1. This module is the *management* half of
the external-node archive pipeline. It walks the ExternalNodeIntake allowlist,
assigns each batch a P0-P4 lifecycle class from policy, and emits the pre-write
manifest destination and a planned archive path — but it performs NO move, NO
copy and NO delete (PRD I1: the gov/ package has no delete primitive, and
MANAGEMENT_FIRST forbids business-file migration until N1 integration passes).

It is a pure function of (intake tree, policy); the only side effect is the
optional manifest plan written into the state dir, which is owned by this tool
and never a business artifact.

Fail-closed properties:
  * Only the two exact ExternalNodeIntake allowlist roots are walked.
  * A batch outside an allowlist is reported, never touched.
  * Archive promotion requires an explicit completion signal (a marker file);
    time alone only produces a CANDIDATE, matching v3.1 invariant #3.
  * The manifest records every field the checklist demands (timestamp,
    operation, source, destination, size, SHA256, names, run_id, class,
    retention date, restore instructions) before any future mutation.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Iterable, Optional

from . import core
from .config import LIFECYCLE_CLASSES, Policy


# A completion signal marker: presence under a batch means "this batch is
# finished and may be promoted". Absence means "time only -> candidate".
COMPLETION_MARKER = ".archive-complete"

# Batch-level lifecycle assignment is source-node default first; a per-project
# override may tighten it later. P4 never archives regardless.
SOURCE_DEFAULT_CLASS = {"LightVela": "P1", "TencentCloud": "P1"}


@dataclass
class Batch:
    """One ExternalNodeIntake <project>/<batch_id> directory."""

    source_node: str
    project: str
    batch_id: str
    path: str
    file_count: int = 0
    total_bytes: int = 0
    oldest_mtime: float = 0.0
    lifecycle_class: str = "P1"
    completed: bool = False
    completion_marker: str = ""
    errors: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "source_node": self.source_node,
            "project": self.project,
            "batch_id": self.batch_id,
            "path": self.path,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "oldest_mtime": self.oldest_mtime,
            "lifecycle_class": self.lifecycle_class,
            "completed": self.completed,
            "completion_marker": self.completion_marker,
            "errors": list(self.errors),
        }


@dataclass
class ArchivePlan:
    """A proposed (never-executed) manifest record for one batch."""

    operation: str
    source_node: str
    source_path: str
    destination: str
    manifest_path: str
    size_bytes: int
    file_count: int
    sha256: str
    lifecycle_class: str
    retention_review_after: str
    restore_path: str
    completed: bool
    run_id: str

    def as_dict(self) -> dict:
        return {
            "timestamp": core.now_iso(),
            "operation": self.operation,
            "source_node": self.source_node,
            "source": self.source_path,
            "destination": self.destination,
            "manifest_path": self.manifest_path,
            "size": self.size_bytes,
            "file_count": self.file_count,
            "sha256": self.sha256,
            "original_name": os.path.basename(self.source_path),
            "final_name": os.path.basename(self.destination),
            "lifecycle_class": self.lifecycle_class,
            "retention_review_after": self.retention_review_after,
            "restore_instructions": "reverse the manifest: restore_path -> "
                                    "source_path, then verify SHA256",
            "restore_path": self.restore_path,
            "completed": self.completed,
            "run_id": self.run_id,
        }


def _class_by_id(pol: Policy, cid: str):
    for c in pol.lifecycle_classes:
        if c.class_id == cid:
            return c
    return None


def _retention_date(pol: Policy, cid: str, completed: bool) -> str:
    cls = _class_by_id(pol, cid)
    if cls is None:
        return "UNKNOWN"
    if cid == "P0":
        return "indefinite"
    days = cls.min_retention_days
    # retention counts from completion; an un-completed batch stays a candidate
    # with no fixed retirement (time alone does not archive, v3.1 invariant 3).
    if not completed:
        return "candidate_pending_completion"
    import datetime as _dt
    return (_dt.date.today() + _dt.timedelta(days=days)).isoformat()


def _sha256_tree(path: str, max_bytes: int = 4 * 1024 ** 3) -> str:
    """Deterministic batch hash over the sorted file list (name + size + file
    sha256). Content-addressed enough to verify a copy, without materialising
    a single monolithic blob."""
    h = hashlib.sha256()
    files = []
    for root, _dirs, names in os.walk(path):
        for n in sorted(names):
            fp = os.path.join(root, n)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            files.append((fp, int(getattr(st, "st_size", 0) or 0)))
    files.sort(key=lambda x: x[0].casefold())
    for fp, size in files:
        fh = hashlib.sha256()
        try:
            with open(fp, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 256), b""):
                    fh.update(chunk)
        except OSError:
            fh.update(b"<unreadable>")
        h.update(fp.replace("\\", "/").casefold().encode("utf-8", "replace"))
        h.update(b"|%d|" % size)
        h.update(fh.hexdigest().encode("ascii"))
        h.update(b"\n")
        if size > max_bytes:
            break
    return h.hexdigest()


def _walk_batch(pol: Policy, batch_dir: str, source_node: str, project: str,
                batch_id: str) -> Batch:
    b = Batch(source_node=source_node, project=project, batch_id=batch_id,
              path=core.norm(batch_dir))
    marker = os.path.join(batch_dir, COMPLETION_MARKER)
    b.completion_marker = marker
    b.completed = os.path.isfile(marker)
    n = 0
    total = 0
    oldest = 0.0
    try:
        for root, dirs, names in os.walk(batch_dir):
            # A reparse point (junction/symlink) is a boundary we must not
            # cross (v3.1 invariant #7). Disclose it, then exclude it from the
            # descent rather than silently following it.
            kept_dirs = []
            for d in dirs:
                if core.is_reparse(os.path.join(root, d)):
                    b.errors.append("reparse_point: %s" % os.path.join(root, d))
                else:
                    kept_dirs.append(d)
            dirs[:] = kept_dirs
            for name in names:
                fp = os.path.join(root, name)
                if name == COMPLETION_MARKER:
                    continue
                try:
                    st = os.lstat(fp)
                except OSError:
                    continue
                if core.is_reparse(fp):
                    b.errors.append("reparse_point: %s" % fp)
                    continue
                n += 1
                total += int(getattr(st, "st_size", 0) or 0)
                m = float(getattr(st, "st_mtime", 0) or 0)
                if m and (not oldest or m < oldest):
                    oldest = m
    except OSError as exc:
        b.errors.append("walk_error: %s" % exc)
    b.file_count = n
    b.total_bytes = total
    b.oldest_mtime = oldest
    return b


def plan_intake(pol: Policy, run_id: Optional[str] = None) -> dict:
    """Walk the two allowlist roots and return a read-only archive plan.

    Returns a dict with `batches` (list[Batch]) and `plans` (list[ArchivePlan]).
    No file is moved, copied or deleted.
    """
    run_id = run_id or ("ext_%s" % core.date_str())
    batches: list[Batch] = []
    plans: list[ArchivePlan] = []
    archive_root = core.norm(pol.lifecycle.get("archive_root", ""))
    manifest_root = core.norm(pol.lifecycle.get("manifest_root", ""))

    for intake_root in pol.external_intake_roots:
        if not os.path.isdir(intake_root):
            # Missing allowlist root is disclosed, not an error that aborts.
            batches.append(Batch(
                source_node="", project="", batch_id="",
                path=intake_root, errors=["intake_root_missing"],
            ))
            continue
        source_node = os.path.basename(intake_root)
        # <intake>/<project>/<batch_id>
        try:
            projects = sorted(os.listdir(intake_root))
        except OSError as exc:
            batches.append(Batch(source_node=source_node, project="",
                                 batch_id="", path=intake_root,
                                 errors=["listdir_error: %s" % exc]))
            continue
        for project in projects:
            pdir = os.path.join(intake_root, project)
            if not os.path.isdir(pdir) or core.is_reparse(pdir):
                continue
            try:
                bidirs = sorted(os.listdir(pdir))
            except OSError:
                continue
            for bid in bidirs:
                bdir = os.path.join(pdir, bid)
                if not os.path.isdir(bdir) or core.is_reparse(bdir):
                    continue
                b = _walk_batch(pol, bdir, source_node, project, bid)
                b.lifecycle_class = SOURCE_DEFAULT_CLASS.get(source_node, "P1")
                batches.append(b)

                cls = _class_by_id(pol, b.lifecycle_class)
                archive = bool(cls and cls.archive)
                # Archive path is <Archive>/<YYYY>/ExternalNodes/<node>/<project>.
                dest = ""
                if archive_root and archive:
                    year = ""
                    if b.oldest_mtime:
                        import datetime as _dt
                        year = _dt.datetime.fromtimestamp(
                            b.oldest_mtime).strftime("%Y")
                    dest = os.path.join(
                        archive_root, year or "unknown", "ExternalNodes",
                        source_node, project, bid)
                manifest_path = ""
                if manifest_root:
                    manifest_path = os.path.join(
                        manifest_root, source_node, project,
                        "%s.jsonl" % bid)
                plans.append(ArchivePlan(
                    operation="external_node_archive_plan",
                    source_node=source_node,
                    source_path=b.path,
                    destination=dest,
                    manifest_path=manifest_path,
                    size_bytes=b.total_bytes,
                    file_count=b.file_count,
                    sha256=_sha256_tree(b.path) if b.file_count else "",
                    lifecycle_class=b.lifecycle_class,
                    retention_review_after=_retention_date(
                        pol, b.lifecycle_class, b.completed),
                    restore_path=b.path,
                    completed=b.completed,
                    run_id=run_id,
                ))

    return {
        "run_id": run_id,
        "ts": core.now_iso(),
        "management_first": True,
        "migration_authorized": False,
        "batches": [b.as_dict() for b in batches],
        "plans": [p.as_dict() for p in plans],
    }

