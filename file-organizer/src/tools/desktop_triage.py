#!/usr/bin/env python3
"""桌面分拣 dry-run 规划器（只读，零副作用）。

它做什么：
  扫描桌面（默认 C:\\Users\\example\\Desktop），按 config/desktop_triage.toml
  的项目簇规则，生成「源 -> 目的地」的迁移计划、人工判定清单、重复文件清单。
  输出三个文件到 .state/desktop_triage/<stamp>/：report.md / plan.csv / plan.json

它不做什么：
  不创建目录、不移动、不复制、不删除、不改名。没有 --apply 这个开关，这是故意的。
  落地执行属于第二阶段，需要逐项批准后另写执行器（copy -> hash verify -> trash）。

Python 3.13 标准库，零第三方依赖。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import tomllib
from dataclasses import dataclass, field
from typing import Optional

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ROOT = r"C:\Users\example\Desktop"
DEFAULT_CONFIG = os.path.join(REPO, "config", "desktop_triage.toml")
INBOX_NAME = "00_Inbox"
TAGS = (("#已完成", "archive"), ("#参考", "reference"),
        ("#待办", "active"), ("#进行中", "active"))


@dataclass
class Item:
    src: str
    name: str
    rel: str
    is_dir: bool
    files: int
    size: int
    mtime: float
    ext: str
    container: str  # "root" or INBOX_NAME


@dataclass
class Decision:
    action: str          # MOVE | REVIEW | KEEP
    dest: str = ""
    cluster: str = ""
    confidence: str = ""  # HIGH | MEDIUM | LOW
    reason: str = ""


@dataclass
class Plan:
    items: list = field(default_factory=list)
    dup_of: dict = field(default_factory=dict)
    dup_groups: list = field(default_factory=list)


def load_config(path: str) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    for key in ("dest", "keep", "review", "cluster"):
        if key not in cfg:
            raise SystemExit("config missing section: %s" % key)
    return cfg


def norm(p: str) -> str:
    return os.path.normpath(os.path.abspath(p))


def is_reparse(path: str) -> bool:
    try:
        return os.path.islink(path) or bool(
            getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0)
            & 0x400)
    except OSError:
        return False


def dir_stats(path: str) -> tuple[int, int, float]:
    n = 0
    size = 0
    newest = 0.0
    for base, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [d for d in dirs if not is_reparse(os.path.join(base, d))]
        for fn in files:
            fp = os.path.join(base, fn)
            try:
                st = os.stat(fp, follow_symlinks=False)
            except OSError:
                continue
            n += 1
            size += st.st_size
            newest = max(newest, st.st_mtime)
    return n, size, newest


def collect(root: str) -> list[Item]:
    items: list[Item] = []
    for e in sorted(os.scandir(root), key=lambda x: x.name.lower()):
        rel = e.name
        if e.is_dir() and not is_reparse(e.path):
            n, size, newest = dir_stats(e.path)
            if e.name == INBOX_NAME:
                for c in sorted(os.scandir(e.path), key=lambda x: x.name.lower()):
                    if c.is_dir() and not is_reparse(c.path):
                        cn, cs, cm = dir_stats(c.path)
                        items.append(Item(c.path, c.name, INBOX_NAME + "/" + c.name,
                                          True, cn, cs, cm, "", INBOX_NAME))
                    elif c.is_file():
                        try:
                            st = c.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        items.append(Item(c.path, c.name, INBOX_NAME + "/" + c.name,
                                          False, 1, st.st_size, st.st_mtime,
                                          os.path.splitext(c.name)[1].lower().lstrip("."),
                                          INBOX_NAME))
                continue
            items.append(Item(e.path, e.name, rel, True, n, size, newest, "", "root"))
        elif e.is_file():
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            items.append(Item(e.path, e.name, rel, False, 1, st.st_size, st.st_mtime,
                              os.path.splitext(e.name)[1].lower().lstrip("."), "root"))
    return items


def keyword_hit(name_low: str, kw: str) -> bool:
    """Short alphanumeric tokens (PRJ1, PRJ2, BP) must sit on a token boundary.

    Plain substring matching put '3c9prj100c...pdf' into PRJ1 because the hash
    contains "prj1"; a 2-3 char token needs a boundary to be evidence.
    """
    k = kw.lower()
    if len(k) <= 3 and k.isalnum():
        return re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(k),
                         name_low) is not None
    return k in name_low


def find_cluster(name: str, ext: str, clusters: list[dict]) -> tuple[Optional[dict], str]:
    for cl in clusters:
        if ext and ext in [x.lower() for x in cl.get("exts", [])]:
            return cl, "MEDIUM"
    low = name.lower()
    for cl in clusters:
        for kw in cl.get("keywords", []):
            if keyword_hit(low, kw):
                return cl, "MEDIUM"
    return None, ""


def zone_base(cfg: dict, zone: str) -> str:
    return cfg["dest"][zone]


def join_dest(base: str, target: str) -> str:
    return os.path.join(base, target)


def classify(item: Item, cfg: dict, clusters: list[dict], stamp: str) -> Decision:
    keep = cfg["keep"]
    rev = cfg["review"]
    low = item.name.lower()
    ext = item.ext

    # 1) 永不动的启动器 / 系统文件
    if ext in [e.lower() for e in keep["ext"]] or item.name in keep["names"]:
        return Decision("KEEP", reason="SHORTCUT_OR_SYSTEM")
    if (item.container == "root" and ext in [e.lower() for e in keep.get("root_ext", [])]
            and not any(t in item.name for t, _ in TAGS)):
        return Decision("KEEP", reason="ROOT_LAUNCHER_SCRIPT")

    # 2) Office 锁文件 / 备份 / 代码 / 临时目录名
    if any(item.name.startswith(p) for p in rev["name_prefixes"]):
        return Decision("REVIEW", reason="OFFICE_LOCK_OR_TEMP_FILE")
    if ext in [e.lower() for e in rev.get("bak_ext", [])]:
        return Decision("REVIEW", reason="BACKUP_FILE")
    if item.name in rev["temp_names"]:
        return Decision("REVIEW", reason="TEMP_DIR_NAME")
    if item.is_dir and item.files == 0:
        return Decision("REVIEW", reason="EMPTY_DIR")
    if not item.is_dir and ext in [e.lower() for e in rev["code_ext"]]:
        return Decision("REVIEW", reason="CODE_FILE")

    # 3) 00_Inbox 内的快捷方式（与桌面重复的启动器）
    if item.container == INBOX_NAME and ext in ("lnk", "url"):
        return Decision("REVIEW", reason="SHORTCUT_IN_INBOX")

    # 4) PRJ16_INSTALLERS
    if not item.is_dir and ext in ("exe", "msi", "msp"):
        return Decision("MOVE", os.path.join(cfg["dest"]["software"], item.name),
                        "PRJ16_INSTALLERS", "HIGH", "INSTALLER")

    # 5) 目录：只认精确目录名；认不出就交人工，不猜
    if item.is_dir:
        for cl in clusters:
            if item.name in cl.get("dirs", []):
                dest = join_dest(zone_base(cfg, cl["zone"]), cl["target"])
                return Decision("MOVE", dest, cl["target"], "HIGH", "DIR_EXACT_MATCH")
        return Decision("REVIEW", reason="UNKNOWN_DIR")

    # 6) 散落文件：标签优先，其次关键词簇，最后兜底
    tag_zone = ""
    for tag, zone in TAGS:
        if tag in item.name:
            tag_zone = zone
            break

    cl, conf = find_cluster(item.name, ext, clusters)
    if cl is not None:
        zone = tag_zone or cl["zone"]
        dest = os.path.join(zone_base(cfg, zone), cl["target"], item.name)
        reason = "TAG_%s" % tag_zone.upper() if tag_zone else "KEYWORD"
        return Decision("MOVE", dest, cl["target"], "HIGH" if tag_zone else conf, reason)

    # 没有簇命中时，即使带标签也只进未归类：标签说明状态，不说明归属
    dest = os.path.join(cfg["dest"]["unclassified"], stamp, item.name)
    reason = "TAG_ONLY_UNCERTAIN" if tag_zone else "NO_RULE_MATCHED"
    return Decision("MOVE", dest, "_未归类", "LOW", reason)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def all_files(root: str) -> list[tuple[str, int]]:
    """Every regular file under root (reparse points not followed)."""
    out = []
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not is_reparse(os.path.join(base, d))]
        for fn in files:
            fp = os.path.join(base, fn)
            try:
                st = os.stat(fp, follow_symlinks=False)
            except OSError:
                continue
            out.append((fp, st.st_size))
    return out


def find_duplicates(root: str, do_hash: bool = True) -> tuple[dict, list]:
    """size -> files, then hash only the same-size groups. Report only."""
    if not do_hash:
        return {}, []
    by_size: dict[int, list[str]] = {}
    for path, size in all_files(root):
        if size <= 0:
            continue
        by_size.setdefault(size, []).append(path)
    groups = []
    dup_of: dict[str, str] = {}
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        by_hash: dict[str, list[str]] = {}
        for p in paths:
            try:
                by_hash.setdefault(sha256(p), []).append(p)
            except OSError:
                continue
        for h, same in by_hash.items():
            if len(same) < 2:
                continue
            same.sort(key=lambda p: (("副本" in p) or ("(1)" in p) or ("copy" in p.lower()),
                                     len(p), p))
            keep = same[0]
            for p in same[1:]:
                dup_of[p] = keep
            groups.append({"sha256": h, "size": size, "keep": keep, "dups": same[1:]})
    groups.sort(key=lambda g: -g["size"] * len(g["dups"]))
    return dup_of, groups


def apply_conflicts(plan: list, cfg: dict) -> None:
    for row in plan:
        if row["action"] != "MOVE":
            continue
        dest = row["dest"]
        if os.path.isdir(dest):
            row["action"] = "REVIEW"
            row["reason"] = "DEST_IS_DIR_MERGE_NEEDED"
        elif os.path.exists(dest):
            row["action"] = "REVIEW"
            row["reason"] = "DEST_EXISTS"


def build(cfg: dict, root: str, stamp: str, do_hash: bool) -> Plan:
    clusters = cfg["cluster"]
    items = collect(root)
    pl = Plan(items=items)
    pl.dup_of, pl.dup_groups = find_duplicates(root, do_hash)
    rows = []
    for it in items:
        dec = classify(it, cfg, clusters, stamp)
        rows.append({
            "action": dec.action,
            "approved": "",
            "src": it.src,
            "dest": dec.dest,
            "kind": "DIR" if it.is_dir else "FILE",
            "files": it.files,
            "size_mb": round(it.size / 1e6, 2),
            "cluster": dec.cluster,
            "confidence": dec.confidence,
            "reason": dec.reason,
            "dup_of": pl.dup_of.get(it.src, ""),
            "rel": it.rel,
        })
    apply_conflicts(rows, cfg)
    order = {"MOVE": 0, "REVIEW": 1, "KEEP": 2}
    rows.sort(key=lambda r: (order.get(r["action"], 9), -r["size_mb"], r["src"].lower()))
    pl.items = rows
    return pl


def summarize(rows: list) -> dict:
    out = {}
    for r in rows:
        b = out.setdefault(r["action"], {"items": 0, "files": 0, "mb": 0.0})
        b["items"] += 1
        b["files"] += r["files"]
        b["mb"] += r["size_mb"]
    for b in out.values():
        b["mb"] = round(b["mb"], 1)
    return out


def by_cluster(rows: list) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        if r["action"] != "MOVE":
            continue
        cluster = r["cluster"] or "_未归类"
        if cluster == "PRJ16_INSTALLERS":
            dest_key = os.path.dirname(r["dest"])
        elif r["kind"] == "FILE":
            dest_key = os.path.dirname(r["dest"])
        else:
            dest_key = r["dest"]
        key = (cluster, dest_key)
        a = agg.setdefault(key, {"cluster": key[0], "dest": key[1], "items": 0,
                                 "files": 0, "mb": 0.0, "conf": set()})
        a["items"] += 1
        a["files"] += r["files"]
        a["mb"] += r["size_mb"]
        if r["confidence"]:
            a["conf"].add(r["confidence"])
    out = []
    for a in agg.values():
        a["mb"] = round(a["mb"], 1)
        a["confidence"] = "/".join(sorted(a.pop("conf"))) or "-"
        out.append(a)
    out.sort(key=lambda x: -x["mb"])
    return out


def write_csv(path: str, rows: list) -> None:
    cols = ["action", "approved", "rel", "src", "dest", "kind", "files", "size_mb",
            "cluster", "confidence", "reason", "dup_of"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_json(path: str, cfg: dict, root: str, stamp: str, pl: Plan) -> None:
    data = {
        "stamp": stamp,
        "root": root,
        "mode": "DRY_RUN_READ_ONLY",
        "policy": "config/desktop_triage.toml",
        "summary": summarize(pl.items),
        "clusters": by_cluster(pl.items),
        "duplicate_groups": pl.dup_groups,
        "items": pl.items,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_report(path: str, root: str, stamp: str, cfg: dict, pl: Plan) -> None:
    s = summarize(pl.items)
    total_mb = sum(r["size_mb"] for r in pl.items)
    total_files = sum(r["files"] for r in pl.items)
    lines = []
    a = lines.append
    a("# 桌面分拣 dry-run 报告")
    a("")
    a("- 扫描根：`%s`" % root)
    a("- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
    a("- 模式：**只读规划**（不创建、不移动、不删除；本工具没有 `--apply`）")
    a("- 规模：%d 个条目 / %d 个文件 / %.1f MB" % (len(pl.items), total_files, total_mb))
    a("")
    a("## 1. 总览")
    a("")
    a("| 动作 | 条目 | 文件 | 体积 | 含义 |")
    a("|---|---:|---:|---:|---|")
    labels = {"MOVE": "可迁移（待批准）", "REVIEW": "需人工判定", "KEEP": "原地保留"}
    for k in ("MOVE", "REVIEW", "KEEP"):
        if k in s:
            a("| %s | %d | %d | %.1f MB | %s |" %
              (k, s[k]["items"], s[k]["files"], s[k]["mb"], labels[k]))
    a("")
    a("## 2. 迁移计划（按簇汇总）")
    a("")
    a("| 簇 | 条目 | 文件 | 体积 | 目的地 | 置信度 |")
    a("|---|---:|---:|---:|---|---|")
    for c in by_cluster(pl.items):
        a("| %s | %d | %d | %.1f MB | `%s` | %s |" %
          (c["cluster"], c["items"], c["files"], c["mb"], c["dest"], c["confidence"]))
    a("")
    a("完整逐项清单见同目录 `plan.csv`（含空 `approved` 列，供逐项签批）。")
    a("")
    a("## 3. 需人工判定")
    a("")
    rev = [r for r in pl.items if r["action"] == "REVIEW"]
    if not rev:
        a("（无）")
    else:
        a("| 条目 | 类型 | 体积 | 原因 |")
        a("|---|---|---:|---|")
        for r in rev[:40]:
            a("| `%s` | %s | %.1f MB | %s |" %
              (r["rel"], r["kind"], r["size_mb"], r["reason"]))
        if len(rev) > 40:
            a("")
            a("（另有 %d 项见 plan.csv）" % (len(rev) - 40))
    a("")
    a("## 4. 重复文件（仅报告，不处理）")
    a("")
    if not pl.dup_groups:
        a("（未发现字节级重复）")
    else:
        a("| 保留 | 重复项 | 体积 |")
        a("|---|---|---:|")
        for g in pl.dup_groups[:15]:
            a("| `%s` | %d 个 | %.1f MB |" %
              (os.path.basename(g["keep"]), len(g["dups"]), g["size"] / 1e6))
        a("")
        a("合计 %d 组；重复项全部列在 plan.json 的 `duplicate_groups`。" % len(pl.dup_groups))
    a("")
    a("## 5. 执行后的桌面形态（目标）")
    a("")
    a("- 顶层只保留：快捷方式（.lnk/.url）、启动脚本、`desktop.ini`、`00_Inbox`（清空后的捕获入口）")
    a("- 其余全部进入 `D:\\Data`：Active 放进行中项目，Reference 放规范/资料，Archive 放 `#已完成`，未判定项进 `Inbox\\Desktop\\_未归类`")
    a("- 不删除任何文件；`~$` 锁文件、代码文件、临时目录、重复项只出清单")
    a("")
    a("## 6. 下一步（需要逐项批准）")
    a("")
    a("1. 在 `plan.csv` 的 `approved` 列填 `y` / `n`（不填 = 不执行）")
    a("2. 冲突项（`DEST_EXISTS` / `DEST_IS_DIR_MERGE_NEEDED`）先定合并策略")
    a("3. 第二阶段执行器：复制 → SHA-256 校验 → 源文件进回收区（可 undo）")
    a("4. 执行完再清空 `00_Inbox`，它继续作为桌面唯一捕获入口")
    a("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser(description="Desktop triage dry-run planner (read-only)")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--out", default="")
    ap.add_argument("--no-hash", action="store_true", help="skip duplicate hashing")
    args = ap.parse_args()

    root = norm(args.root)
    if not os.path.isdir(root):
        print("root not found: %s" % root, file=sys.stderr)
        return 2
    try:
        cfg = load_config(args.config)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print("config error: %s" % exc, file=sys.stderr)
        return 2

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = norm(args.out) if args.out else os.path.join(REPO, ".state",
                                                       "desktop_triage", stamp)
    try:
        inside = os.path.commonpath([out, root]) == root
    except ValueError:  # different drives: cannot be inside
        inside = False
    if inside:
        print("refusing to write inside the scanned root", file=sys.stderr)
        return 2
    os.makedirs(out, exist_ok=True)

    pl = build(cfg, root, stamp, not args.no_hash)
    write_csv(os.path.join(out, "plan.csv"), pl.items)
    write_json(os.path.join(out, "plan.json"), cfg, root, stamp, pl)
    write_report(os.path.join(out, "report.md"), root, stamp, cfg, pl)

    s = summarize(pl.items)
    print("out: %s" % out)
    print("items=%d files=%d mb=%.1f" %
          (len(pl.items), sum(r["files"] for r in pl.items),
           sum(r["size_mb"] for r in pl.items)))
    for k in ("MOVE", "REVIEW", "KEEP"):
        if k in s:
            print("%-6s items=%-4d files=%-6d mb=%.1f" %
                  (k, s[k]["items"], s[k]["files"], s[k]["mb"]))
    print("duplicate_groups=%d" % len(pl.dup_groups))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
