"""Composing the two lines that carry a session across a compaction.

One home for what `compact-at-seam.py` does at a Stop and what `compact-sweep`
does from outside. The hook can only act when a turn ends in its own session; the
sweeper reaches a session that is parked and will never end another turn. They must
compose identically, or the same session gets a different brief depending on which
route reached it — so the facts, the prompt, the model and the ceiling live here.
"""
import json
import os
import re
import subprocess

# The host's record of what this session observably touched — its mission,
# ticket, stores and the checkout it last wrote into. Optional by construction:
# a machine without the host composes from the hook's own cwd exactly as before,
# and a host module that fails at import is that machine.
try:
    import work_context
except Exception:
    work_context = None

REENTRY = ("COMPACT_AT_SEAM_COMPOSING", "STOP_UNFINISHED_JUDGING")
COMPOSER_MODEL = os.environ.get("COMPACT_AT_SEAM_MODEL", "claude-haiku-4-5-20251001")
COMPOSE_TIMEOUT_S = 90      # Measured 2026-09-08 against a live Haiku, on the two-field
                            # prompt: 24.5s and 36.8s on a clean base checkout, 49.7s and
                            # 57.0s on a lane carrying a dirty tree and an open PR — the
                            # population this exists for, and the slowest because it is
                            # the richest. At 40s that case timed out on every run; at
                            # 60s it cleared by three seconds, which is not a margin.
                            # A timeout is caught and falls back, so the cost of a low
                            # ceiling is a generic focus on exactly the sessions that
                            # needed a composed one — and the row still reads "fallback",
                            # which looks like a model failure rather than a budget.

FALLBACK_FOCUS = (
    "Preserve: the ticket and its spec, every ticket still open, absolute paths of files "
    "changed this session, decisions taken and why, and any correction the owner made. "
    "Continue at the first open ticket.")

# Empty, deliberately. A composer that failed has no facts to re-prime from, and
# the far side already holds a correct generic message — so the fallback is to let
# that stand rather than to invent a specific instruction from nothing.
FALLBACK_RESUME = ""

SYSTEM_PROMPT = (
    "You write the two lines that carry a conversation across a compaction. "
    "Answer with JSON only: {\"focus\": \"<one line>\", \"resume\": \"<one line>\"}. "
    "\"focus\" steers the summary: what to preserve. Where the input carries "
    "pending_background_work, which holds dispatched work only (a subagent, a "
    "workflow or an agent), the focus MUST name what each pending task was "
    "started for and what its result feeds — that reason is re-read when the task "
    "completes and is the one thing the replacement cannot reconstruct. "
    "background_listeners are facts, not pending steps: watchers, monitors, shells "
    "and any host-armed listener such as an artifact watcher. The focus may name "
    "one, but never leads with it, and none is an obligation of the focus. "
    "Where the input carries work_context, it holds the ids of the mission, ticket, "
    "stores and checkout the session observably touched; preserve them verbatim. "
    "\"resume\" is typed AFTER the compaction, into the session's own prompt, so "
    "it must read as an INSTRUCTION and start with an imperative verb — \"Re-read "
    "X, then continue.\" A noun phrase or a list of filenames is not an "
    "instruction and is wrong. It tells the session what to RE-READ and "
    "RE-VERIFY before acting: the files the work last touched and the branch's "
    "own diff. It never points at a message above, the closing message or an "
    "earlier message — the compaction deletes them, so the session cannot find "
    "what it names. It never names the next action — "
    "the summary already carries that, and a wrong specific instruction costs "
    "more than a generic right one. It never restates what the focus preserved. "
    "Every fact you need is in the input; you have no tools and must invent nothing.")

# Which background entries are DISPATCHED work: the kind whose result returns
# into the session and whose reason for starting is costliest to lose. One rule
# for two readers — the Stop gate holds on it, and the composer's facts split on
# it — because a task the gate protects and the focus then files as a listener
# would lose exactly the reason the gate held for. Teammates never count: a
# finished one stays in the payload, so counting it holds a session forever. An
# entry with no kind at all counts: the sweeper rebuilds pending work from the
# gate's own rows, which record dispatched kinds only and carry no type.
DISPATCHED_KINDS = ("subagent", "workflow", "agent")


def dispatched(task):
    kind = str(task.get("type") or task.get("task_type") or "").lower()
    if not kind:
        return True
    if "teammate" in kind:
        return False
    return kind in DISPATCHED_KINDS or kind.endswith("_agent")


