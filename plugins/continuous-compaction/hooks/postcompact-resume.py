#!/usr/bin/env python3
"""PostCompact — put the conversation back to work after a compaction.

Failure it prevents: a compaction that ends the turn leaves the session sitting
at an idle prompt with the task list intact and nobody to say "carry on". The
work stops for as long as it takes a human to notice, which on an unattended
run is however long the human is asleep.

`PostCompact` cannot do this by itself. Its documented decision control is
"none — used for side effects like logging or cleanup", and Claude Code discards
its `systemMessage` and `continue`. So it cannot put a message into the session
whose compaction it just watched. It rings a doorbell instead: the terminal
multiplexer the session is already running inside types for it.

  The typist is resolved from THIS HOOK'S OWN ENVIRONMENT, inherited from the
  session process — no registry, no lookup, no most-recent-session guess:

    tmux   `TMUX_PANE`        -> tmux send-keys -t <pane>
    cmux   `CMUX_SURFACE_ID`  -> cmux send --surface <uuid>

  tmux is tried first and is the one that travels: a Linux server has it, and a
  session there has to run inside it anyway to survive an SSH drop. cmux is the
  macOS laptop's terminal, and without its branch this hook is inert exactly
  where the day's work happens — which is how it first shipped, and what the
  decision row `no tmux pane owns this session (TMUX_PANE=<unset>)` was.

  Both call shapes were run end to end before being written here (2026-08-18):
  text and Enter as two calls, into a throwaway surface, verified by reading
  what arrived. Neither was assembled from parts that worked separately.

Verified end to end 2026-08-18, in a live session: the hook fired, sent through
tmux, and the session acted on the message unprompted. The decision log is the
durable evidence — `hook-decisions --check postcompact-resume` replays every
fire, and a `stamped` row carries the trigger and the typist it used.

Fires on both triggers. `auto` is the case it exists for — the window filled and
the turn may have ended with nobody there. `manual` is included because a human
who types `/compact` mid-task wants the work to carry on just as much, and the
cost of being wrong is one extra turn saying "nothing left to do". Narrow with
`POSTCOMPACT_RESUME_TRIGGERS=auto`.

MUST RUN LAST AMONG POSTCOMPACT WORK — and position in `settings.json` does not
arrange that. "All matching hooks run in parallel" is documented, so a second
wired PostCompact entry would race this one, and this one types a message that
ends the compaction seam. Anything that must complete before the session is put
back to work therefore goes INSIDE this file, above `send()`, in the order it
needs — one process, sequential, actually ordered. Do not wire a second
PostCompact handler in `settings.json`; there is no way to sequence it against
this one.

IT TYPES ONLY WHEN THE LINE IS WANTED AND THE BOX IS EMPTY. On the owner's turn —
the session's transcript shows the last turn closing on a question to him — a
typed line answers a question that was his. With a draft in the composer, the
line lands on the end of it and the Enter sends both as one message; with a
composer that cannot be read, that is not ruled out. Each of those defers: the
line is not typed, and the handoff is spent all the same.

WHAT IT TYPES, in order of preference: a line naming the host's work context
when the host names any id for this session; else the resume line the near side
composed, unless it points at a message the compaction deleted; else a generic
line. Every one of them goes through the same prompt-prefix guard.

Off switch: `touch ~/.claude/hooks/.postcompact-resume-off`.
Observe rung: `HOOK_MODE_POSTCOMPACT_RESUME=observe` records `would-stamp` or
`would-defer`, types nothing and consumes nothing.

Contract (verified against hook-development references/events-and-contracts.md
and code.claude.com/docs/en/hooks, both 2026-08-18):
  PostCompact carries no decision control and cannot block.
  Input adds `trigger` (`manual`|`auto`) and `compact_summary`.
  exit 0 always — a broken resume must never disturb a session.
"""
import json
import os
import sys

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
OFF_MARKER = os.path.join(HOOKS_DIR, ".postcompact-resume-off")
CHECK = "postcompact-resume"

sys.path.insert(0, os.path.join(HOOKS_DIR, "lib"))
sys.path.append(os.path.expanduser("~/.claude/hooks/lib"))   # the host engine, when present
try:
    import hook_decisions
except Exception:
    hook_decisions = None

