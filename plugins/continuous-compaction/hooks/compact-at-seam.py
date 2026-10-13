#!/usr/bin/env python3
"""Stop — compact a filling session at a seam, with a focus composed for it.

## The failure this prevents

A long run fills its window and hits the wall mid-slice. What is lost is not
tokens but the thread: the ticket, the step in progress, the decisions taken.
Measured on this machine, 171 of 219 sessions in one week went past 180k and only
21 ever compacted — the window is not being managed, and the owner's own remedy
is to hand-write a continuation brief inside `/compact`.

## Why a typist rather than a decision

A session cannot compact itself. The SlashCommand tool's own text excludes
built-in CLI commands and `/compact` is one; `PreCompact` carries a veto only and
`PostCompact` is side-effects-only, so no hook event can start a compaction.
The one route that works is typing into the session's own pane — the same route
`postcompact-resume.py` proved end to end on 2026-08-18, and the resolution now
lives in `lib/hook_typist.py` so both callers read one copy.

## Why Stop

Stop is the only event whose payload proves no tool call is in flight for this
session, because it fires after the turn ends. It is also where the peer blockers
live, and a compaction typed into a turn another handler is re-opening lands in
the middle of the work it re-opened.

Contract (verified against hook-development references/events-and-contracts.md):
  This hook NEVER emits `decision`. A Stop hook that blocked would hold the turn
  open, which is the opposite of its job. Every path exits 0 and prints nothing.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
# The host's own hook engine, when this machine has one. `hook_decisions` and
# `hook_run` live there and nowhere else; the imports below already degrade to
# None without them, so this only decides whether a machine that HAS the engine
# keeps its decision log. Appended rather than inserted: the plugin's own copies
# of the composer, the handoff and the typist always win.
sys.path.append(os.path.expanduser("~/.claude/hooks/lib"))
try:
    import hook_decisions
except Exception:
    hook_decisions = None
try:
    import hook_run
except Exception:
    hook_run = None
try:
    from hook_compact_focus import facts as _facts, compose as _compose, dispatched
except Exception:                      # copied away from its lib: composer is gone,
    def _facts(cwd, running=None, session=None):   # so every fire falls back
        return {}                                  # rather than crashing

    def _compose(facts, close):
        return ("Preserve the ticket, the step in progress, the files changed and the "
                "decisions taken. Continue at the first step whose evidence is absent."),\
               "", "fallback"

    # The shared rule, inline: a gate copied away from its lib must not open wider.
    def dispatched(task):
        kind = str(task.get("type") or task.get("task_type") or "").lower()
        if not kind:
            return True
        if "teammate" in kind:
            return False
        return kind in ("subagent", "workflow", "agent") or kind.endswith("_agent")
try:
    import hook_compact_handoff
except Exception:
    hook_compact_handoff = None
try:
    import hook_compact_boundary as _boundary
except Exception:                      # no reader, no inert verdict: never a false one
    _boundary = None
try:
    import hook_compact_settle
except Exception:                      # copied away from its lib: a fire is spent as typed
    hook_compact_settle = None
try:
    from hook_typist import typist, draft_in_composer
except Exception:                      # copied away from its lib: run unwrapped
    def typist():
        return None, None

    def draft_in_composer():
        return None

CHECK_ID = "compact-at-seam"
MODE_ENV = "HOOK_MODE_COMPACT_AT_SEAM"
REENTRY = ("COMPACT_AT_SEAM_COMPOSING", "STOP_UNFINISHED_JUDGING")

def _option(name, legacy_env, default):
    """A `userConfig` value, the legacy environment variable, then the default.

    Claude Code exports every declared option as CLAUDE_PLUGIN_OPTION_<KEY>, so
    the plugin's own manifest is the first source. The legacy variable is read
    second and deliberately: it is what the hand-installed copy of this hook was
    wired with, so a machine mid-cutover keeps the value it already had rather
    than silently reverting to the default."""
    for var in ("CLAUDE_PLUGIN_OPTION_" + name.upper(), legacy_env):
        raw = os.environ.get(var, "").strip()
        if raw:
            return raw
    return default


PCT_FLOOR = int(float(_option("pct_floor", "COMPACT_AT_SEAM_PCT", "80")))
# 🔑 THE POINT PAST WHICH NOTHING DEFERS.
#
# Every gate below the floor answers "should this session compact NOW", and each
# is a reason to wait. None of them was a reason to wait FOREVER, and one of them
# turned out to be exactly that: an orchestrator holds subagents at every Stop by
# construction, so the background gate deferred it on every turn while its fill
# only climbed. Measured 2026-09-10 on a live session — 81%, two agents in
# flight, its standing instruction "keep the fleet at 4, refill as they land", so
# the count never reaches zero and the deferral never ends. It dies at 100%.
#
# Deferring protects a dispatched agent's RESULT: compaction replaces context, so
# a task completing afterwards re-invokes a session that may no longer know why
# it started. That is worth protecting and it is not worth the session. Past this
# reading the pending work stops being a veto and becomes a fact — `_facts()`
# already hands the composer every running task, and the composer's prompt
# already requires the focus to name what each was started for, so the dispatch
# intent survives in the summary rather than being lost with everything else.
#
# Anthropic's own auto-compaction gates on NOTHING and fires at the limit, which
# is why it cannot strand a session this way. This hook replaces it — measured on
# this machine, `autoCompactEnabled` is false wherever it runs — so it inherits
# that obligation. A ceiling is how a gate keeps its protection without becoming
# the thing that kills the session.
PCT_CEILING = int(float(_option("pct_ceiling", "COMPACT_AT_SEAM_CEILING", "85")))
# 🔑 HOW LONG DISPATCHED WORK MAY HOLD THIS SESSION'S GATE — the session's hold,
# never one task's age.
#
# The set is live and true: the vendor's `Stop` payload drops a task the moment it
# finishes (agent-sdk-research `background-tasks.md` §3.4, observed), and read
# against transcripts on 2026-09-11, 163 of the 168 vetoed ids that resolved were
# still running at their Stop — p50 9 min, p90 43 min past it.
#
# It still never opened, because an orchestrator's dispatches OVERLAP: of the 163
# Stops that followed a vetoed task's end, 122 were vetoed again by a different,
# younger task. Ageing each task by its own id saw a fresh id at every Stop, so a
# session could be held for a day by tasks none of which was ever stale. The clock
# that can run out is the session's: it starts at the first Stop the gate defers at
# or above the floor, survives every change of which tasks are present, and clears
# at the first Stop nothing holds. Past it, the pending work stops vetoing and is
# handed to the composer instead, which names what each task was started for.
#
# The userConfig key keeps its original name so every value already written to an
# installed `pluginConfigs` keeps binding.
HOLD_S = int(float(_option("task_stale_s", "COMPACT_AT_SEAM_TASK_STALE_S", "1800")))
COOLDOWN_S = int(os.environ.get("COMPACT_AT_SEAM_COOLDOWN_S", "900"))
# The budget is a backstop against a pathological loop, and 3 made it a cap on how
# long a session may live as well — by accident, because the two were never
# separated. It is spent after about a day of ordinary work, and the session then
# rides to 100% with nothing able to reach it: measured 2026-09-06..12, four of 341
# sessions reached the bound and not one of them was looping. 9a6b1656 fired at
# 11:09, 07:54 and 13:08 across 26 hours, then ran 20 more at 97%; 86b23896 spread
# its three over 15 hours and sat at 77% for 16 more. What keeps the ordinary case
# honest is not this number and is unchanged: the cooldown caps it at four an hour,
# and the inert rule retires a session whose compaction frees fewer than
# PROGRESS_POINTS — which is what actually stopped two of those four. 9 covers a
# ~72h session at the measured cadence and is still a hard stop.
MAX_FIRES = int(os.environ.get("COMPACT_AT_SEAM_MAX_FIRES", "9"))
# The lost bound does NOT follow the budget. It
# answers a different question: a lost fire is the keys landing inside somebody's
# unfinished draft and submitting it, so this is how many times a pane may swallow
# a `/compact` before the check stops typing into it. That has nothing to do with
# how long the session has been alive, and leaving the two coupled would have paid
# for a longer session with six more chances to submit a draft nobody wrote.
MAX_LOST = int(os.environ.get("COMPACT_AT_SEAM_MAX_LOST", "3"))
PROGRESS_POINTS = 15        # a landed compaction freeing fewer fill points is inert
PEER_WAIT_S = int(os.environ.get("COMPACT_AT_SEAM_PEER_WAIT_S", "95"))
                            # A WINDOW FROM THIS HOOK'S START, not a wait that begins
                            # when the peer is read — the compose below runs inside it,
                            # so the usual path spends nothing here at all.
                            #
                            # 25s was budgeted against a judge measured at 7-22s, and
                            # the judge got slower: over 975 judged stops to 2026-09-10
                            # it ran p50 34.9s, p90 63.7s, max 88.9s, and 676 of them
                            # passed 25s. Every one deferred as `peer verdict unread` —
                            # one session sat at 71% deferring on four consecutive
                            # Stops, which is the feature being off rather than a rare
                            # miss. 95 covers the judge's own 90s handler timeout, past
                            # which no row can arrive at all.
                            #
                            # Budget: 15 facts + 90 compose runs FIRST and inside this
                            # window, then whatever the window has left, then 10
                            # capture-pane — inside the 180s this handler is wired with.
# Only `stop-unfinished` — it is the LAST arm in that file to resolve. `design-owed`
# writes its row before the judge is called, so a `silent` from it at t~0.5s says
# nothing about a judge block landing at t~15s, and reading it as terminal is how
# this hook would type into a turn the judge had re-opened.
PEER_CHECKS = ("stop-unfinished",)

STATE_ROOT = os.environ.get("HOOK_STATE_ROOT", os.path.expanduser("~/.claude/state"))
STATE_DIR = os.path.join(STATE_ROOT, "compact-at-seam")
DECISION_LOG = os.path.join(STATE_ROOT, "hook-decisions.jsonl")

def _mode():
    """`enforce` | `observe` | `off`.

    As a plugin, being ARMED is the mode: Claude Code only runs this hook at all
    when the plugin is installed and enabled, so the remaining question is
    whether the host wants it firing. `armed` answers it, and a host that has
    selected a different continuation strategy sets it false rather than relying
    on the plugin being disabled — two independent belts, so a plugin re-enabled
    by hand still cannot type into a session the host is driving itself.

    The legacy variable is read only when no option is set, which is what keeps a
    hand-installed copy behaving exactly as it did before this file was vendored."""
    armed = os.environ.get("CLAUDE_PLUGIN_OPTION_ARMED", "").strip().lower()
    if armed:
        return "enforce" if armed not in ("false", "0", "no", "off") else "off"
    v = os.environ.get(MODE_ENV, "").strip().lower()
    return v if v in ("enforce", "off") else "observe"     # observe is the shipped default


def _log(session, verdict, detail=""):
    if not hook_decisions:
        return
    try:
        hook_decisions.log(CHECK_ID, verdict, detail,
                           session=session, event="Stop", mode=_mode())
    except Exception:
        pass


def _state_path(session):
    safe = "".join(c for c in str(session) if c.isalnum() or c in "-_")[:64] or "unknown"
    return os.path.join(STATE_DIR, safe + ".json")


def _load(session):
    try:
        with open(_state_path(session)) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(session, state):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = _state_path(session) + ".%d" % os.getpid()
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, _state_path(session))
    except Exception:
        pass


def _fill(session):
    """(percent, age_seconds) from the file the status line already writes, or
    (None, None). Written on every render via rename, so a reader never sees
    half a line."""
    path = "/tmp/.ccctx-%s" % session
    try:
        age = time.time() - os.path.getmtime(path)
        with open(path) as fh:
            first = fh.readline().strip()
    except Exception:
        return None, None
    parts = first.split("\t")
    try:
        return float(parts[0]), age
    except (ValueError, IndexError):
        return None, None


def _ts_epoch(value):
    """Parse a decision row's timestamp to epoch seconds, or None.

    Two writers, two formats: `hook_decisions.py` writes an aware isoformat with
    microseconds and a `+00:00` offset, `log-decision.sh` writes `...Z`. Comparing
    either as a STRING against a locally-formatted stamp is right by accident and
    wrong the moment a format or a timezone moves, so both are parsed."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _peer_verdict(session, since):
    """'block' | 'clear' | 'unread'.

    Stop handlers run in parallel and cannot see one another's verdicts, so the
    peer's decision is read after the fact from the log it writes. Failing to
    'unread' is the cheap direction: not typing costs one missed compaction,
    while typing into a re-opened turn lands mid-work."""
    # No peer will write a row if the peer is off, so waiting for one is waiting
    # forever. Without this branch the hook defers on every Stop of a session
    # whose judge is disabled — which is a supported configuration.
    # `hook_mode.resolve` reports the SHIPPED default and does not read the env
    # override, so the switch is read where it is actually set.
    def _off(check):
        var = "HOOK_MODE_" + check.upper().replace("-", "_")
        return os.environ.get(var, "").strip().lower() == "off"

    # A peer that is not INSTALLED is the same wait as a peer that is off, and
    # it is the ordinary case everywhere this plugin is not running beside the
    # hook engine it grew up in. Without this branch such a machine polls for
    # PEER_WAIT_S on every Stop, returns `unread`, defers — and the plugin is
    # installed, enabled, armed and permanently inert, with the 25s spent each
    # turn as the only symptom. Existence is read rather than inferred from the
    # log: a quiet peer and an absent one write the same nothing.
    def _installed(check):
        return os.path.exists(os.path.expanduser("~/.claude/hooks/%s.py" % check))

    if all(_off(c) or not _installed(c) for c in PEER_CHECKS):
        return "clear"

    # From the HOOK's start, not from this call. Both handlers were launched
    # together, so the judge's clock started then — and the compose that runs
    # before this already spent part of the window.
    # ALWAYS ONE READ, whatever the clock says. The window is a budget for
    # WAITING, not permission to skip looking: the compose can consume all of it,
    # and a verdict already sitting in the log is the case this is for.
    deadline = since + PEER_WAIT_S
    while True:
        try:
            with open(DECISION_LOG) as fh:
                rows = fh.readlines()[-400:]
        except Exception:
            rows = []
        for line in reversed(rows):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("session") != session or d.get("check") not in PEER_CHECKS:
                continue
            ts = _ts_epoch(d.get("ts") or d.get("timestamp"))
            if ts is not None and ts < since:
                continue
            return "block" if d.get("verdict") == "block" else "clear"
        if time.time() >= deadline:
            return "unread"
        time.sleep(1)