def work_context_of(session):
    """The host's fold for `session`, or {}. The host promises `fold` never
    raises; a hook that types into a live session does not stake itself on that."""
    if work_context is None or not session:
        return {}
    try:
        fold = work_context.fold(session)
    except Exception:
        return {}
    return fold if isinstance(fold, dict) else {}


def one_line(text, cap):
    """Every value that reaches a line: whitespace collapsed, newlines included, then capped."""
    return " ".join(str(text).split())[:cap]


def _describe(task):
    return one_line(task.get("description") or task.get("command") or "?", 160)


def facts(cwd, running=None, session=None):
    """Everything the composer is handed. A toolless composer asked about
    repository state confabulates, so nothing here is left for it to ask.

    `running` is the session's pending background work. It is a FACT rather than
    a veto: compaction is a context REPLACEMENT (`query-lifecycle.md:397` in the
    SDK corpus), so what a pending task costs is not the compaction itself but the
    loss of WHY it was started — the session is re-invoked when it completes and
    must still know what its result feeds. That reason is exactly what a focus
    line can carry, so the pending work is named to the composer instead of being
    used to stand the hook down. Only dispatched work carries that obligation;
    everything else running is a listener, named apart so it cannot lead the focus.

    `session` reaches the host's work context. Its `checkout` is where the work
    lives, which the hook's cwd is not whenever a session writes into a worktree
    it did not start in, so git is read there when it names a real directory.
    Transcript paths are dropped from what the composer sees: they locate files,
    they identify nothing, and the composer has no tools to open them."""
    out = {}
    tasks = [t for t in (running or []) if isinstance(t, dict)]
    work = [_describe(t) for t in tasks if dispatched(t)][:8]
    listeners = [_describe(t) for t in tasks if not dispatched(t)][:8]
    if work:
        out["pending_background_work"] = work
    if listeners:
        out["background_listeners"] = listeners

    context = work_context_of(session)
    if context:
        out["work_context"] = {k: v for k, v in context.items() if k != "transcript_paths"}
        checkout = context.get("checkout")
        if isinstance(checkout, str) and os.path.isdir(checkout):
            cwd = checkout

    def git(*a):
        try:
            r = subprocess.run(("git", "-C", cwd) + a, capture_output=True,
                               text=True, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None

    branch = git("rev-parse", "--abbrev-ref", "HEAD") or ""
    out["branch"] = branch
    m = re.search(r"(?<![a-z0-9])([a-z]{2,6}-\d{1,6})(?![a-z0-9])", branch, re.I)
    out["ticket"] = m.group(1).upper() if m else None
    out["dirty"] = (git("status", "--porcelain") or "")[:1500]
    root = git("rev-parse", "--show-toplevel")
    if root:
        for kind in ("validation", "arch", "implementation"):
            d = os.path.join(root, "docs", "reports", kind)
            try:
                names = os.listdir(d)
            except OSError:
                continue
            if out["ticket"]:
                hit = [n for n in names if out["ticket"].lower() in n.lower()]
                if hit:
                    out.setdefault("evidence", []).append("%s: %s" % (kind, hit[0]))
    try:
        r = subprocess.run(["gh", "pr", "view", "--json", "number,isDraft,state"],
                           cwd=cwd, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            out["pr"] = r.stdout.strip()[:200]
    except Exception:
        pass
    return out


# 🔴 THE FIRST CHARACTER OF A TYPED LINE DECIDES WHETHER IT IS A PROMPT.
#
# Claude Code's interactive mode reads a leading `!` as SHELL MODE and runs the
# rest as a command; a leading `/` is a slash command; `#` and `@` have their
# own meanings. The resume line is typed into an EMPTY prompt, so whatever it
# starts with is the line's first character.
#
# That line is model output, and the model's inputs are not all ours: the
# composer is handed the session's own last message and 1500 characters of
# `git status --porcelain`, which carry text the session read from a branch, a
# PR body or a fetched page. A payload that steers the composer into returning
# `!curl … | sh` reaches a live prompt with a second Enter behind it and no
# permission check anywhere in the path — verified end to end against a fake
# tmux on 2026-09-09.
#
# The focus never had this problem, and its shape is the fix: it is only ever
# typed as `"/compact " + focus`, so its first character is never the line's.
# Here the equivalent is to put a word in front rather than to drop the line —
# a resume that begins with punctuation is usually a real instruction.
PROMPT_MODE_PREFIXES = ("!", "/", "#", "@")


def defuse_prompt_prefix(text):
    """A resume line that cannot change the prompt's mode by its first character."""
    if text.startswith(PROMPT_MODE_PREFIXES):
        return "Continue: " + text
    return text


# A line typed after the seam that points at "the message above" points at
# something the compaction just deleted: the session goes looking, finds the
# nearest thing that fits, and works on that. Measured on one session across two
# compactions, both came out working on a superseded artifact. Word-bounded, so
# "aboveground" or "the messages panel" are not referents.
REFERENT = re.compile(r"\b(above|closing messages?|earlier messages?)\b", re.I)


def names_a_referent(text):
    return bool(REFERENT.search(text or ""))


WORK_CONTEXT_STORES = 3


def work_context_line(fold):
    """The line typed after a compaction when the host's fold names any id, or None.

    One sentence of ids and at most one directive. It is context, never a script:
    the specifics ride the host's own post-compaction note, and a typed plan
    competes with the summary the way a composed resume would. The directive is to
    re-read the mission, and only when one governs — a mission is what survives a
    compaction as a record, so it is the one thing worth sending the session to."""
    if not isinstance(fold, dict):
        return None

    def one(value):
        return one_line(value, 200) if isinstance(value, str) else ""

    mission, ticket, checkout = (one(fold.get(k)) for k in ("mission", "ticket", "checkout"))
    stores = fold.get("stores") if isinstance(fold.get("stores"), list) else []
    stores = [s for s in (one(v) for v in stores) if s][:WORK_CONTEXT_STORES]
    parts = []
    if mission:
        parts.append("mission " + mission)
    if ticket:
        parts.append("ticket " + ticket)
    if stores:
        parts.append("stores " + ", ".join(stores))
    if checkout:
        parts.append("checkout " + checkout)
    if not parts:
        return None
    line = "Compaction ended (machine-typed): work context is " + ", ".join(parts)
    if mission:
        line += "; re-read mission %s before your next act" % mission
    return line + "."


def compose(facts, close):
    """(focus, resume, how). `how` is 'composed' or 'fallback' — the row says which.

    Both lines come out of ONE call, from one reading of the facts. Composing the
    resume separately would either spend a second model call on the same input or,
    worse, run it after the seam where the facts no longer exist."""
    payload = json.dumps({"facts": facts, "closing_message": close[:4000]},
                         ensure_ascii=False)
    env = dict(os.environ)
    for k in REENTRY:
        env[k] = "1"
    # `how` carries WHY it fell back, not just that it did. A bare "fallback" reads
    # as a model failure whatever actually happened, and the three causes want
    # opposite fixes: a timeout says raise the ceiling, a non-zero exit says the
    # binary or the credential is wrong, and an unparseable answer says the prompt
    # is. Measured 2026-09-08, the first production fallback could not be told
    # apart from the other two by anything in its row.
    try:
        r = subprocess.run(
            ["claude", "--print", "--model", COMPOSER_MODEL,
             "--system-prompt", SYSTEM_PROMPT, "--disallowedTools", "*"],
            input=payload, capture_output=True, text=True,
            timeout=COMPOSE_TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired:
        return FALLBACK_FOCUS, FALLBACK_RESUME, "fallback:timeout-%ds" % COMPOSE_TIMEOUT_S
    except Exception as exc:
        return FALLBACK_FOCUS, FALLBACK_RESUME, "fallback:spawn-%s" % type(exc).__name__
    if r.returncode != 0:
        return FALLBACK_FOCUS, FALLBACK_RESUME, "fallback:exit-%d" % r.returncode
    try:
        raw = r.stdout
        answer = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        focus = answer.get("focus") or ""
        resume = answer.get("resume") or ""
    except Exception:
        return FALLBACK_FOCUS, FALLBACK_RESUME, "fallback:unparseable"
    focus = one_line(focus, 500)
    resume = defuse_prompt_prefix(one_line(resume, 500))
    # The focus is what makes the summary usable, so a missing one is the fallback
    # case whatever the resume said. A missing resume is not: the far side has its
    # own message, and a composed focus is still worth typing.
    if not focus:
        return FALLBACK_FOCUS, FALLBACK_RESUME, "fallback:no-focus"
    return focus, resume, "composed"