# Deliberately generic. The compaction summary already holds what the work is;
# restating it here would be this hook's own guess competing with the summary's.
# The message says only "resume", and lets the context answer "at what".
#
# It is the FALLBACK. When `compact-at-seam` typed the `/compact` that produced
# this seam, it left a composed resume line in `hook_compact_handoff` — one that
# names what to re-read rather than what to do. That line is preferred because
# the summary is written as a record of history and deliberately carries no
# current state, so the session arriving here has nothing to act on until it
# re-reads. A handoff that is missing, expired or malformed reads as absent and
# this message stands.
MESSAGE = (
    "Continue working on the objectives from before the compaction. Pick up at "
    "the next unfinished step and keep going."
)

# Repeated auto-compactions in quick succession are a documented failure mode:
# when one file refills the window, Claude Code retries a few times before it
# gives up. Each retry fires this event, and without a floor between fires the
# session would collect a queue of identical nudges to work through afterwards.
MIN_SECONDS_BETWEEN_FIRES = 180


def state_root():
    return os.environ.get("HOOK_STATE_ROOT") or os.path.expanduser("~/.claude/state")


def mode():
    return "observe" if os.environ.get(
        "HOOK_MODE_POSTCOMPACT_RESUME") == "observe" else "enforce"


def wanted_triggers():
    """Which compaction triggers this hook answers.

    THE `manual` ENTRY IS LOAD-BEARING AND MUST NOT BE REMOVED. A `/compact` that
    `compact-at-seam` types into the pane arrives here as **`trigger=manual`** —
    `auto` is the harness compacting on its own threshold, which is the case this
    hook was originally written for. Measured 2026-09-08 on the first end-to-end
    crossing: `compact-at-seam stamped … resume=composed` at 15:47:27 was answered
    by `postcompact-resume stamped trigger=manual … msg=composed` at 15:54:30.

    So narrowing this to `auto` does not merely skip hand-typed compactions — it
    silently disables the entire composed-resume seam, because every compaction the
    hook layer causes is manual. Nothing errors; the far side just types its
    generic message forever."""
    raw = os.environ.get("POSTCOMPACT_RESUME_TRIGGERS", "auto,manual")
    return {part.strip() for part in raw.split(",") if part.strip()}


def record(verdict, detail, session):
    if hook_decisions is None:
        return
    try:
        # The id is a literal at the call site, never `CHECK`. The inventory
        # reconciling code against the registry reads arguments by AST, so a
        # check reporting through a variable is invisible to it and lands in
        # "declared but silent" — a clean-looking registry with a hole in it.
        hook_decisions.log("postcompact-resume", verdict, detail=detail,
                           session=session, event="PostCompact", mode=mode())
    except Exception:
        pass


def too_soon(session):
    """True when this session fired recently enough that another nudge would
    stack. Uses mtime rather than a stored clock: one file, no parsing, and a
    missing state directory reads as "never fired" rather than raising."""
    path = os.path.join(state_root(), "postcompact-resume", str(session))
    try:
        import time
        last = os.path.getmtime(path)
        return (time.time() - last) < MIN_SECONDS_BETWEEN_FIRES, path
    except Exception:
        return False, path


def stamp(path):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("")
    except Exception:
        pass


sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
sys.path.append(os.path.expanduser("~/.claude/hooks/lib"))   # the host engine, when present
try:
    from hook_typist import typist, draft_in_composer   # one home for the multiplexer
except Exception:                            # copied away from its lib: inert, loudly
    def typist():
        return None, None

    def draft_in_composer():                 # unreadable: defers, never types blind
        return None
try:
    import hook_compact_handoff             # the resume line, from the near side
except Exception:
    hook_compact_handoff = None
try:
    import hook_compact_focus               # the typed line's guards and the work context
except Exception:
    hook_compact_focus = None
try:
    import hook_turn_standing
except Exception:
    hook_turn_standing = None
try:
    import hook_compact_boundary
except Exception:
    hook_compact_boundary = None


def owners_turn(session, payload):
    """True only on a transcript that positively shows the last turn closing on a
    question to the owner. Absent, unreadable or malformed all read False: a
    reading that cannot be taken withholds nothing, because withholding costs
    the unattended session its only way back to work."""
    if hook_turn_standing is None or hook_compact_boundary is None:
        return False
    try:
        return hook_turn_standing.owners_turn(
            hook_compact_boundary.transcript_of(session, payload.get("transcript_path")))
    except Exception:
        return False


def names_a_referent(text):
    # Copied away from its lib, the rule cannot be read, so no composed line is
    # trusted: the generic one is always safe to type, and a second copy of the
    # rule here would drift from the one the lib keeps.
    if hook_compact_focus is None:
        return True
    return hook_compact_focus.names_a_referent(text)