def _transcript(session, data):
    """The session's transcript. The id is filtered like the state path's: it
    reaches a glob, where `..` or a metacharacter would resolve to another
    session's file and settle this fire against somebody else's compaction."""
    if not _boundary:
        return None
    safe = "".join(c for c in str(session) if c.isalnum() or c in "-_")[:64]
    return _boundary.transcript_of(safe or "unknown",
                                   data.get("transcript_path"))


def _settle_pending(session, data):
    """Settle the fire an earlier Stop typed, write the settlement's row, and return
    (outcome, detail) from `hook_compact_settle.settle`.

    A settlement is an event about the fire, so it is recorded wherever the Stop
    lands — including below the floor, which is where a compaction that worked
    leaves the session. Whether this Stop then stands down is `_stand_down`'s
    question, asked further down."""
    if not hook_compact_settle:
        return None, None
    state = _load(session)
    fire = state.get("pending") if isinstance(state.get("pending"), dict) else {}
    handoff_at = fire.get("handoff_at") or fire.get("ts")
    outcome, detail = hook_compact_settle.settle(
        state, _transcript(session, data), time.time(), COOLDOWN_S)
    if outcome in ("landed", "lost", "unconfirmed"):
        _save(session, state)
        if outcome != "landed" and hook_compact_handoff:
            # Only this fire's own: another route may have written one since, and
            # clearing that would cost the far side its composed resume.
            hook_compact_handoff.clear(session, before=handoff_at)
        _log(session, outcome, detail)
    return outcome, detail


