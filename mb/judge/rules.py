"""The judge's rule checks: cheap, deterministic, run in code before any model call.

Each check is named in the rubric's rule_checks with its setting, e.g.
`notification_title_max_chars: 40`. A new use case brings its own rubric; a check name the
code doesn't know is an error at startup, never silently skipped.
"""
from __future__ import annotations

from typing import Callable

from mb.config import Rubric
from mb.contracts import Context, MorningBrief


def _title(b: MorningBrief, ctx: Context, n: int) -> list[str]:
    t = b.notification.title
    return [f"Notification title is {len(t)} characters (limit {n})"] if len(t) > n else []


def _body(b: MorningBrief, ctx: Context, n: int) -> list[str]:
    t = b.notification.body
    return [f"Notification body is {len(t)} characters (limit {n})"] if len(t) > n else []


def _max_surprises(b: MorningBrief, ctx: Context, n: int) -> list[str]:
    return [f"{len(b.surprises)} surprises (limit {n})"] if len(b.surprises) > n else []


def _surprise_source(b: MorningBrief, ctx: Context, on: bool) -> list[str]:
    """Every surprise has a place ID we gathered or an http(s) source link."""
    if not on:
        return []
    known = {c.place_id for c in ctx.candidates if c.place_id} | {p.place_id for p in ctx.places.values()}
    out = []
    for s in b.surprises:
        if s.place_id and s.place_id not in known:
            out.append(f"Surprise '{s.name}': place ID does not match any looked-up place")
        elif not s.place_id and not (s.source_url or "").startswith(("http://", "https://")):
            out.append(f"Surprise '{s.name}': no place ID or source link")
    return out


def _all_fields(b: MorningBrief, ctx: Context, on: bool) -> list[str]:
    if not on:
        return []
    missing = [k for k in MorningBrief.model_fields if k not in b.model_dump()]
    empty = [name for name, v in (("notification.title", b.notification.title),
                                  ("notification.body", b.notification.body),
                                  ("send_at_local", b.send_at_local)) if not v]
    if not b.timeline and not ctx.day.is_free:
        empty.append("timeline")
    return [f"Missing field: {k}" for k in missing] + [f"Empty field: {k}" for k in empty]


CHECKS: dict[str, Callable[[MorningBrief, Context, object], list[str]]] = {
    "notification_title_max_chars": _title,
    "notification_body_max_chars": _body,
    "max_surprises": _max_surprises,
    "surprise_needs_source_or_place_id": _surprise_source,
    "all_fields_present": _all_fields,
}


def validate_rules(rubric: Rubric) -> None:
    unknown = set(rubric.rule_checks) - set(CHECKS)
    if unknown:
        raise ValueError(f"{rubric.id}: unknown rule checks {sorted(unknown)}")


def run_rule_checks(brief: MorningBrief, ctx: Context, rubric: Rubric) -> list[str]:
    validate_rules(rubric)
    return [msg for name, setting in rubric.rule_checks.items() for msg in CHECKS[name](brief, ctx, setting)]
