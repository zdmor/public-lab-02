#!/usr/bin/env python3
"""D:\\Data 四区月度体检（只读）。

扫描 Active / Reference / Archive / Inbox，输出：
  - 项目活跃度与归档候选
  - Reference 复盘候选
  - Inbox\\Desktop\\_未归类 的批次年龄
  - 跨区重复文件
不移动、不复制、不删除、不重命名。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import time
import tomllib
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_CONFIG = os.path.join(REPO, "config", "data_zones.toml")
DAY = 86400.0


@dataclass
class Bucket:
    path: str
    zone: str
    name: str
    files: int = 0
    size: int = 0
    newest: float = 0.0
    oldest: float = 0.0
    active_tag_hits: int = 0


@dataclass
class Report:
    generated: str = ""
    buckets: list = field(default_factory=list)
    dup_groups: list = field(default_factory=list)
    totals: dict = field(default_factory=dict)


def load_config(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def is_reparse(path: str) -> bool:
    try:
        return os.path.islink(path) or bool(
            getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0)
            & 0x400)
    except OSError:
        return False


def scan(path: str, zone: str, name: str, active_tags: list[str]) -> Bucket:
    b = Bucket(path=path, zone=zone, name=name)
    for base, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [d for d in dirs if not is_reparse(os.path.join(base, d))]
        for fn in files:
            fp = os.path.join(base, fn)
            try:
                st = os.stat(fp, follow_symlinks=False)
            except OSError:
                continue
            b.files += 1
            b.size += st.st_size
            b.newest = max(b.newest, st.st_mtime)
            if b.oldest == 0 or st.st_mtime < b.oldest:
                b.oldest = st.st_mtime
            if any(t in fn for t in active_tags):
                b.active_tag_hits += 1
    return b


def age_days(ts: float) -> float:
    return (time.time() - ts) / DAY if ts else -1.0


def status_of(b: Bucket, cfg: dict) -> tuple[str, str]:
    th = cfg["thresholds"]
    tags = th["active_tags"]
    if b.zone == "active":
        if b.active_tag_hits:
            return "ACTIVE", "has #待办/#进行中 tag"
        if b.newest and age_days(b.newest) > th["active_stale_days"]:
            return "ARCHIVE_CANDIDATE", "no change for %d days" % age_days(b.newest)
        return "ACTIVE", "recent activity"
    if b.zone == "reference":
        if b.newest and age_days(b.newest) > th["reference_stale_days"]:
            return "REVIEW_CANDIDATE", "reference untouched for %d days" % age_days(b.newest)
        return "REFERENCE", "ok"
    if b.zone == "inbox":
        if b.newest and age_days(b.newest) > th["unclassified_aged_days"]:
            return "UNCLASSIFIED_AGED", "unclaimed for %d days" % age_days(b.newest)
        return "UNCLASSIFIED", "recent"
    return "PROTECTED", "archive zone"


def collect(cfg: dict) -> Report:
    rep = Report(generated=time.strftime("%Y-%m-%d %H:%M:%S"))
    z = cfg["zones"]
    tags = cfg["thresholds"]["active_tags"]

    for zone, root in (("active", z["active"]), ("reference", z["reference"])):
        if not os.path.isdir(root):
            continue
        for e in sorted(os.scandir(root), key=lambda x: x.name.lower()):
            if e.is_dir() and not is_reparse(e.path):
                rep.buckets.append(scan(e.path, zone, e.name, tags))

    archive = z["archive"]
    if os.path.isdir(archive):
        for year in sorted(os.scandir(archive), key=lambda x: x.name):
            if year.is_dir():
                rep.buckets.append(scan(year.path, "archive", year.name, tags))

    inbox = os.path.join(z["inbox"], "Desktop")
    if os.path.isdir(inbox):
        for e in sorted(os.scandir(inbox), key=lambda x: x.name.lower()):
            if e.is_dir():
                rep.buckets.append(scan(e.path, "inbox", e.name, tags))

    rep.dup_groups = duplicates(rep.buckets)
    total = {"files": 0, "size": 0}
    for b in rep.buckets:
        total["files"] += b.files
        total["size"] += b.size
    rep.totals = total
    return rep


def duplicates(buckets: list[Bucket]) -> list[dict]:
    by_size: dict[int, list[tuple[str, str]]] = {}
    for b in buckets:
        if b.zone == "archive":
            continue
        for base, dirs, files in os.walk(b.path, followlinks=False):
            dirs[:] = [d for d in dirs if not is_reparse(os.path.join(base, d))]
            for fn in files:
                fp = os.path.join(base, fn)
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    continue
                if size <= 0:
                    continue
                by_size.setdefault(size, []).append((fp, b.name))
    groups = []
    for size, items in by_size.items():
        if len(items) < 2:
            continue
        by_hash: dict[str, list[tuple[str, str]]] = {}
        for fp, name in items:
            try:
                h = hashlib.sha256()
                with open(fp, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
            except OSError:
                continue
            by_hash.setdefault(h.hexdigest(), []).append((fp, name))
        for h, same in by_hash.items():
            if len(same) >= 2:
                groups.append({"sha256": h, "size": size, "files": same})
    groups.sort(key=lambda g: -g["size"] * (len(g["files"]) - 1))
    return groups


def write_csv(path: str, rep: Report, cfg: dict) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["zone", "project", "status", "files", "mb", "last_activity_days",
                    "active_tags", "path", "reason"])
        for b in rep.buckets:
            st, why = status_of(b, cfg)
            w.writerow([b.zone, b.name, st, b.files, round(b.size / 1e6, 2),
                        round(age_days(b.newest), 1), b.active_tag_hits,
                        b.path, why])


def write_report(path: str, rep: Report, cfg: dict) -> None:
    th = cfg["thresholds"]
    L = []
    a = L.append
    a("# D:\\Data 四区月度体检")
    a("")
    a("- 生成时间：%s" % rep.generated)
    a("- 模式：**只读**（不移动、不复制、不删除、不重命名）")
    a("- 范围：Active / Reference / Archive / Inbox\\Desktop")
    a("- 总计：%d 个文件 / %.1f MB" % (rep.totals["files"], rep.totals["size"] / 1e6))
    a("")
    a("## 1. 活跃项目")
    a("")
    a("| 项目 | 文件 | 体积 | 最近变更 | 状态 |")
    a("|---|---:|---:|---:|---|")
    for b in sorted([x for x in rep.buckets if x.zone == "active"],
                    key=lambda x: -x.newest):
        st, why = status_of(b, cfg)
        a("| %s | %d | %.1f MB | %d 天前 | %s |" %
          (b.name, b.files, b.size / 1e6, age_days(b.newest), st))
    a("")
    cand = [b for b in rep.buckets if b.zone == "active"
            and status_of(b, cfg)[0] == "ARCHIVE_CANDIDATE"]
    a("**归档候选（≥%d 天无变更，仅提案）：** %s" %
      (th["active_stale_days"], "、".join(b.name for b in cand) or "无"))
    a("")
    a("## 2. Reference")
    a("")
    a("| 主题 | 文件 | 体积 | 最近变更 | 状态 |")
    a("|---|---:|---:|---:|---|")
    for b in sorted([x for x in rep.buckets if x.zone == "reference"],
                    key=lambda x: -x.size):
        st, _ = status_of(b, cfg)
        a("| %s | %d | %.1f MB | %d 天前 | %s |" %
          (b.name, b.files, b.size / 1e6, age_days(b.newest), st))
    a("")
    a("## 3. Inbox / 未归类")
    a("")
    a("| 批次 | 文件 | 体积 | 最近变更 | 状态 |")
    a("|---|---:|---:|---:|---|")
    for b in rep.buckets:
        if b.zone != "inbox":
            continue
        st, _ = status_of(b, cfg)
        a("| %s | %d | %.1f MB | %d 天前 | %s |" %
          (b.name, b.files, b.size / 1e6, age_days(b.newest), st))
    a("")
    a("超过 %d 天未认领的批次应在本月内处理：归入 Active / Reference，或明确归档。" %
      th["unclassified_aged_days"])
    a("")
    a("## 4. Archive")
    a("")
    for b in rep.buckets:
        if b.zone == "archive":
            a("- %s：%d 文件 / %.1f MB（只读，永不清洗）" %
              (b.name, b.files, b.size / 1e6))
    a("")
    a("## 5. 跨区重复（仅报告）")
    a("")
    if not rep.dup_groups:
        a("（未发现字节级重复）")
    else:
        a("| 保留建议 | 重复项 | 体积 |")
        a("|---|---:|---:|")
        for g in rep.dup_groups[:15]:
            names = "、".join(os.path.basename(p) for p, _ in g["files"][:3])
            a("| %s | %d | %.1f MB |" % (names, len(g["files"]) - 1, g["size"] / 1e6))
        a("")
        a("合计 %d 组。重复项不自动处理。" % len(rep.dup_groups))
    a("")
    a("## 6. 本月建议动作")
    a("")
    if cand:
        a("1. 逐个确认归档候选；确认后移动到 `Archive\\2026\\<项目>`。")
    else:
        a("1. 无项目达到归档阈值。")
    a("2. 检查 `Inbox\\Desktop\\_未归类`，认领或明确暂缓。")
    a("3. 重复文件按组决定保留哪一份；本工具不删除。")
    a("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def write_json(path: str, rep: Report, cfg: dict) -> None:
    rows = []
    for b in rep.buckets:
        st, why = status_of(b, cfg)
        rows.append({"zone": b.zone, "name": b.name, "path": b.path,
                     "files": b.files, "size": b.size,
                     "last_activity_days": round(age_days(b.newest), 1),
                     "active_tags": b.active_tag_hits, "status": st, "reason": why})
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"generated": rep.generated, "totals": rep.totals,
                   "buckets": rows, "duplicates": rep.dup_groups},
                  f, ensure_ascii=False, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser(description="D:\\Data four-zone monthly review (read-only)")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    cfg = load_config(args.config)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = args.out or os.path.join(cfg["zones"]["state"], stamp)
    os.makedirs(out, exist_ok=True)
    rep = collect(cfg)
    write_csv(os.path.join(out, "review.csv"), rep, cfg)
    write_report(os.path.join(out, "report.md"), rep, cfg)
    write_json(os.path.join(out, "review.json"), rep, cfg)
    print("out: %s" % out)
    print("buckets=%d files=%d mb=%.1f duplicates=%d" %
          (len(rep.buckets), rep.totals["files"], rep.totals["size"] / 1e6,
           len(rep.dup_groups)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
