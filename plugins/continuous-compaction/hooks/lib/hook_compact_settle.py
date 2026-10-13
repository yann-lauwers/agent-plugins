"""What a typed `/compact` became, read back from the session's own transcript.

A typist's exit code says the keys reached the terminal, never that anything
compacted. Measured over the 37 fires `compact-at-seam` typed between 2026-09-08
and 2026-09-11: 32 compacted, 4 were appended to a draft already sitting in the
composer and submitted inside it, and 1 ran and compacted nothing. So a fire is
PENDING when it is typed, and a later reading of the transcript — Claude Code's
record of every input and every compaction — settles it.

A compaction after the fire settles it whatever else the transcript holds, and is
therefore read first: a landed compaction leaves BOTH its own boundary and the
`/compact` row that caused it, so reading the typed text first would call every
real compaction "ran and nothing followed". Failing a boundary, the transcript is
read past the size it had at the send, so nothing typed earlier in the session can
settle this fire.

The rule lives in this module rather than in the hook so that any route which types
a `/compact` settles it the same way, against one copy of the transcript's shapes.

Every reading fails toward "nothing shown yet". Those shapes are Claude Code's own
and undocumented, and a shape that moves must leave fires unconfirmed — spent,
but never judged as compactions — rather than lost and retyped on every Stop.
"""
import json
import os

try:
    import hook_compact_boundary
except Exception:
    hook_compact_boundary = None

# Long enough to be this fire's own text, short enough to survive being cut where
# the terminal wraps or a draft is glued in front of it. It carries the command
# itself, not the focus alone: a row that merely QUOTES the focus — a task
# notification, a log line read back into the session — then decides nothing.
MARK_LEN = 56

# How long a `/compact` may sit in the input queue before the fire is given up on.
# A queued one is normally run the moment the turn it is behind ends, so the bound
# is not about patience: it is about a queue entry that will never run at all —
# flushed wholesale, or left behind by a session that was killed and resumed.
# Measured over 8,994 transcripts: 216 queue entries across 137 sessions ended
# stranded. Without a bound each of those strands the check for the session's life.
# The multiple is tied to the handoff's own expiry: once the resume line composed
# for this seam can no longer cross it, holding the seam open buys nothing.
QUEUED_WINDOWS = 4

_LOST_WHY = {
    "absorbed": "the typed /compact was submitted inside another input",
    "removed": "the queued /compact was taken out of the queue before it ran",
    "ran": "the /compact ran and no compaction followed",
}


def begin(pct, focus, transcript, now):
    """The pending record for `/compact <focus>`, taken BEFORE the keys are sent:
    `offset` is where this fire's evidence starts in the transcript."""
    try:
        offset = os.path.getsize(transcript) if transcript else None
    except OSError:
        offset = None
    return {"ts": now, "pct": pct, "offset": offset,
            "mark": ("/compact " + focus.strip())[:MARK_LEN]}


def _typed(row):
    """(route, text) for a record carrying typed input, or (None, None).

    A typed line reaches the transcript as a queue entry when a turn is busy and
    as a user entry when it runs. `remove` and `popAll` both carry the text they
    are dropping; `dequeue` carries none, and the user entry that follows it is
    what says the command ran. A tool result or an attachment arrives as a list,
    never as a string, so it can quote the focus without deciding anything."""
    if row.get("type") == "queue-operation":
        if row.get("operation") in ("enqueue", "remove", "popAll"):
            return row["operation"], row.get("content")
        return None, None
    if row.get("type") == "user" and not row.get("isMeta") and not row.get("isCompactSummary"):
        content = (row.get("message") or {}).get("content")
        if isinstance(content, str):
            return "user", content
    return None, None


def _is_compact(text):
    return (text.lstrip().startswith("/compact")
            or "<command-name>/compact</command-name>" in text)


def fate(transcript, offset, mark):
    """'absorbed' | 'removed' | 'ran' | 'queued' | None, from the records written
    after `offset`.

    The first record carrying `mark` decides whether the typed text stayed a
    command of its own: inside anything that is not a `/compact`, it was
    submitted as part of somebody else's input. Once it is a queued `/compact`,
    later records can only move it on — dropped from the queue, or run."""
    if not transcript or offset is None or not mark:
        return None
    queued = False
    try:
        with open(transcript, "rb") as fh:
            fh.seek(offset)
            for raw in fh:
                if b"/compact" not in raw:
                    continue
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                route, text = _typed(row)
                if not isinstance(text, str) or mark not in text:
                    continue
                if not _is_compact(text):
                    if not queued:
                        return "absorbed"
                    continue
                if route == "user":
                    return "ran"
                if route in ("remove", "popAll"):
                    return "removed"
                queued = True
    except OSError:
        return None
    return "queued" if queued else None


def _spend(state, pending, baseline):
    """A fire that counts: dated from the moment it was typed, and with its fill as
    the inert rule's baseline only when a compaction was seen to judge."""
    state.pop("pending", None)
    state["fires"] = state.get("fires", 0) + 1
    state["last_ts"] = pending["ts"]
    if baseline:
        state["fill_at_last_fire"] = pending.get("pct")
        state.pop("lost", None)          # the count bounds a RUN of fires that do
                                         # not land; one that lands ends the run
    else:
        state.pop("fill_at_last_fire", None)


def settle(state, transcript, now, window):
    """Settle `state["pending"]` in place and return (outcome, detail).

    `landed`      a compaction boundary after the fire — the fire is spent.
    `lost`        the typed text never became a compaction — it counts toward
                  `lost` and spends nothing, because nothing else ever moved.
    `unconfirmed` nothing has shown what it became within the window — spent, with
                  no baseline, so it is never judged as a compaction.
    `pending`     undecided. A `/compact` still queued behind a turn is this until
                  the queued bound: typing a second one over it would compact twice.
    (None, None) when there is no pending fire."""
    pending = state.get("pending")
    if not isinstance(pending, dict):
        return None, None
    ts = pending.get("ts")
    if not isinstance(ts, (int, float)):
        # Nothing to read the transcript after, so this fire can never be settled
        # on evidence. It is spent rather than dropped: a fire was typed.
        state.pop("pending", None)
        state["fires"] = state.get("fires", 0) + 1
        state.pop("fill_at_last_fire", None)
        return "unconfirmed", "why=the pending record carried no send time"

    boundary = (hook_compact_boundary.first_after(transcript, ts)
                if hook_compact_boundary and transcript else None)
    if isinstance(boundary, dict):
        _spend(state, pending, baseline=True)
        return "landed", "pct=%s tokens=%s->%s after=%ds" % (
            pending.get("pct"), boundary.get("pre"), boundary.get("post"),
            int(boundary.get("at", ts) - ts))

    became = fate(transcript, pending.get("offset"), pending.get("mark"))
    if became in _LOST_WHY:
        state.pop("pending", None)
        state["lost"] = state.get("lost", 0) + 1
        return "lost", "why=%s n=%d" % (_LOST_WHY[became], state["lost"])
    waited = int(now - ts)
    if became == "queued":
        if waited < window * QUEUED_WINDOWS:
            return "pending", "why=a typed /compact is queued %ds" % waited
        _spend(state, pending, baseline=False)
        return "unconfirmed", "why=the /compact sat queued %ds and never ran" % waited
    if waited < window:
        return "pending", "why=a typed /compact is unsettled %ds" % waited
    _spend(state, pending, baseline=False)
    return "unconfirmed", "why=nothing in the transcript settled it within %ds" % window
