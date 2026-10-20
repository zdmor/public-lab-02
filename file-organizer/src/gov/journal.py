"""Append-only NDJSON journal (the authority) and undo chains (PRD 13)."""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Optional, Sequence

from . import core

TERMINAL = ("done", "failed", "undone")
EVENT_FIELDS = ("event_id", "ts", "machine", "actor", "plan_id", "action",
                "src", "dst", "src_hash", "size", "mtime", "risk_class",
                "rule_ids", "policy_id", "policy_version", "evidence", "ai",
                "state", "undo_event_id")


class JournalError(Exception):
    pass


def new_event_id() -> str:
    return "e_" + uuid.uuid4().hex[:12]


def new_plan_id() -> str:
    return "p_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]


@dataclass
class Journal:
    state_dir: str
    machine: str = field(default_factory=core.machine_name)
    backup_dir: str = ""            # optional second-location mirror (P0-3)
    corrupt: list = field(default_factory=list, repr=False)
    _last: dict = field(default_factory=dict, repr=False)
    _undone: set = field(default_factory=set, repr=False)
    _loaded: bool = field(default=False, repr=False)
    _mirror_writes: int = field(default=0, repr=False)
    _mirror_fails: list = field(default_factory=list, repr=False)

    @property
    def dir(self) -> str:
        return os.path.join(core.norm(self.state_dir), "journal", self.machine)

    def shard(self, day: Optional[str] = None) -> str:
        return os.path.join(self.dir, "%s.ndjson" % (day or core.date_str()))

    def append(self, event: dict) -> dict:
        ev = dict(event)
        ev.setdefault("event_id", new_event_id())
        ev.setdefault("ts", core.now_iso())
        ev.setdefault("machine", self.machine)
        ev.setdefault("actor", ev.get("actor") or os.environ.get("USERNAME") or "local")
        missing = [f for f in EVENT_FIELDS if f not in ev]
        if missing:
            raise JournalError("event missing fields %s (PRD 13)" % missing)
        core.append_ndjson(self.shard(ev["ts"][:10]), ev)
        self._mirror(ev)
        if self._loaded:  # keep the read cache honest: last write wins
            self._last[ev["event_id"]] = ev
            if ev.get("action") == "UNDO" and ev.get("undo_of"):
                self._undone.add(ev["undo_of"])
        return ev

    # ---- second-location mirror (P0-3) ----------------------------------
    def _mirror(self, ev: dict) -> None:
        """Append to the backup journal if configured (never blocks primary).

        The mirror lives on a different failure domain (machine override
        ``journal_backup_dir``). A failed mirror write is counted, not raised:
        the primary journal remains the authority and a later run can re-sync.
        """
        base = getattr(self, "backup_dir", "") or ""
        if not base:
            return
        day = ev["ts"][:10]
        try:
            os.makedirs(os.path.join(base, "journal", self.machine), exist_ok=True)
            core.append_ndjson(
                os.path.join(base, "journal", self.machine, "%s.ndjson" % day),
                ev)
            self._mirror_writes += 1
        except OSError as exc:  # noqa: BLE001 - disclosed, not silent
            self._mirror_fails.append({"event_id": ev.get("event_id"),
                                       "error": str(exc)})

    def backup_status(self) -> dict:
        """What the mirror actually did (for the P0-3 closure report)."""
        return {"backup_dir": getattr(self, "backup_dir", "") or "",
                "mirror_writes": getattr(self, "_mirror_writes", 0),
                "mirror_fails": list(getattr(self, "_mirror_fails", []))}

    def plan(self, event: dict) -> dict:
        ev = dict(event)
        ev["state"] = "planned"
        return self.append(ev)

    def commit(self, event_id: str, state: str = "done",
               updates: Optional[dict] = None) -> dict:
        if state not in TERMINAL:
            raise JournalError("commit state must be one of %s" % (TERMINAL,))
        base = dict(self.get(event_id) or {})
        if not base:
            raise JournalError("unknown event_id %s" % event_id)
        base.update(updates or {})
        base["event_id"] = event_id
        base["state"] = state
        return self.append(base)

    def shards(self) -> list[str]:
        if not os.path.isdir(self.dir):
            return []
        return [os.path.join(self.dir, n)
                for n in sorted(os.listdir(self.dir)) if n.endswith(".ndjson")]

    def iter_lines(self, machine: Optional[str] = None) -> Iterator[dict]:
        dirs = ([os.path.join(core.norm(self.state_dir), "journal", machine)]
                if machine else [self.dir])
        for d in dirs:
            if not os.path.isdir(d):
                continue
            for n in sorted(os.listdir(d)):
                if not n.endswith(".ndjson"):
                    continue
                for row in core.iter_ndjson(os.path.join(d, n),
                                            on_bad=self.corrupt.append):
                    yield row

    def latest_map(self) -> dict:
        """event_id -> last row, read once and cached for the rest of the run.

        Without the cache every commit() re-reads every shard, which makes a
        2000-action apply quadratic in filesystem traffic.
        """
        if not self._loaded:
            last: dict[str, dict] = {}
            undone: set = set()
            for row in self.iter_lines():
                last[row.get("event_id")] = row
                if row.get("action") == "UNDO" and row.get("undo_of"):
                    undone.add(row["undo_of"])
            self._last, self._undone, self._loaded = last, undone, True
        return self._last

    def all_events(self) -> list[dict]:
        return list(self.iter_lines())

    def get(self, event_id: str) -> Optional[dict]:
        return self.latest_map().get(event_id)

    def by_plan(self, plan_id: str) -> list[dict]:
        merged = {k: v for k, v in self.latest_map().items()
                  if v.get("plan_id") == plan_id}
        return [merged[k] for k in sorted(merged)]

    def states(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {"planned": [], "done": [], "failed": [],
                                      "undone": []}
        for ev in self.latest_map().values():
            out.setdefault(ev.get("state") or "?", []).append(ev)
        return out

    def unfinished(self) -> list[dict]:
        """planned with no terminal line = crash residue (DESIGN 3.5)."""
        return [ev for ev in self.latest_map().values()
                if ev.get("state") == "planned"]

    def undone_ids(self) -> set[str]:
        self.latest_map()
        return set(self._undone)

    def summary(self) -> dict:
        states = self.states()
        return {"shards": len(self.shards()), "events": len(self.latest_map()),
                "by_state": {k: len(v) for k, v in states.items()},
                "unfinished": [e.get("event_id") for e in self.unfinished()],
                "corrupt_lines": len(self.corrupt)}
