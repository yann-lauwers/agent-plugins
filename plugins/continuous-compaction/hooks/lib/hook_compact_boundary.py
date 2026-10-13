"""The compaction boundary a session's transcript records after a `/compact` lands.

It is the only evidence that a typed compaction took effect, and the only honest
measure of what it freed: Claude Code writes one `compact_boundary` row per
compaction, carrying the context size before and after. A fill reading taken at
some later Stop measures the session's work since, not the compaction — measured
2026-09-11, three sessions were retired as "compacted without progressing" hours
after a boundary that had freed 94-97% of their tokens. `compact-at-seam` and
`compact-sweep` both judge a fire from here, so the two verdicts cannot drift.
"""
import glob
import json
import os
from datetime import datetime


def transcript_of(session, hint=None):
    """The session's transcript path: the Stop payload's own `transcript_path`
    when it names a file, else the one `~/.claude/projects/*/<session>.jsonl`."""
    if isinstance(hint, str) and os.path.isfile(hint):
        return hint
    safe = "".join(c for c in str(session) if c.isalnum() or c in "-_")[:64] or "unknown"
    hits = glob.glob(os.path.join(os.path.expanduser("~/.claude/projects"), "*",
                                  "%s.jsonl" % safe))
    return hits[0] if hits else None


def epoch(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def first_after(transcript, since):
    """{"at", "pre", "post"} for the first boundary at or after `since` (epoch),
    or None when there is none, or the transcript cannot be read. `post` is None
    on a boundary that did not record it, which judges nothing."""
    if not transcript:
        return None
    try:
        with open(transcript, errors="replace") as fh:
            for line in fh:
                if "compact_boundary" not in line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("subtype") != "compact_boundary":
                    continue
                at = epoch(row.get("timestamp"))
                if at is None or at < since:
                    continue
                meta = row.get("compactMetadata") or {}
                pre, post = meta.get("preTokens"), meta.get("postTokens")
                if not isinstance(pre, (int, float)) or pre <= 0:
                    continue
                return {"at": at, "pre": int(pre),
                        "post": int(post) if isinstance(post, (int, float)) else None}
    except Exception:
        return None
    return None


def inert(state, transcript, progress_points):
    """Apply the inert rule to a hook or sweep state dict, in place.

    Returns (basis, fresh, changed): `basis` is the standing verdict's
    {"pre", "post", "freed"} or None, `fresh` says it was judged on this call,
    and `changed` says the state must be saved. Both callers keep the same keys
    — `fill_at_last_fire`, `last_ts`, `inert`, `inert_basis`, `judged_ts`.

    An `inert` with no basis was written by the rule that compared the fill at
    the fire against the fill at some later Stop, and every one measured was
    wrong, so it no longer binds. Each fire is judged once, from its boundary."""
    changed = False
    if state.get("inert") and not state.get("inert_basis"):
        state.pop("inert")
        changed = True
    if state.get("inert"):
        return state["inert_basis"], False, changed
    prev, last = state.get("fill_at_last_fire"), state.get("last_ts")
    if prev is None or prev < 0 or not last or state.get("judged_ts") == last:
        return None, False, changed
    v = judge(prev, last, transcript, progress_points)
    if not v:
        return None, False, changed
    state["judged_ts"] = last
    if v.pop("inert"):
        state["inert"], state["inert_basis"] = True, v
        return v, True, True
    return None, False, True


def judge(fill_at_fire, fired_at, transcript, progress_points):
    """The inert verdict for one fire, or None when there is nothing to judge.

    None means no compaction landed after the fire, or its boundary recorded no
    size after — neither says whether compacting helps this session. Otherwise
    {"inert", "pre", "post", "freed"}, where `freed` is the fill points the
    compaction freed: the boundary's own before/after ratio applied to the fill
    the fire read, so no window size is needed."""
    b = first_after(transcript, fired_at)
    if not b or b["post"] is None:
        return None
    freed = float(fill_at_fire) * (b["pre"] - b["post"]) / b["pre"]
    return {"inert": freed < progress_points, "pre": b["pre"], "post": b["post"],
            "freed": round(freed, 1)}