def main():
    if os.path.exists(OFF_MARKER):
        return

    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    session = payload.get("session_id") or "unknown"

    # A subagent owns its own context window and its own compaction. The pane
    # belongs to the parent session, so typing there would answer a child's
    # seam with a message in the user's conversation.
    if payload.get("agent_id"):
        return

    trigger = payload.get("trigger") or "unknown"
    if trigger not in wanted_triggers():
        return

    target, send = typist()
    if send is None:
        record("error", "no multiplexer owns this session "
               "(TMUX_PANE=%s CMUX_SURFACE_ID=%s)"
               % (os.environ.get("TMUX_PANE") or "<unset>",
                  os.environ.get("CMUX_SURFACE_ID") or "<unset>"), session)
        return

    recent, path = too_soon(session)
    if recent:
        record("error", "cooldown — fired under %ds ago" % MIN_SECONDS_BETWEEN_FIRES,
               session)
        return

    # Deferring comes BEFORE the stamp: a seam that typed nothing is not a nudge
    # the next one would stack on, so it must not start the cooldown. The owner's
    # turn is read first, and on it the composer is never read at all.
    why = None
    if owners_turn(session, payload):
        why = "owner's turn"
    else:
        # None is UNREADABLE and is not False — folding them together is how an
        # unreadable capture reads as "the box is empty".
        draft = draft_in_composer()
        if draft:
            why = "owner is typing"
        elif draft is None:
            why = "composer unreadable"
    if why:
        if mode() == "observe":
            record("would-defer", "why=" + why, session)
            return
        # The handoff described THIS compaction; left on disk it lands on the next.
        if hook_compact_handoff:
            hook_compact_handoff.clear(session)
        record("deferred", "why=" + why, session)
        return

    # A seam for this session has completed, so a handoff left for it is spent
    # here whether or not it gets typed — it described THIS compaction, and left
    # on disk it would land on the next one. Observe reads without consuming:
    # a rung that changes nothing must not change this either.
    resume = None
    if hook_compact_handoff:
        resume = (hook_compact_handoff.read(session) if mode() == "observe"
                  else hook_compact_handoff.take(session))
    kind = "composed" if resume else "generic"
    if resume and names_a_referent(resume):
        resume, kind = None, "rejected:referent"
    message = resume or MESSAGE
    # The host's work context outranks both: ids the session observably touched
    # are facts, where a composed line is a model's reading of them.
    context = None
    if hook_compact_focus is not None:
        context = hook_compact_focus.work_context_line(
            hook_compact_focus.work_context_of(session))
    if context:
        message, kind = context, "work-context"
    # Defence in depth. The composer already defuses this before the handoff is
    # written, so nothing this build produces arrives dangerous — but a handoff
    # left by an EARLIER build, or one written by hand, reaches here untouched,
    # and this is the line that actually types into the prompt. A leading `!`
    # is shell mode; the guard belongs at the write AND at the send.
    if hook_compact_focus is not None:
        message = hook_compact_focus.defuse_prompt_prefix(message)
    elif message.startswith(("!", "/", "#", "@")):
        # Copied away from its lib, like every other import in this file.
        # Inline the same rule rather than typing an unguarded line.
        message = "Continue: " + message

    if mode() == "observe":
        record("would-stamp", "trigger=%s via=%s msg=%s" % (trigger, target, kind),
               session)
        return

    # ---- anything that must finish BEFORE the session resumes goes here ----
    # This send is the end of the compaction seam: the moment it lands, the
    # session is working again. A sibling wired separately in settings.json
    # would run in parallel with it, not before it, so post-compact work that
    # has to complete first belongs above this line and inside this process.
    # ------------------------------------------------------------------------

    stamp(path)
    try:
        send(message)
    except Exception as exc:
        record("error", "send via %s failed: %r" % (target, exc), session)
        return

    record("stamped", "trigger=%s via=%s msg=%s" % (trigger, target, kind), session)


if __name__ == "__main__":
    # The recorder is optional by construction. A hook copied away from its lib
    # must still run: crashing on a missing import is the very failure this
    # wrapper exists to record.
    try:
        from hook_run import run as _run
    except Exception:
        def _run(_check, fn, event=None):
            return fn() or 0
    sys.exit(_run(CHECK, main, event="PostCompact"))
