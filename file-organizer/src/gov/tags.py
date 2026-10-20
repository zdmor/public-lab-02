"""Filename tag engine: format / parse / pre-flight rename plan (PRD 11).

Canonical segment: `__[<group>-<value>]`, appended to the basename *before*
the extension so the extension stays routable. Parsing is right-to-left and
pure; format(parse(x)) == x for anything this module can emit, and format()
is idempotent for a repeated group-value pair.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from . import core

GROUP_CHARS = r"[^\[\]\s_-]+"
VALUE_CHARS = r"[^\[\]\s_-]+"
SEGMENT = re.compile(r"^(?P<base>.*)__\[(?P<g>%s)-(?P<v>%s)\]$"
                     % (GROUP_CHARS, VALUE_CHARS), re.S)
_TAG_STR = re.compile(r"^%s-%s$" % (GROUP_CHARS, VALUE_CHARS))


@dataclass(frozen=True)
class Tag:
    group: str
    value: str

    def __str__(self) -> str:
        return "%s-%s" % (self.group, self.value)

    @classmethod
    def parse(cls, text: str) -> Optional["Tag"]:
        text = (text or "").strip()
        if _TAG_STR.match(text):
            g, _, v = text.partition("-")
            return cls(g, v)
        if "-" in text:
            g, _, v = text.partition("-")
            if _TAG_STR.match("%s-%s" % (g, v)):
                return cls(g, v)
        return None


def normalize_tags(tags: Iterable) -> list[Tag]:
    out: list[Tag] = []
    seen = set()
    for t in tags:
        if isinstance(t, str):
            parsed = Tag.parse(t)
            if parsed is None:
                continue
            t = parsed
        if not isinstance(t, Tag):
            continue
        if str(t) in seen:
            continue
        seen.add(str(t))
        out.append(t)
    return out


def split_ext(name: str) -> tuple[str, str]:
    base, ext = os.path.splitext(name)
    return base, ext


def parse_name(name: str) -> tuple[str, str, list[Tag]]:
    """`report_v2__[类别-文档].docx` -> ('report_v2', '.docx', [Tag(类别,文档)])."""
    base, ext = split_ext(os.path.basename(name))
    tags: list[Tag] = []
    while True:
        m = SEGMENT.match(base)
        if not m:
            break
        tags.append(Tag(m.group("g"), m.group("v")))
        base = m.group("base")
    if not base:  # a name made only of tags is not a name we recognize
        return os.path.basename(name), ext, []
    return base, ext, list(reversed(tags))


def format_name(base: str, ext: str, tags: Iterable) -> str:
    return "%s%s%s" % (base, "".join("__[%s]" % t for t in normalize_tags(tags)), ext)


def parse_path(path: str) -> tuple[str, str, str, list[Tag]]:
    d = core.norm(os.path.dirname(path))
    base, ext, tags = parse_name(os.path.basename(path))
    return d, base, ext, tags


def add_tags(path: str, tags: Iterable) -> str:
    d, base, ext, cur = parse_path(path)
    return os.path.join(d, format_name(base, ext, list(cur) + list(tags)))


def remove_tags(path: str, tags: Iterable) -> str:
    d, base, ext, cur = parse_path(path)
    drop = {str(t) if isinstance(t, str) else str(t) for t in normalize_tags(tags)}
    kept = [t for t in cur if str(t) not in drop]
    return os.path.join(d, format_name(base, ext, kept))


def category_for(ext: str, pol) -> Optional[Tag]:
    table = pol.tag.get("category_by_ext", {}) if pol.tag else {}
    val = table.get((ext or "").casefold())
    return Tag(str(pol.tag.get("canonical_group", "类别")), str(val)) if val else None


def sidecar_path(path: str) -> str:
    return core.norm(path) + ".tags.json"


def plan_rename(path: str, tags: Sequence, pol, refs=None,
                *, tier: Optional[str] = None, approved: bool = False,
                exists=None, replace: bool = False
                ) -> tuple[Optional[str], Optional[str]]:
    """Return (new_path, None) or (None, reject_reason). Never truncates.

    `replace=True` makes the result carry exactly `tags` (used by untag);
    the default appends them to what the name already carries.
    """
    path = core.norm(path)
    if not os.path.lexists(path):
        return None, "missing_source"
    tier = tier or _tier_for(path, pol)
    if tier == "NEVER_RENAME":
        return None, "never_rename"
    if tier == "TAG_WITH_REVIEW" and not approved:
        return None, "review_only"
    if refs is not None and refs.is_referenced(path):
        return None, "referenced"
    d, base, ext, cur = parse_path(path)
    blocked = pol.action_target_ok(d)
    if blocked:
        return None, ("frozen_zone" if blocked == "frozen_zone" else "not_safe")
    final = [] if replace else list(cur)
    new_name = format_name(base, ext, final + list(tags))
    if new_name == os.path.basename(path):
        return None, "noop"
    target = core.norm(os.path.join(d, new_name))
    reason = core.check_long_path(target, pol.max_target_path)
    if reason:
        return None, "too_long"
    if core.drive_of(path) != core.drive_of(target):
        return None, "cross_volume"
    exists = exists or os.path.lexists
    if exists(target):
        return None, "collision"
    return target, None


def plan_untag(path: str, pol, refs=None, *, tier: Optional[str] = None,
               approved: bool = False, exists=None
               ) -> tuple[Optional[str], Optional[str]]:
    d, base, ext, cur = parse_path(path)
    if not cur:
        return None, "noop"
    return plan_rename(path, [], pol, refs, tier=tier, approved=approved,
                       exists=exists, replace=True)


def _tier_for(path: str, pol) -> str:
    return core_rename_tier(os.path.basename(path), pol)


def core_rename_tier(name: str, pol) -> str:
    from . import policy as _policy
    return _policy.rename_safety(name, core.ext_of(name), pol)


def describe(path: str) -> dict:
    d, base, ext, tags = parse_path(path)
    return {"dir": d, "basename": base, "ext": ext,
            "tags": [str(t) for t in tags],
            "untagged_name": format_name(base, ext, [])}
