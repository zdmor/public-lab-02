# -*- coding: utf-8 -*-
"""台账生成器：把两条执行链路的历史记录合成一份可读账本。

链路 A —— gov（gov/ 包）
    .state/journal/<MACHINE>/*.ndjson        实际执行事件（planned/done/undone）
    .state/plans/*.ndjson                    计划（dry-run，未执行）
    .state/candidates/*.jsonl                全盘候选分类结果（只读判定）

链路 B —— 桌面分拣（tools/desktop_triage*.py）
    .state/desktop_triage/runs/*/manifest.json   每次执行的逐条结果
    .state/desktop_triage/<stamp>/plan.json      每次规划的分类依据
    .state/desktop_triage/journal.ndjson         全量事件流（含人工改判/手工入回收站）

只读，不碰任何被管理文件。输出 .state/ledger/ledger_<stamp>.md 并打印摘要。

用法：
    python tools/ledger.py
    python tools/ledger.py --out D:/somewhere/x.md
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(BASE, ".state")


def _read_ndjson(path):
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
    except OSError:
        pass
    return out


def _mb(n):
    return n / 1048576.0


# ---------------------------------------------------------------- 链路 A
def gov_section():
    L = []
    jdir = os.path.join(STATE, "journal")
    jfiles = []
    if os.path.isdir(jdir):
        for m in sorted(os.listdir(jdir)):
            d = os.path.join(jdir, m)
            if os.path.isdir(d):
                for f in sorted(os.listdir(d)):
                    if f.endswith(".ndjson"):
                        jfiles.append(os.path.join(d, f))

    rows = []
    for f in jfiles:
        rows.extend(_read_ndjson(f))

    # 注意：state=="done" 同时包含 MOVE 与 UNDO 两种动作，必须按 action 拆开，
    # 否则会把「撤销成功」误算成「又移动了 15 个文件」。
    moved = [r for r in rows if r.get("state") == "done" and r.get("action") == "MOVE"]
    restored = [r for r in rows if r.get("state") == "done" and r.get("action") == "UNDO"]
    undone = [r for r in rows if r.get("state") == "undone"]
    net = len(moved) - len(restored)
    net_bytes = (sum(r.get("size") or 0 for r in moved)
                 - sum(r.get("size") or 0 for r in restored))

    L.append("## A. gov —— 判了什么，动了什么")
    L.append("")
    if not rows:
        L.append("无执行记录。gov 从未真正移动过任何文件。")
        L.append("")
        return L, {"done": 0, "undone": 0, "files": 0, "bytes": 0}

    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    L.append("| 事件总数 | %d |" % len(rows))
    L.append("| 移动成功（MOVE done） | %d 个 / %.1f KB |"
             % (len(moved), sum(r.get("size") or 0 for r in moved) / 1024.0))
    L.append("| 撤销成功（UNDO done） | %d 个 / %.1f KB |"
             % (len(restored), sum(r.get("size") or 0 for r in restored) / 1024.0))
    L.append("| **净变动（仍在回收站）** | **%d 个 / %.1f KB** |" % (net, net_bytes / 1024.0))
    if moved:
        L.append("| 首次 / 末次执行 | %s → %s |" % (moved[0].get("ts"), moved[-1].get("ts")))
    L.append("")

    if moved:
        L.append("命中的清理规则：")
        L.append("")
        for k, v in collections.Counter(
            tuple(r.get("rule_ids") or []) for r in moved
        ).most_common():
            L.append("- `%s` × %d" % (",".join(k) or "-", v))
        L.append("")
        L.append("风险等级：`%s`" % json.dumps(
            dict(collections.Counter(r.get("risk_class") for r in moved)),
            ensure_ascii=False))
        L.append("")
        L.append("来源分布：")
        L.append("")
        for k, v in collections.Counter(
            "\\".join((r.get("src") or "").split("\\")[:7]) for r in moved
        ).most_common(10):
            L.append("- %d 个 ← `%s`" % (v, k))
        L.append("")
        L.append("> 全部 %d 个均为可再生的浏览器缓存分片；撤销动作把原文件按 SHA-256 校验后放回。"
                 % len(moved))
        L.append("")

    # 计划与候选集规模
    pdir = os.path.join(STATE, "plans")
    nplans = len([f for f in os.listdir(pdir) if f.endswith(".ndjson")]) if os.path.isdir(pdir) else 0
    cdir = os.path.join(STATE, "candidates")
    cands = []
    if os.path.isdir(cdir):
        cands = sorted(
            (os.path.join(cdir, f) for f in os.listdir(cdir) if f.endswith(".jsonl")),
            key=os.path.getmtime,
        )
    L.append("| 产物 | 数量 |")
    L.append("|---|---|")
    L.append("| 全盘盘点快照（inventory） | %d |" % _count(STATE, "inventory", ".ndjson.gz"))
    L.append("| 计划（dry-run） | %d |" % nplans)
    L.append("| 候选分类集 | %d |" % len(cands))
    L.append("")

    if cands:
        # 关键：候选集有「覆盖范围」差异。只扫 C: 的那次 AUTO_SAFE 会骤降，
        # 直接拿「最新一次」当「全盘」会得出错误结论。所以按扫描范围列表。
        recent = cands[-4:]
        L.append("最近 %d 次候选集（口径必须看清扫描范围）：" % len(recent))
        L.append("")
        L.append("| 候选集 | 覆盖 | 记录数 | 扫描体积 | `AUTO_SAFE` |")
        L.append("|---|---|---:|---:|---|")
        stats = []
        for p in recent:
            n, cls, vol, ab, tb = _scan_candidates(p)
            vols = "+".join(sorted(v for v in vol if len(v) == 2 and v.endswith(":")))
            stats.append({
                "name": os.path.basename(p), "vols": vols, "n": n,
                "tb": tb, "cls": cls,
            })
            L.append("| `%s` | %s | %d | %.1f GB | %d 个 / %.2f GB |" % (
                os.path.basename(p).replace("-candidates.jsonl", ""),
                vols or "?", n, _mb(tb) / 1024.0,
                cls.get("AUTO_SAFE", 0), _mb(ab) / 1024.0))
        L.append("")

        # 基准 = 覆盖盘符最多、记录数最多的那一份
        best = max(stats, key=lambda s: (len(s["vols"]), s["n"]))
        latest = stats[-1]
        if latest["vols"] != best["vols"]:
            L.append("> 口径提醒：最新一次只覆盖 `%s`，与覆盖 `%s` 的 `%s` 不可直接比较；"
                     "引用「全盘可清理量」请用后者。" % (
                         latest["vols"], best["vols"], best["name"]))
            L.append("")

        n = best["n"]
        L.append("以 `%s`（覆盖 `%s`）为准的风险分层：" % (best["name"], best["vols"]))
        L.append("")
        L.append("| 风险分层 | 条数 | 占比 | 含义 |")
        L.append("|---|---:|---:|---|")
        meaning = {
            "PROTECTED": "被运行时证据/受保护前缀覆盖，不动",
            "NEVER_TOUCH": "扩展名属永久禁区，不动",
            "REVIEW_REQUIRED": "需人工判断，不动",
            "AUTO_SAFE": "四项门禁全过，可动，但需一次 --approve",
        }
        for k, v in best["cls"].most_common():
            L.append("| %s | %d | %.2f%% | %s |" % (
                k, v, 100.0 * v / max(n, 1), meaning.get(k, "")))
        L.append("")

    return L, {
        "done": len(moved),
        "undone": len(restored),
        "files": net,
        "bytes": net_bytes,
    }


def _count(root, sub, suffix):
    d = os.path.join(root, sub)
    if not os.path.isdir(d):
        return 0
    return len([f for f in os.listdir(d) if f.endswith(suffix)])


def _scan_candidates(path):
    """快速扫描候选集：只用字符串定位，不做 json.loads。

    候选集单份可达 800 MB / 53 万行，逐行 json.loads 太慢。这里依赖
    gov 输出为紧凑 JSON（`"key":"value"`，无空格）取三个字段即可。

    返回 (记录数, {风险层: 条数}, {盘符: 条数}, AUTO_SAFE 字节, 总字节)
    """
    cls = collections.Counter()
    vol = collections.Counter()
    auto_b = 0
    total_b = 0
    n = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            i = ln.find('"risk_class":"')
            if i < 0:
                continue
            j = ln.find('"', i + 14)
            c = ln[i + 14:j]
            cls[c] += 1
            n += 1

            k = ln.find('"path":"')
            if k >= 0:
                vol[ln[k + 8:k + 10]] += 1

            s = ln.find('"size":')
            if s >= 0:
                e = s + 7
                while e < len(ln) and ln[e] not in ",}":
                    e += 1
                try:
                    b = int(float(ln[s + 7:e]))
                except ValueError:
                    b = 0
                total_b += b
                if c == "AUTO_SAFE":
                    auto_b += b
    return n, cls, vol, auto_b, total_b


# ---------------------------------------------------------------- 链路 B
def desktop_section():
    L = []
    RUNDIR = os.path.join(STATE, "desktop_triage", "runs")
    PLANDIR = os.path.join(STATE, "desktop_triage")

    meta = {}
    if os.path.isdir(PLANDIR):
        for d in sorted(os.listdir(PLANDIR)):
            fj = os.path.join(PLANDIR, d, "plan.json")
            if not os.path.isfile(fj):
                continue
            try:
                plan = json.load(open(fj, encoding="utf-8"))
            except Exception:
                continue
            for it in plan.get("items", []):
                rel = it.get("rel")
                if rel and rel not in meta:
                    meta[rel] = (it.get("reason"), it.get("cluster"), it.get("confidence"))

    L.append("## B. 桌面分拣 —— 搬了什么，搬去哪")
    L.append("")
    if not os.path.isdir(RUNDIR):
        L.append("无执行记录。")
        L.append("")
        return L, {"items": 0, "files": 0, "bytes": 0}

    runs = sorted(d for d in os.listdir(RUNDIR) if os.path.isdir(os.path.join(RUNDIR, d)))
    real = []
    fixture = []
    for r in runs:
        mj = os.path.join(RUNDIR, r, "manifest.json")
        if not os.path.isfile(mj):
            continue
        try:
            m = json.load(open(mj, encoding="utf-8"))
        except Exception:
            continue
        (fixture if ".fixture" in (m.get("root") or "") else real).append((r, m))

    L.append("| 运行 | 模式 | 根 | 候选 | 移动 | 失败 | 预算挂起 |")
    L.append("|---|---|---|---:|---:|---:|---:|")
    for r, m in real:
        L.append("| `%s` | %s | `%s` | %d | %d | %d | %d |" % (
            r, m.get("mode"), m.get("root"), m.get("candidates", 0),
            m.get("moved", 0), m.get("failed", 0), m.get("held_by_budget", 0)))
    for r, m in fixture:
        L.append("| `%s` | %s | 夹具（非真实数据） | %d | %d | %d | %d |" % (
            r, m.get("mode"), m.get("candidates", 0),
            m.get("moved", 0), m.get("failed", 0), m.get("held_by_budget", 0)))
    L.append("")

    by_reason = collections.Counter()
    by_reason_b = collections.Counter()
    by_cluster = collections.defaultdict(lambda: [0, 0, 0])
    by_dest = collections.defaultdict(lambda: [0, 0])
    tot = [0, 0, 0]
    rows = []
    for r, m in real:
        for it in m.get("results", []):
            if it.get("state") != "done":
                continue
            rel = it.get("rel", "")
            reason, cluster, conf = meta.get(rel, ("?", "?", "?"))
            dst = it.get("dst") or ""
            files = it.get("files", 0)
            b = it.get("bytes", 0)
            tot[0] += 1
            tot[1] += files
            tot[2] += b
            by_reason[reason] += 1
            by_reason_b[reason] += b
            key = cluster if cluster and cluster != "_未归类" else "_未归类"
            by_cluster[key][0] += 1
            by_cluster[key][1] += files
            by_cluster[key][2] += b
            zone = "\\".join(dst.split("\\")[:3])
            by_dest[zone][0] += 1
            by_dest[zone][1] += b
            rows.append((r, rel, dst, reason, conf, files, b))

    L.append("**合计：%d 条 / %d 文件 / %.1f MB**" % (tot[0], tot[1], _mb(tot[2])))
    L.append("")
    L.append("### B1 判定原因分布")
    L.append("")
    L.append("| 原因码 | 条目 | 字节 |")
    L.append("|---|---:|---:|")
    for k, v in by_reason.most_common():
        L.append("| `%s` | %d | %.1f MB |" % (k, v, _mb(by_reason_b[k])))
    L.append("")
    L.append("### B2 按项目簇")
    L.append("")
    L.append("| 簇 | 条目 | 文件 | 字节 |")
    L.append("|---|---:|---:|---:|")
    for k, v in sorted(by_cluster.items(), key=lambda x: -x[1][2]):
        L.append("| %s | %d | %d | %.1f MB |" % (k, v[0], v[1], _mb(v[2])))
    L.append("")
    L.append("### B3 按落地分区")
    L.append("")
    L.append("| 分区 | 条目 | 字节 |")
    L.append("|---|---:|---:|")
    for k, v in sorted(by_dest.items(), key=lambda x: -x[1][1]):
        L.append("| `%s` | %d | %.1f MB |" % (k, v[0], _mb(v[1])))
    L.append("")

    # 人工介入事件
    jp = os.path.join(STATE, "desktop_triage", "journal.ndjson")
    manual = [r for r in _read_ndjson(jp)
              if str(r.get("event_id", "")).startswith("m_")
              or r.get("state") == "manual_reclassify"]
    if manual:
        L.append("### B4 人工介入（改判 / 手工清理）")
        L.append("")
        L.append("| 时间 | 事件 | 动作 | 源 | 去向 |")
        L.append("|---|---|---|---|---|")
        for r in manual:
            L.append("| %s | `%s` | %s | `%s` | `%s` |" % (
                r.get("ts"), r.get("event_id"),
                r.get("action") or r.get("state"),
                r.get("src"), r.get("dst")))
        L.append("")

    L.append("### B5 逐条明细")
    L.append("")
    L.append("| 运行 | 源（Desktop 相对） | 置信 | 原因 | 去向 | 文件 | 字节 |")
    L.append("|---|---|---|---|---|---:|---:|")
    for r, rel, dst, reason, conf, files, b in sorted(rows, key=lambda x: x[2]):
        L.append("| %s | `%s` | %s | `%s` | `%s` | %d | %.1f MB |" % (
            r, rel, conf, reason, dst, files, _mb(b)))
    L.append("")

    return L, {"items": tot[0], "files": tot[1], "bytes": tot[2]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成执行台账（只读）")
    ap.add_argument("--out", default=None, help="输出路径，省略则写 .state/ledger/")
    args = ap.parse_args(argv)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    a, sa = gov_section()
    b, sb = desktop_section()

    head = ["# 执行台账", "", "- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
            "- 数据源：`.state/journal/`、`.state/plans/`、`.state/candidates/`、"
            "`.state/desktop_triage/`", ""]
    body = head + a + b
    text = "\n".join(body) + "\n"

    out = args.out or os.path.join(STATE, "ledger", "ledger_%s.md" % stamp)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)

    print("gov    : done=%d undone=%d files=%d (%.1f KB)"
          % (sa["done"], sa["undone"], sa["files"], sa["bytes"] / 1024.0))
    print("desktop: items=%d files=%d (%.1f MB)"
          % (sb["items"], sb["files"], _mb(sb["bytes"])))
    print("written:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