def _stand_down(session, settled):
    """True when this Stop must type nothing: a `/compact` is still in flight, or
    lost fires have reached the bound. A lost fire spends nothing, so without that
    bound a pane that keeps swallowing the keys — measured: by submitting whatever
    draft sat in the composer — would be typed into on every Stop."""
    outcome, detail = settled
    if outcome == "pending":
        _log(session, "silent", detail)
        return True
    lost = _load(session).get("lost", 0)
    if lost >= MAX_LOST:
        _log(session, "silent", "why=typed compactions keep not landing n=%d" % lost)
        return True
    return False


def main():
    if any(os.environ.get(k) for k in REENTRY):
        return
    if _mode() == "off":
        return
    started = time.time() - 1.0   # one second of slack for clock granularity

    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    session = data.get("session_id") or "unknown"

    if data.get("agent_id") or data.get("permission_mode") == "plan":
        return

    running = [t for t in (data.get("background_tasks") or [])
               if isinstance(t, dict) and t.get("status") == "running"]
    # The COUNT was the whole predicate until 2026-09-08, and it refused 982 of
    # this check's first 1065 stops — a gate that is closed 92% of the time is not
    # protecting an invariant, it is preventing the feature.
    #
    # What it was protecting: compaction is a context REPLACEMENT, not lossless
    # summarisation (SDK corpus `query-lifecycle.md:397`), so a pending task that
    # completes afterwards re-invokes a session that may no longer know WHY it was
    # started. That reason is the thing worth protecting — not the compaction.
    #
    # Why the count cannot express it, measured 2026-09-08: this payload's entries
    # carry `command|description|id|status|type`, and `type` reads `shell` for BOTH
    # a persistent Monitor and a finite `run_in_background` Bash. No field separates
    # "will complete" from "never completes", so a predicate resting on that
    # distinction cannot be written. (`task_type`, which the SDK's own
    # `background_tasks_changed` carries, is absent here.)
    #
    # So the gate now holds for the one kind whose result is substantive and whose
    # dispatch intent is costliest to lose — an agent or a workflow — and every
    # other kind becomes a FACT handed to the composer instead of a veto. Anthropic's
    # own auto-compaction gates on nothing at all; naming the pending work in the
    # focus is what makes this strictly better than that rather than merely looser.
    # WHICH KINDS BLOCK IS A QUESTION ABOUT THE REGISTRY'S BOOKKEEPING, not about
    # how important the work is. A kind blocks only if its count is trustworthy,
    # because a count that never falls is a permanent refusal wearing the shape of
    # a safety check.
    #
    # Measured 2026-09-08 over this check's own rows, tracking each session's count
    # across successive Stops:
    #
    #   teammate   8 of 9 sessions NEVER saw the count fall. 10 held for 3h49m,
    #              19 for 2h57m, 8 for 1h57m; one went UP. A finished teammate stays
    #              in `background_tasks`, so gating on it blocks any session that
    #              ever spawned one, for the rest of its life. That is what pinned
    #              0b5a7cad at 90% and ec78b61e at 74% all afternoon.
    #   subagent   drains correctly — 4>3>2>1, 2>1, 4>3>2 across three sessions.
    #              The count means what it says.
    #
    # So subagents hold the gate and teammates do not. A subagent's result returns
    # into this session as a tool result and is the work most costly to strand;
    # the corpus records that subagents run backgrounded by default since v2.1.198,
    # so one genuinely can be in flight when a turn ends.
    #
    # `workflow` keeps blocking on thin evidence — 4 readings, one session, flat —
    # because a workflow IS a fleet of subagents and it is rare enough (4 of 122
    # rows) that a wrong call here cannot reproduce the teammate problem.
    #
    # The rule itself is `hook_compact_focus.dispatched`, shared with the facts
    # the composer is handed: what holds this gate is exactly what the focus is
    # obliged to explain, and everything else reaches the composer as a listener.
    blocking = [t for t in running if dispatched(t)]
    # Read the fill BEFORE deciding whether the pending work vetoes, because the
    # answer depends on it. The row carries the reading either way, so a session
    # that defers repeatedly shows its fill CLIMBING rather than repeating one
    # indistinguishable line — which is what made the stranding invisible.
    pct, age = _fill(session)
    # Before the floor test: a compaction that lands drops the fill below the
    # floor, and that Stop is exactly the one that has to settle it. A settlement
    # row carries no dispatch facts, and `compact-sweep` reads only rows that do.
    settled = _settle_pending(session, data)
    state = _load(session)
    now = time.time()
    held_since = state.get("held_since")

    # Below the floor there is nothing to hold, so no row and no clock. A deferral
    # row written here is also what `compact-sweep` reads as the session's last
    # state, long after the work it named has ended.
    if pct is not None and pct < PCT_FLOOR:
        if held_since is not None:
            state.pop("held_since")
            _save(session, state)
        return

    if not blocking and held_since is not None:
        state.pop("held_since")
        _save(session, state)
    if blocking:
        # THE CLOCK STARTS ONLY ON A READABLE FILL, because the hold measures time
        # spent AT OR ABOVE THE FLOOR and an unknown fill shows neither. The floor
        # test above returns on a reading below it and cannot act on `None`, so a
        # session whose status line has not rendered would otherwise accumulate the
        # whole hold on Stops where the floor was never demonstrated — then be
        # released by the first reading it ever got, twenty points under the
        # ceiling, over live dispatch. `unknown` is not permission to fire, and by
        # the same reading it is not evidence the floor was crossed.
        if held_since is None and pct is not None:
            held_since = state["held_since"] = now
            _save(session, state)
        held = now - held_since if held_since is not None else 0.0
        kinds = {}
        for t in blocking:
            k = str(t.get("type") or t.get("task_type") or "?")[:24]
            kinds[k] = kinds.get(k, 0) + 1
        # The ids ride in the row so one task held for an hour reads apart from
        # twelve that each ran five minutes, and `compact-sweep` matches them
        # against the transcript's task-notifications to tell when they ended.
        # They go LAST and uncapped: the log's own detail cap then cuts only ids,
        # and a row naming fewer ids than it counted reads as unprovable there.
        facts = "n=%d of %d kinds=%s held=%ds hold=%d pct=%s ceiling=%d ids=%s" % (
            len(blocking), len(running),
            ",".join("%s:%d" % kv for kv in sorted(kinds.items()))[:80],
            held, HOLD_S, "?" if pct is None else "%.0f" % pct, PCT_CEILING,
            ",".join(sorted(str(t.get("id") or "?")[:10] for t in blocking)))
        # An unreadable fill defers whatever the hold says. It cannot show the
        # ceiling was reached, and `unknown` is never read as permission — the
        # same rule the panel keeps one layer up.
        if pct is None or (pct < PCT_CEILING and held < HOLD_S):
            _log(session, "silent", "why=dispatched work in flight " + facts)
            return
        # A release is its own row, naming what released it, so a fire over live
        # dispatch is countable in observe mode and never reads as a fire with
        # nothing pending.
        _log(session, "released", "why=%s %s"
             % ("ceiling" if pct >= PCT_CEILING else "hold spent", facts))
    close = data.get("last_assistant_message")
    if not isinstance(close, str) or not close.strip():
        _log(session, "silent", "why=no last_assistant_message")
        return

    # Below the dispatch gate: a Stop standing down for a pending fire still owes
    # the gate its row, which is what `compact-sweep` reads for live dispatched
    # work, and the hold's clock still has to start.
    if _stand_down(session, settled):
        return

    if pct is None:
        _log(session, "silent", "why=no fill reading")
        return

    # A STALE READING IS NOT A REASON TO REFUSE, and the ordering above is why:
    # the floor test has already run, so anything reaching here read at or above
    # PCT_FLOOR. Fill only grows between compactions, so an old reading of 96% is a
    # LOWER BOUND — the session is at least that full now. Refusing on it threw away
    # evidence that errs in the safe direction.
    #
    # It threw it away on exactly the population this hook exists for. The only
    # writer is `~/.claude/statusline.sh:41`, which runs when the status line
    # RENDERS; an unfocused or parked pane does not repaint, so a session sitting
    # full and idle is precisely the one whose reading ages out. Measured 2026-09-08
    # by probing a live session at 96%: it cleared the background gate and the peer
    # gate, then died on `stale fill age=2772s` at the very moment its turn ended.
    #
    # The one case the lower-bound argument misses is a compaction between the
    # reading and now, leaving the true fill lower. That costs one unnecessary
    # compaction, and three things bound it: the 900s cooldown, the 3-fire budget,
    # and the inert rule below, which stands the check down for good once a landed
    # compaction frees too little. The age rides in the row so the cost is countable.

    name, send = typist()
    if not send:
        # `typist()` returns (None, None) for two different situations and only one
        # of them is a fault. A session running outside cmux and tmux — a plain
        # terminal, an IDE, a headless run — has no pane to type into and is simply
        # OUT OF SCOPE; recording that as an error means a normal configuration
        # raises an alarm on every turn it takes, forever. A session that DOES name
        # a pane whose multiplexer cannot be reached is broken and stays an error.
        declared = os.environ.get("TMUX_PANE") or os.environ.get("CMUX_SURFACE_ID")
        if declared:
            _log(session, "error",
                 "why=a pane is declared but its multiplexer could not be reached")
        else:
            _log(session, "silent",
                 "why=session runs outside cmux and tmux, so nothing can type for it")
        return

    # Judged from the boundary the compaction itself wrote, never from the fill at
    # a later Stop: that rule retired three sessions whose compactions had landed
    # and freed 94-97% of the window, then refilled. No boundary after the fire
    # means the /compact never landed, which is no verdict. A standing verdict
    # SAYS so at every Stop — a silent return here left 2a4fe685's last row
    # reading "dispatched" through seven later Stops, and the sweep refused on it.
    basis, fresh, changed = (_boundary.inert(
        state, _boundary.transcript_of(session, data.get("transcript_path")),
        PROGRESS_POINTS) if _boundary else (None, False, False))
    if changed:
        _save(session, state)
    if basis:
        _log(session, "inert" if fresh else "silent", "why=%s freed=%s pre=%s post=%s"
             % ("compaction freed too little" if fresh else "inert",
                basis.get("freed"), basis.get("pre"), basis.get("post")))
        return
    if state.get("fires", 0) >= MAX_FIRES:
        _log(session, "silent", "why=budget spent n=%d" % state["fires"])
        return
    last = state.get("last_ts", 0)
    if last and time.time() - last < COOLDOWN_S:
        _log(session, "silent", "why=cooldown %ds" % int(time.time() - last))
        return

    # 🔑 COMPOSE FIRST, THEN READ THE PEER. The judge and this hook are separate
    # Stop handlers the host runs in PARALLEL, so seconds spent composing are
    # seconds the judge is already using. Reading the peer first made the two
    # costs add instead: the wait expired while the judge was still thinking, and
    # the hook deferred on 676 of 975 judged stops without ever composing.
    #
    # What this order costs is a composer call on a turn the judge then re-opens
    # — 136 of those 975. One haiku call, against a session that otherwise never
    # compacts at all.
    focus, resume, how = _compose(
        _facts(data.get("cwd") or os.getcwd(), running, session=data.get("session_id")),
        close)
    command = "/compact " + focus

    peer = _peer_verdict(session, started)
    if peer != "clear":
        _log(session, "deferred", "why=%s" % (
            "peer blocker re-opened the turn" if peer == "block" else "peer verdict unread"))
        return

    # None means UNREADABLE and is not False. Folding them together is how an
    # unreadable capture reads as "the box is empty". Both multiplexers get the
    # same treatment: `draft_in_composer()` resolves tmux or cmux by the same
    # precedence `typist()` did, so a `None` here means the box THIS `send` is
    # about to type into could not be read, whichever one it is.
    draft = draft_in_composer()
    if draft:
        _log(session, "deferred", "why=owner is typing")
        return
    if draft is None:
        _log(session, "deferred", "why=composer unreadable")
        return

    if _mode() == "observe":
        # The state write is NOT skipped here. Observe measures the fire RATE, and
        # a rung that records without spending the cooldown pays the poll and the
        # model call on every Stop above the floor for the session's life — which
        # measures nothing and costs everything.
        # State is re-read first: the compose and the peer wait ran for up to 95s
        # since it was loaded, and another Stop of this session may have written
        # it meanwhile.
        state = _load(session)
        state["fires"] = state.get("fires", 0) + 1
        state["last_ts"] = time.time()
        state.pop("fill_at_last_fire", None)
        _save(session, state)
        # Neither the inert rule's baseline nor the handoff is written. Observe
        # types nothing, so no compaction follows for the one to judge — and a
        # handoff left behind would be picked up by whatever compaction does
        # happen next, which is a manual one about other work.
        _log(session, "would-stamp",
             "pct=%.0f age=%ds how=%s typist=%s n=%d resume=%s focus=%s"
             % (pct, age, how, name, state["fires"],
                "composed" if resume else "generic", focus[:160]))
        return

    # Taken BEFORE the send: it marks where this fire's evidence starts in the
    # transcript, so nothing typed or compacted earlier can settle it.
    pending = (hook_compact_settle.begin(pct, focus, _transcript(session, data), time.time())
               if hook_compact_settle else None)

    # Written BEFORE the send: the keys land, the host compacts, and PostCompact
    # can fire before this process would otherwise get back. A handoff that loses
    # that race is a handoff that never existed.
    handed = bool(hook_compact_handoff
                  and hook_compact_handoff.write(session, resume))
    if pending and handed:
        # The moment THIS fire's resume line was written, so settlement can leave a
        # handoff composed later — by another route typing into the same session —
        # where it is.
        pending["handoff_at"] = time.time()
    try:
        send(command)
    except Exception as exc:
        if hook_compact_handoff:
            hook_compact_handoff.clear(session)   # no seam happened; leave none behind
        _log(session, "error", "why=send failed %s" % type(exc).__name__)
        return

    # The typist's exit code proves only that the keys reached the terminal, so
    # the fire is PENDING: it spends nothing until a later Stop settles it
    # against the transcript, and until then that Stop stands down rather than
    # type a second `/compact` over it. State is re-read first, because the
    # compose and the peer wait ran for up to 95s since it was loaded.
    state = _load(session)
    if pending:
        state["pending"] = pending
    else:
        state["fires"] = state.get("fires", 0) + 1
        state["last_ts"] = time.time()
        state.pop("fill_at_last_fire", None)
    _save(session, state)
    # `over=` names a fire the ceiling allowed past the background gate, so an
    # override is countable against ordinary fires rather than reading as one.
    # Without it, the only evidence a session was rescued from stranding is its
    # ABSENCE from a list of dead sessions. `n=` is the fire this send becomes
    # if it lands.
    _log(session, "stamped", "pct=%.0f age=%ds how=%s typist=%s n=%d resume=%s%s%s"
         % (pct, age, how, name, state.get("fires", 0) + (1 if pending else 0),
            "composed" if handed else "generic",
            " lost=%d" % state["lost"] if state.get("lost") else "",
            " over=%d" % len(blocking) if blocking else ""))


if __name__ == "__main__":
    if hook_run:
        hook_run.run(CHECK_ID, main, event="Stop")
    else:
        try:
            main()
        except Exception:
            pass
        sys.exit(0)
