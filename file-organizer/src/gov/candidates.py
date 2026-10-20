"""Candidate queue construction (PRD FR5). Every line must carry evidence."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Optional, Sequence

from . import core, policy as pol_mod

MIN_EVIDENCE_KEYS = ("rule_hit", "age_days", "referenced", "size")


class CandidateError(Exception):
    pass


@dataclass
class Candidate:
    path: str
    size: int
    mtime: float
    ext: str
    risk_class: str
    rename_safety: str
    suggested_action: str
    rule_ids: list
    evidence: dict
    unknown: bool = False
    ai: Optional[dict] = None

    def as_dict(self) -> dict:
        return {"path": self.path, "size": self.size, "mtime": self.mtime,
                "ext": self.ext, "risk_class": self.risk_class,
                "rename_safety": self.rename_safety,
                "suggested_action": self.suggested_action,
                "rule_ids": list(self.rule_ids), "evidence": dict(self.evidence),
                "unknown": self.unknown, "ai": self.ai}

    def validate(self) -> None:
        if not self.evidence:
            raise CandidateError("candidate without evidence: %s" % self.path)
        if self.risk_class not in core.RISK_CLASSES:
            raise CandidateError("candidate with bad risk_class: %s" % self.path)
        if self.risk_class == core.RISK_CLASSES[0]:  # AUTO_SAFE
            for k in ("age_ok", "no_reference_ok", "regenerable_ok"):
                if not self.evidence.get(k):
                    raise CandidateError(
                        "AUTO_SAFE candidate missing gate %s: %s" % (k, self.path))


def build(records: Iterable[core.Record], refs, pol, *,
          ai_judge: Optional[Callable] = None,
          classify: Optional[Callable] = None) -> Iterator[Candidate]:
    """Yield one Candidate per record; AUTO_SAFE requires the full gate."""
    classify = classify or (lambda rec: pol_mod.classify(rec, refs, pol))
    for rec in records:
        if rec.is_dir or rec.is_reparse:
            continue
        dec = classify(rec)
        cand = Candidate(path=rec.path, size=rec.size, mtime=rec.mtime,
                         ext=rec.ext, risk_class=dec.risk_class,
                         rename_safety=dec.rename_safety,
                         suggested_action=dec.suggested_action,
                         rule_ids=list(dec.rule_ids), evidence=dict(dec.evidence),
                         unknown=dec.unknown)
        cand.evidence.setdefault("rule_hit", bool(dec.rule_ids and dec.rule_ids != [""]))
        cand.evidence.setdefault("age_days", round(rec.age_days, 2))
        cand.evidence.setdefault("referenced", cand.evidence.get("referenced", []))
        cand.evidence.setdefault("size", rec.size)
        if ai_judge is not None and pol_mod.needs_ai(dec):
            cand.ai = ai_judge(rec, dec)
            if cand.ai is None or cand.ai.get("error"):
                cand.risk_class = core.RISK_CLASSES[1]  # fail-closed
                cand.evidence["ai_error"] = (cand.ai or {}).get("error", "no_judgment")
        cand.validate()
        yield cand


def summarize(cands: Sequence[Candidate]) -> dict:
    out: dict[str, dict] = {}
    for c in cands:
        bucket = out.setdefault(c.risk_class, {"count": 0, "bytes": 0, "rules": {}})
        bucket["count"] += 1
        bucket["bytes"] += c.size
        for rid in (c.rule_ids or [""]):
            r = bucket["rules"].setdefault(rid or "no_rule",
                                           {"count": 0, "bytes": 0})
            r["count"] += 1
            r["bytes"] += c.size
    return out


def cleanable(cands: Sequence[Candidate]) -> list[Candidate]:
    return [c for c in cands if c.risk_class == core.RISK_CLASSES[0]]


def by_rule(cands: Sequence[Candidate]) -> dict:
    out: dict[str, list[Candidate]] = {}
    for c in cands:
        for rid in (c.rule_ids or ["no_rule"]):
            out.setdefault(rid or "no_rule", []).append(c)
    return out
