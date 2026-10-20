"""Command assembly (PRD section 15). Exit codes: 0 ok, 2 config/root refused,
3 budget hit, 4 unsafe state (unfinished journal actions)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional, Sequence

from . import (aiprovider, candidates as cand_mod, config, core,
               evidence as ev_mod, executor as ex_mod, external_archive,
               housekeep, index as index_mod, inventory, journal as jr_mod,
               monthly, release, report, tags, weekly)

BASE_DIR = core.norm(os.path.dirname(os.path.dirname(core.__file__)))
EXIT_OK, EXIT_CONFIG, EXIT_BUDGET, EXIT_UNSAFE = 0, 2, 3, 4


class Cli:
    def __init__(self, base_dir: str = BASE_DIR, machine: Optional[str] = None,
                 out=sys.stdout):
        self.base_dir = base_dir
        self.machine = machine or core.machine_name()
        self.out = out
        self.pol: Optional[config.Policy] = None

    def say(self, text: str = "") -> None:
        self.out.write(text + "\n")

    def load(self, *, check_python: bool = True) -> config.Policy:
        self.pol = config.load(self.base_dir, self.machine)
        if check_python:
            config.check_interpreter(self.pol)
        return self.pol

    # ---- helpers -------------------------------------------------------
    def state_path(self, *parts: str) -> str:
        assert self.pol
        return os.path.join(self.pol.state_dir, *parts)

    def journal(self) -> jr_mod.Journal:
        assert self.pol
        return jr_mod.Journal(self.pol.state_dir, self.pol.hostname,
                              backup_dir=getattr(self.pol, "journal_backup_dir", "") or "")

    def collect_refs(self) -> ev_mod.ReferencedIndex:
        assert self.pol
        path = ev_mod.load(self.pol.state_dir, self.pol.hostname)
        if not path:
            self.say("note: no evidence file yet; run `gov evidence` (PROTECTED "
                     "inference will be weaker)")
            return ev_mod.ReferencedIndex()
        rows = [ev_mod.EvidenceRow(r.get("kind", ""), r.get("raw", ""),
                                   r.get("paths", []))
                for r in core.iter_ndjson(path) if r.get("kind") != "header"]
        idx = ev_mod.ReferencedIndex.from_rows(rows)
        self.say("evidence: %s -> %s" % (path, idx.stats()))
        return idx

    def scan(self, roots: Sequence[str], on_dir=None) -> tuple[list[core.Record],
                                                              inventory.ScanStats]:
        assert self.pol
        recs: list[core.Record] = []
        budget = self.pol.new_budget()
        stats = inventory.ScanStats()
        for r in roots:
            # authorize() proves the request sits inside a declared scan root and
            # supplies the role; the walk must cover *this* path, because walking
            # decl.path would turn `--root <subdir>` into a whole-volume scan
            decl = self.pol.authorize(r, "scan")
            inventory.walk(core.norm(r), rules=self.pol, budget=budget,
                           zone_role=decl.role, emit=recs.append, stats=stats,
                           on_dir=on_dir)
            if stats.budget_hit:
                break
        stats.bytes_total = budget.bytes_total
        stats.wall_s = budget.wall_s
        return recs, stats

    def save_inventory(self, recs: Sequence[core.Record], stats: inventory.ScanStats,
                       roots: Sequence[str]) -> str:
        assert self.pol
        path = self.state_path("inventory", "%s-%s.ndjson" % (self.pol.hostname,
                                                              stats.scan_id))
        core.append_ndjson(path, {"kind": "header", "stats": stats.as_dict(),
                                  "roots": [core.norm(r) for r in roots],
                                  "policy": self.pol.as_dict()})
        # one open handle: a real scan emits 10^5..10^6 records
        core.append_ndjson_many(path, (rec.to_dict() for rec in recs))
        return path

    def load_plan(self, plan_id: str) -> list[ex_mod.Action]:
        path = self.state_path("plans", "%s.ndjson" % plan_id)
        if not os.path.isfile(path):
            raise config.PolicyError("plan not found: %s" % path)
        return [ex_mod.Action(**row) for row in core.iter_ndjson(path)
                if row.get("kind") != "header"]

    def save_plan(self, actions: Sequence[ex_mod.Action], plan_id: str) -> str:
        assert self.pol
        path = self.state_path("plans", "%s.ndjson" % plan_id)
        core.append_ndjson(path, {"kind": "header", "plan_id": plan_id,
                                  "ts": core.now_iso(), "policy": self.pol.as_dict()})
        # header first, then one handle for the actions (max_actions_per_run rows
        # at one mkdir+open each was measurable in `plan`)
        core.append_ndjson_many(path, (act.as_dict() for act in actions))
        return path

    # ---- commands ------------------------------------------------------
    def cmd_health(self, args) -> int:
        pol = self.load()
        roots = args.root or [r.path for r in pol.roots if "scan" in r.kinds]
        volumes = [inventory.volume_free(core.drive_of(r) + "\\") for r in roots]
        jr = self.journal()
        unfinished = jr.unfinished()
        toplevel = {}
        for r in roots:
            # per root: the newest snapshot may cover only one of several volumes
            snap_path = inventory.latest_snapshot(pol.state_dir, pol.hostname,
                                                 roots=[r])
            head = inventory.snapshot_header(snap_path) if snap_path else {}
            rl = core.low(core.norm(r))
            prev_top = {core.low(k): set(v)
                        for k, v in (head.get("toplevel") or {}).items()}
            if rl not in prev_top:
                toplevel[r] = {"baseline": "no snapshot covers this root yet"}
                continue
            now = {core.norm(e["path"]) for e in
                   inventory.top_shallow(r, rules=pol) if e.get("is_dir")}
            prev = {os.path.join(core.norm(r), n) for n in prev_top[rl]}
            toplevel[r] = {
                "new": sorted(os.path.basename(p) for p in now - prev),
                "gone": sorted(os.path.basename(p) for p in prev - now)}
        self.say(report.health(pol, roots, volumes, toplevel=toplevel,
                               journal_summary=jr.summary()))
        if unfinished:
            self.say("!! unfinished journal actions (crash residue): %s" % unfinished)
            return EXIT_UNSAFE
        return EXIT_OK

    def cmd_inventory(self, args) -> int:
        pol = self.load()
        roots = args.root or [r.path for r in pol.roots if "scan" in r.kinds]
        excl = list((pol.evidence or {}).get("proof_exclude") or [])
        prev = inventory.latest_snapshot(pol.state_dir, pol.hostname, roots=roots)
        proof_before = proof_meta = None
        if args.proof:
            proof_before, proof_meta = report.manifest(roots[0], pol, exclude=excl)
            self.say("AC4 proof window before: %s" % json.dumps(proof_meta,
                                                                ensure_ascii=False))
        dirs, on_dir = inventory.aggregate()
        recs, stats = self.scan(roots, on_dir=on_dir)
        path = self.save_inventory(recs, stats, roots)
        inventory.fold_rollups(dirs)
        snap = inventory.save_snapshot(pol.state_dir, stats.scan_id, dirs, stats)
        self.say(json.dumps(self._stats_view(stats), ensure_ascii=False, indent=1))
        self.say("inventory: %s" % path)
        self.say("snapshot : %s" % snap)
        self.say(report.render_rollup(dirs, roots[0], top=args.top))
        if prev and prev != snap:
            d = inventory.diff_snapshot(inventory.load_snapshot(prev), dirs, roots)
            self.say("diff vs previous snapshot (%s): added=%d removed=%d grew=%d shrank=%d"
                     % (os.path.basename(prev), len(d["added"]), len(d["removed"]),
                        len(d["grew"]), len(d["shrank"])))
            for row in d["added"][:10]:
                self.say("  + %s (%d files)" % (row["dir"], row["files"]))
        elif not prev:
            self.say("diff vs previous snapshot: skipped, no earlier snapshot "
                     "covers these roots (a different volume is not a baseline)")
        if proof_before is not None:
            after, after_meta = report.manifest(roots[0], pol, exclude=excl)
            diff = report.manifest_diff(proof_before, after)
            self.say("AC4 proof window after : %s" % json.dumps(after_meta,
                                                                ensure_ascii=False))
            self.say("AC4 proof diff: %s" % json.dumps(diff, ensure_ascii=False))
            if diff["paths_added"] or diff["paths_removed"]:
                self.say("!! paths appeared or vanished; proof FAILED (samples: %s)"
                         % diff["samples"][:4])
                return EXIT_UNSAFE
            if not diff["identical"]:
                self.say("!! path set is identical but %d files were rewritten "
                         "(excluded prefixes above do not cover them)"
                         % diff["paths_changed"])
                return EXIT_UNSAFE
        if stats.budget_hit:
            self.say("budget hit: %s" % stats.budget_hit)
            return EXIT_BUDGET
        return EXIT_OK

    @staticmethod
    def _stats_view(stats: inventory.ScanStats) -> dict:
        """Compact view: the counts that matter first, long lists capped."""
        view = stats.as_dict()
        for key in ("denied", "reparse", "broken_links"):
            view[key] = view.get(key, [])[:10]
        return view

    def cmd_evidence(self, args) -> int:
        pol = self.load()
        cfg = pol.evidence or {}
        roots = (args.reparse_root or list(cfg.get("reparse_roots") or [])
                 or [r.path for r in pol.roots if "scan" in r.kinds])
        depth = int(cfg.get("reparse_depth", 4) if args.reparse_depth is None
                    else args.reparse_depth)
        rows, meta = ev_mod.collect(pol.state_dir, reparse_roots=roots,
                                    reparse_depth=depth,
                                    timeout_s=args.timeout, rules=pol)
        path = ev_mod.save(pol.state_dir, rows, meta)
        idx = ev_mod.ReferencedIndex.from_rows(rows)
        self.say("evidence meta: %s" % json.dumps(meta, ensure_ascii=False))
        self.say("evidence index: %s" % json.dumps(idx.stats(), ensure_ascii=False))
        self.say("saved: %s" % path)
        return EXIT_OK

    def _candidates(self, roots: Sequence[str], refs) -> list[cand_mod.Candidate]:
        recs, stats = self.scan(roots)
        runner = aiprovider.Runner(self.pol)
        ai_judge = runner if runner.enabled else None
        cands = list(cand_mod.build(recs, refs, self.pol, ai_judge=ai_judge))
        if ai_judge is not None:
            self.say("ai: %s" % json.dumps(runner.stats(), ensure_ascii=False))
        return cands

    def cmd_candidates(self, args) -> int:
        pol = self.load()
        roots = args.root or [r.path for r in pol.roots if "scan" in r.kinds]
        refs = self.collect_refs() if args.evidence == "latest" else ev_mod.ReferencedIndex()
        cands = self._candidates(roots, refs)
        self.say(report.render_candidates(cands, limit=args.show))
        if args.json:
            # A candidates file is a snapshot of one classification run, not a
            # log. A per-day name plus append meant a re-run left the previous
            # verdict alive beside the new one (same path, two risk_class rows)
            # and inflated every weekly count. A timestamped snapshot per run
            # keeps history without ever mixing two verdicts for one path.
            stamp = time.strftime("%Y%m%d_%H%M%S")
            path = self.state_path("candidates", "%s-candidates.jsonl" % stamp)
            report.save_jsonl(path, [c.as_dict() for c in cands])
            self.say("candidates written: %s" % path)
            self._write_candidates_manifest(path, stamp, roots, cands)
        return EXIT_OK

    def _write_candidates_manifest(self, path: str, scan_id: str,
                                   roots, cands) -> None:
        """Sidecar manifest for a candidates snapshot.

        PHASE_SCAN_BASELINE §5: two snapshots can only be diffed if their
        caliber is *provable*. The manifest records what was covered
        (volume_coverage + roots), when (as_of), and the content hash
        (manifest_hash), so a later same-caliber diff asserts equality
        instead of assuming it. Read-only w.r.t. the snapshot itself.
        """
        vols = {}
        total_bytes = 0
        for c in cands:
            d = c.as_dict()
            vol = os.path.splitdrive(core.norm(d["path"]))[0].upper() + ":"
            cell = vols.setdefault(vol, {"entries": 0, "bytes": 0})
            cell["entries"] += 1
            cell["bytes"] += d.get("size", 0)
            total_bytes += d.get("size", 0)
        manifest = {
            "schema_version": 1,
            "scan_id": scan_id,
            "as_of": core.now_iso(),
            "as_of_epoch": time.time(),
            "snapshot_path": path,
            "manifest_hash": core.sha256_file(path),
            "entry_count": len(cands),
            "total_bytes": total_bytes,
            "volume_coverage": vols,
            "roots_requested": [str(r) for r in roots],
            "generator": "gov.cli candidates --json",
        }
        mpath = path + ".manifest.json"
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        self.say("candidates manifest: %s" % mpath)

    def cmd_plan(self, args) -> int:
        pol = self.load()
        # a plan reads its sources, so a root must be both scannable and actionable
        roots = args.root or [r.path for r in pol.roots
                             if "scan" in r.kinds and "action" in r.kinds]
        if not roots:
            self.say("no root declares kinds = [scan, action]; nothing to plan")
            return EXIT_CONFIG
        refs = self.collect_refs() if args.evidence == "latest" else ev_mod.ReferencedIndex()
        cands = self._candidates(roots, refs)
        tags_fn = (lambda c: [t for t in [tags.category_for(c.ext, pol)] if t]
                   if args.kind in ("RENAME", "TAG_SIDECAR") else None)
        plan_id, actions = ex_mod.plan(pol, cands, kind=args.kind, roots=roots,
                                       tags_fn=tags_fn, limit=args.limit, refs=refs)
        path = self.save_plan(actions, plan_id)
        self.say(report.render_plan(actions, limit=args.show))
        self.say("plan id: %s  saved: %s" % (plan_id, path))
        self.say("next: gov apply --plan %s --approve" % plan_id)
        return EXIT_OK

    def cmd_apply(self, args) -> int:
        pol = self.load()
        actions = self.load_plan(args.plan)
        jr = self.journal()
        if args.dry_run:
            out = ex_mod.apply_plan(pol, actions, jr, approved=False, dry_run=True)
        else:
            if not args.approve:
                self.say("refusing: apply needs --approve (PRD FR6)")
                return EXIT_CONFIG
            out = ex_mod.apply_plan(pol, actions, jr, approved=True,
                                     hash_files=args.hash, actor=args.actor)
        self.say(json.dumps(out, ensure_ascii=False, indent=1))
        return EXIT_OK

    def cmd_undo(self, args) -> int:
        pol = self.load()
        jr = self.journal()
        if not args.approve and not args.dry_run:
            self.say("refusing: undo needs --approve")
            return EXIT_CONFIG
        if args.event_id:
            out = ex_mod.undo_event(pol, jr, args.event_id, approved=True,
                                    dry_run=args.dry_run)
        elif args.plan:
            out = ex_mod.undo_plan(pol, jr, args.plan, approved=True,
                                   dry_run=args.dry_run)
        else:
            self.say("undo needs <event_id> or --plan <id>")
            return EXIT_CONFIG
        self.say(json.dumps(out, ensure_ascii=False, indent=1))
        return EXIT_OK

    def cmd_report(self, args) -> int:
        pol = self.load()
        jr = self.journal()
        events = jr.all_events()
        text = report.render_journal(events)
        self.say(text)
        self.say("journal summary: %s" % json.dumps(jr.summary(), ensure_ascii=False))
        if args.save:
            self.say("saved: %s" % report.save(pol.state_dir, "journal", text))
        return EXIT_OK

    def cmd_housekeep(self, args) -> int:
        pol = self.load()
        hk = (pol.housekeep or {}) if hasattr(pol, "housekeep") else {}
        hp = housekeep.plan(
            pol,
            keep=int(args.keep if args.keep is not None else hk.get("keep", 5)),
            retention_days=float(args.retention_days if args.retention_days is not None
                                 else hk.get("retention_days", 180.0)),
        )
        self.say(json.dumps(hp.as_dict(), ensure_ascii=False, indent=1))
        if args.approve:
            self.say("NOTE: execution lives outside gov/ (tools/housekeep_exec.py); "
                     "run it with --approve to apply this plan.")
        return EXIT_OK

    def cmd_release(self, args) -> int:
        pol = self.load()
        rp = release.build(pol, self.journal(), verify_hash=bool(args.verify_hash))
        text = release.render(rp)
        self.say(text)
        if args.save:
            d = os.path.join(core.norm(pol.state_dir), "release")
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, "%s-release.json" % time.strftime("%Y%m%d_%H%M%S"))
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(rp.as_dict(), fh, ensure_ascii=False, indent=1)
            self.say("manifest saved: %s" % path)
            self.say("NOTE: no delete happened. Permanent removal lives outside "
                     "gov/ — tools/release_exec.py --manifest <file> --approve")
        return EXIT_OK

    def cmd_weekly(self, args) -> int:
        pol = self.load()
        rep = weekly.build(pol, top=args.top)
        text = weekly.render(rep)
        self.say(text)
        if args.save:
            self.say("saved: %s" % report.save(pol.state_dir, "weekly", text))
        return EXIT_OK

    def cmd_external_archive(self, args) -> int:
        pol = self.load()
        plan = external_archive.plan_intake(pol)
        self.say(json.dumps(plan, ensure_ascii=False, indent=1))
        if args.save:
            d = self.state_path("external_archive")
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, "%s-external-archive.json"
                                % time.strftime("%Y%m%d_%H%M%S"))
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(plan, fh, ensure_ascii=False, indent=1)
            self.say("plan saved: %s" % path)
        self.say("NOTE: read-only plan. No business file was moved, copied or "
                 "deleted (MANAGEMENT_FIRST).")
        return EXIT_OK

    def cmd_monthly(self, args) -> int:
        pol = self.load()
        rep = monthly.build(pol)
        text = monthly.render(rep)
        self.say(text)
        if args.save:
            self.say("saved: %s" % report.save(pol.state_dir, "monthly", text))
        return EXIT_OK

    def cmd_index(self, args) -> int:
        pol = self.load()
        idx = index_mod.Index(pol.state_dir)
        inv_dir = self.state_path("inventory")
        snaps = [inventory.latest_snapshot(pol.state_dir, pol.hostname) or ""]
        invs = [os.path.join(inv_dir, n) for n in sorted(os.listdir(inv_dir))] \
            if os.path.isdir(inv_dir) else []
        counts = idx.rebuild(inventory_paths=invs[-1:], snapshot_paths=snaps,
                             journal_lines=self.journal().all_events())
        self.say("index rebuilt at %s -> %s" % (idx.path, counts))
        for row in idx.top_dirs(args.top):
            self.say("%14s %9d  %s" % (core.human_gb(row[2]), row[1], row[0]))
        return EXIT_OK

    def cmd_fixture(self, args) -> int:
        from . import fixture as fx_mod
        if args.action == "build":
            fx = fx_mod.build(args.dir)
            self.say(json.dumps({"root": fx["root"], "junction": fx["junction_created"],
                                 "notes": fx["notes"]}, ensure_ascii=False, indent=1))
            self.say("purge with: tools/purge_fixture.py %s" % fx["root"])
            return EXIT_OK
        res = fx_mod.run_acceptance(args.dir)
        self.say(res.render())
        text = res.render()
        base = args.out_dir or os.path.join(BASE_DIR, "docs", "acceptance")
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "acceptance_%s.txt" % core.date_str())
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        self.say("report saved: %s" % path)
        return EXIT_OK if not res.failed else 1

    def cmd_tag(self, args) -> int:
        pol = self.load()
        refs = self.collect_refs() if args.evidence == "latest" else None
        raw = list(args.add or [])
        t = tags.normalize_tags(raw)
        bad = [r for r in raw if r not in [str(x) for x in t]]
        if bad and not args.undo:
            self.say("refusing tag(s) outside the grammar [group-value] "
                     "(no '-', no space, no brackets): %s" % ", ".join(bad))
            return EXIT_CONFIG
        if args.undo:
            tgt, rej = tags.plan_untag(args.path, pol, refs,
                                       tier=args.tier or "FILENAME_TAG_SAFE",
                                       approved=args.approve)
        else:
            tgt, rej = tags.plan_rename(args.path, t, pol, refs, tier=args.tier,
                                         approved=args.approve)
        self.say(json.dumps({"target": tgt, "reject": rej}, ensure_ascii=False))
        if rej == "noop":
            return EXIT_OK
        if tgt is None:
            return EXIT_CONFIG
        if args.dry_run:
            return EXIT_OK
        if not args.approve:
            self.say("refusing: gov tag needs --approve (PRD FR6)")
            return EXIT_CONFIG
        act = ex_mod.Action("p_tag", jr_mod.new_event_id(), "RENAME", core.norm(args.path),
                            core.norm(tgt), "REVIEW_REQUIRED", [str(x) for x in t],
                            "manual_tag", manual=True)
        out = ex_mod.apply_plan(pol, [act], self.journal(), approved=True)
        self.say(json.dumps(out, ensure_ascii=False, indent=1))
        return EXIT_OK if out["done"] else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gov", description="local file governance")
    p.add_argument("--config", default=BASE_DIR, help="base dir holding policy/ and config/")
    p.add_argument("--machine", default=None, help="override hostname for layer selection")
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("health")
    h.add_argument("--root", action="append")
    h.set_defaults(fn="health")

    inv = sub.add_parser("inventory")
    inv.add_argument("--root", action="append")
    inv.add_argument("--top", type=int, default=20)
    inv.add_argument("--proof", action="store_true",
                     help="re-walk the first root afterwards and prove it is unchanged")
    inv.set_defaults(fn="inventory")

    ev = sub.add_parser("evidence")
    ev.add_argument("--reparse-root", action="append")
    ev.add_argument("--reparse-depth", type=int, default=None,
                    help="default: [evidence].reparse_depth from the policy")
    ev.add_argument("--timeout", type=int, default=240)
    ev.set_defaults(fn="evidence")

    c = sub.add_parser("candidates")
    c.add_argument("--root", action="append")
    c.add_argument("--show", type=int, default=25)
    c.add_argument("--evidence", choices=("latest", "none"), default="latest")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn="candidates")

    pl = sub.add_parser("plan")
    pl.add_argument("--kind", choices=("MOVE", "RENAME", "TAG_SIDECAR"), default="MOVE")
    pl.add_argument("--root", action="append")
    pl.add_argument("--limit", type=int, default=None)
    pl.add_argument("--show", type=int, default=40)
    pl.add_argument("--evidence", choices=("latest", "none"), default="latest")
    pl.set_defaults(fn="plan")

    ap = sub.add_parser("apply")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--hash", action="store_true")
    ap.add_argument("--actor", default="cli")
    ap.set_defaults(fn="apply")

    un = sub.add_parser("undo")
    un.add_argument("event_id", nargs="?")
    un.add_argument("--plan")
    un.add_argument("--approve", action="store_true")
    un.add_argument("--dry-run", action="store_true")
    un.set_defaults(fn="undo")

    rp = sub.add_parser("report")
    rp.add_argument("--save", action="store_true")
    rp.set_defaults(fn="report")

    wk = sub.add_parser("weekly")
    wk.add_argument("--top", type=int, default=20)
    wk.add_argument("--save", action="store_true")
    wk.set_defaults(fn="weekly")

    ea = sub.add_parser("external-archive",
                        help="read-only ExternalNodeIntake plan (Issue #22)")
    ea.add_argument("--save", action="store_true")
    ea.set_defaults(fn="external_archive")

    mo = sub.add_parser("monthly",
                        help="Monthly Review: retention/second-copy/Trash digest")
    mo.add_argument("--save", action="store_true")
    mo.set_defaults(fn="monthly")

    ix = sub.add_parser("index")
    ix.add_argument("action", choices=("build",))
    ix.add_argument("--top", type=int, default=15)
    ix.set_defaults(fn="index")

    fx = sub.add_parser("fixture")
    fx.add_argument("action", choices=("build", "run"))
    fx.add_argument("--dir", default=None)
    fx.add_argument("--out-dir", default=None)
    fx.set_defaults(fn="fixture")

    tg = sub.add_parser("tag")
    tg.add_argument("path")
    tg.add_argument("--add", action="append", help="group-value, e.g. 类别-文档")
    tg.add_argument("--undo", action="store_true")
    tg.add_argument("--tier", default=None)
    tg.add_argument("--approve", action="store_true")
    tg.add_argument("--dry-run", action="store_true")
    tg.add_argument("--evidence", choices=("latest", "none"), default="none")
    tg.set_defaults(fn="tag")

    hk = sub.add_parser("housekeep")
    hk.add_argument("--approve", action="store_true",
                    help="apply via tools/housekeep_exec.py (default: dry-run plan only)")
    hk.add_argument("--keep", type=int, default=None)
    hk.add_argument("--retention-days", type=float, default=None)
    hk.set_defaults(fn="housekeep")

    rl = sub.add_parser("release",
                        help="managed-trash retention manifest (read-only listing)")
    rl.add_argument("--save", action="store_true",
                    help="write the manifest JSON for tools/release_exec.py")
    rl.add_argument("--verify-hash", action="store_true",
                    help="hash every trash copy against its apply-time src_hash")
    rl.set_defaults(fn="release")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    # the console codepage on this box is GBK and real paths contain characters
    # it cannot encode (measured: UnicodeEncodeError on a private-use name)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    args = build_parser().parse_args(argv)
    cli = Cli(base_dir=args.config, machine=args.machine)
    fn = getattr(cli, "cmd_" + args.fn, None)
    if fn is None:
        cli.say("unknown command")
        return EXIT_CONFIG
    try:
        return fn(args)
    except (config.PolicyError, config.RootError, report.ManifestError) as exc:
        cli.say("REFUSED (config/root/proof): %s" % exc)
        return EXIT_CONFIG
    except (ex_mod.ExecuteError, jr_mod.JournalError) as exc:
        cli.say("REFUSED (execute/journal): %s" % exc)
        return EXIT_UNSAFE
    except KeyboardInterrupt:
        cli.say("interrupted")
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
