"""One home for the resume line that crosses the compaction seam.

`compact-at-seam` composes it — alongside the focus, in the same model call,
from the same facts — and writes it immediately before typing `/compact`.
`postcompact-resume` reads it on the far side and types it instead of its own
generic message.

## Why a file rather than a second composer on the far side

After compaction the only context left IS the summary, so a composer asked
there would be paraphrasing its own output. The facts that make a re-prime
useful — the dirty paths, the branch, the PR state, what the last turn was
actually doing — are readable before the seam and gone after it.

## Why it expires

The typed `/compact` may never compact: the owner escapes, or types over the
line before it submits. Without a TTL that handoff waits in the state dir and
lands on whatever compaction happens next — which can be a manual one, hours
later, about different work. An expired handoff reads as absent, and the far
side falls back to its own generic message.

## Why absence is the safe value

Every failure path here returns None. The far side has a correct generic
message of its own, so losing a handoff costs one less-specific resume; typing
a stale or half-written one costs a session re-primed onto the wrong work.
"""
import json
import os
import time

# 3600s, not the 900s it shipped with. Measured 2026-09-08 on the first end-to-end
# crossing: 423 seconds between `compact-at-seam` typing the command and
# `postcompact-resume` firing — the compaction itself, plus whatever the session was
# mid-way through. That is 47% of a 900s budget spent on a single ordinary case, and
# an expiry costs the whole feature while a long TTL costs almost nothing: the
# handoff is session-scoped, consumed on read, and cleared when the send fails, so
# the only way a stale one survives is a typed `/compact` that never compacted.
TTL_S = int(os.environ.get("COMPACT_HANDOFF_TTL_S", "3600"))


def _root():
    return os.environ.get("HOOK_STATE_ROOT") or os.path.expanduser("~/.claude/state")


def _path(session):
    safe = "".join(c for c in str(session) if c.isalnum() or c in "-_")[:64] or "unknown"
    return os.path.join(_root(), "compact-handoff", safe + ".json")


def write(session, resume):
    """Leave a resume line for the far side. Falsy clears any stale one.

    Returns True only when a line is now on disk for this session."""
    if not resume or not str(resume).strip():
        clear(session)
        return False
    path = _path(session)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d" % (path, os.getpid())
        with open(tmp, "w") as fh:
            json.dump({"resume": str(resume).strip(), "ts": time.time()}, fh)
        os.replace(tmp, path)          # rename: the far side never reads half a line
        return True
    except Exception:
        return False


def read(session):
    """The resume line, or None. Missing, malformed and expired all read None."""
    try:
        with open(_path(session)) as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    line, ts = data.get("resume"), data.get("ts")
    if not isinstance(line, str) or not line.strip():
        return None
    if not isinstance(ts, (int, float)) or (time.time() - ts) > TTL_S:
        return None
    return line.strip()


def clear(session, before=None):
    """Remove the handoff. `before` keeps one written after that moment: several
    routes type a `/compact` for the same session, and a caller giving up on its
    own fire must not take the resume line a later one just composed."""
    if before is not None:
        try:
            with open(_path(session)) as fh:
                ts = json.load(fh).get("ts")
            if isinstance(ts, (int, float)) and ts > before:
                return
        except Exception:
            pass
    try:
        os.remove(_path(session))
    except Exception:
        pass


def take(session):
    """Read once and remove. A handoff crosses exactly one seam."""
    line = read(session)
    clear(session)
    return line
