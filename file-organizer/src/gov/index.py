"""SQLite as a *rebuildable* index. The NDJSON journal stays the authority.

Deleting this file must never cost data (PRD FR10), so nothing here is
written back to the journal and every table is derived.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Iterable, Optional, Sequence

from . import core

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  path TEXT PRIMARY KEY, scan_id TEXT, parent TEXT, name TEXT, ext TEXT,
  size INTEGER, mtime REAL, risk_class TEXT, rule_ids TEXT, zone_role TEXT);
CREATE TABLE IF NOT EXISTS dirs(
  dir TEXT PRIMARY KEY, scan_id TEXT, files INTEGER, bytes INTEGER, children INTEGER);
CREATE TABLE IF NOT EXISTS actions(
  event_id TEXT, ts TEXT, plan_id TEXT, action TEXT, src TEXT, dst TEXT,
  size INTEGER, risk_class TEXT, state TEXT, undo_event_id TEXT);
CREATE INDEX IF NOT EXISTS idx_files_size ON files(size DESC);
CREATE INDEX IF NOT EXISTS idx_files_risk ON files(risk_class);
CREATE INDEX IF NOT EXISTS idx_actions_plan ON actions(plan_id);
"""


class Index:
    def __init__(self, state_dir: str):
        self.state_dir = core.norm(state_dir)
        self.path = os.path.join(self.state_dir, "index", "catalog.sqlite")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.executescript(SCHEMA)
        return conn

    def rebuild(self, *, inventory_paths: Sequence[str] = (),
                snapshot_paths: Sequence[str] = (),
                journal_lines: Iterable[dict] = ()) -> dict:
        """Full rebuild from NDJSON inputs; returns row counts."""
        conn = self.connect()
        with conn:
            for table in ("files", "dirs", "actions"):
                conn.execute("DELETE FROM %s" % table)
            counts = {"files": 0, "dirs": 0, "actions": 0}
            for p in inventory_paths:
                if not os.path.isfile(p):
                    continue
                for row in core.iter_ndjson(p):
                    if row.get("kind") == "header":
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO files(path,scan_id,parent,name,ext,"
                        "size,mtime,risk_class,rule_ids,zone_role) VALUES"
                        "(?,?,?,?,?,?,?,?,?,?)",
                        (row.get("path"), row.get("scan_id"), row.get("parent"),
                         row.get("name"), row.get("ext"), row.get("size"),
                         row.get("mtime"), row.get("risk_class"),
                         json.dumps(row.get("rule_ids") or [], ensure_ascii=False),
                         row.get("zone_role")))
                    counts["files"] += 1
            for p in snapshot_paths:
                if not os.path.isfile(p):
                    continue
                for row in core.iter_ndjson(p):
                    if row.get("kind") != "dir":
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO dirs(dir,scan_id,files,bytes,children)"
                        " VALUES(?,?,?,?,?)",
                        (row.get("dir"), row.get("scan_id"), row.get("files"),
                         row.get("bytes"), row.get("children")))
                    counts["dirs"] += 1
            for ev in journal_lines:
                conn.execute(
                    "INSERT OR REPLACE INTO actions(event_id,ts,plan_id,action,src,"
                    "dst,size,risk_class,state,undo_event_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (ev.get("event_id"), ev.get("ts"), ev.get("plan_id"),
                     ev.get("action"), ev.get("src"), ev.get("dst"), ev.get("size"),
                     ev.get("risk_class"), ev.get("state"), ev.get("undo_event_id")))
                counts["actions"] += 1
        conn.close()
        return counts

    def query(self, sql: str, args: Sequence = ()) -> list[tuple]:
        conn = self.connect()
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def top_dirs(self, n: int = 20) -> list[tuple]:
        return self.query("SELECT dir, files, bytes FROM dirs ORDER BY bytes DESC "
                          "LIMIT ?", (n,))

    def top_files(self, n: int = 20, risk_class: Optional[str] = None) -> list[tuple]:
        if risk_class:
            return self.query("SELECT path, size, risk_class FROM files WHERE "
                              "risk_class=? ORDER BY size DESC LIMIT ?",
                              (risk_class, n))
        return self.query("SELECT path, size, risk_class FROM files ORDER BY "
                          "size DESC LIMIT ?", (n,))

    def actions(self, plan_id: Optional[str] = None) -> list[tuple]:
        if plan_id:
            return self.query("SELECT event_id,state,action,src,dst FROM actions "
                              "WHERE plan_id=? ORDER BY ts", (plan_id,))
        return self.query("SELECT event_id,state,action,src,dst FROM actions "
                          "ORDER BY ts")

    def exists(self) -> bool:
        return os.path.isfile(self.path)
