"""Resolve the multiplexer that owns this session, and type into it.

One home for a fact two hooks need. `postcompact-resume.py` proved the call
shapes end to end on 2026-08-18 — text and Enter as two calls, into a throwaway
surface, verified by reading what arrived — and `compact-at-seam.py` needs the
identical resolution. A second copy drifts the day cmux renames a variable.

The typist is resolved from THE CALLING HOOK'S OWN ENVIRONMENT, inherited from
the session process. No registry, no lookup, no most-recent-session guess: a hook
runs inside the session it is typing into, and that is what makes the pairing
correct rather than probable.

tmux is tried first and is the one that travels: a Linux server has it, and a
session there has to run inside it anyway to survive an SSH drop. cmux is the
macOS laptop's terminal, and without its branch a caller is inert exactly where
the day's work happens.
"""
import os
import shutil
import subprocess


def cmux_bin():
    """cmux names its own binary for hooks, and it is not always on PATH — the
    app bundle's bin directory is added by the shell integration, which a hook
    subprocess does not necessarily inherit."""
    for var in ("CMUX_CLAUDE_HOOK_CMUX_BIN", "CMUX_BUNDLED_CLI_PATH"):
        path = os.environ.get(var)
        if path and os.path.exists(path):
            return path
    return shutil.which("cmux")


def typist():
    """(name, send_callable) for whichever multiplexer owns this session, or
    (None, None)."""
    pane = os.environ.get("TMUX_PANE")
    if pane and shutil.which("tmux"):
        def send_tmux(text):
            # Text then Enter, as two calls. A TUI commonly drops a newline
            # arriving in the same write as the text it terminates, which reads
            # as the message being typed and never submitted.
            subprocess.run(["tmux", "send-keys", "-t", pane, "-l", text],
                           check=True, timeout=10)
            subprocess.run(["tmux", "send-keys", "-t", pane, "Enter"],
                           check=True, timeout=10)
        return "tmux:" + pane, send_tmux

    surface = os.environ.get("CMUX_SURFACE_ID")
    binary = cmux_bin()
    if surface and binary:
        def send_cmux(text):
            subprocess.run([binary, "send", "--surface", surface, text],
                           check=True, timeout=10)
            subprocess.run([binary, "send-key", "--surface", surface, "enter"],
                           check=True, timeout=10)
        return "cmux:" + surface, send_cmux

    return None, None


# cmux and tmux both render an EMPTY composer's prompt as the marker glyph
# followed by U+00A0 (NO-BREAK SPACE), not an ordinary U+0020 — confirmed
# byte-for-byte against two live sessions' captures. Every other
# NBSP mention in this plugin, its tests included, points back to this
# comment rather than restating the finding.
_NBSP = " "


def _tmux_draft(pane):
    try:
        r = subprocess.run(["tmux", "capture-pane", "-p", "-t", pane],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    # The composer is box-drawn, so the captured line starts with a border glyph
    # and the prompt marker sits inside it. Scanning for a bare "> " matched
    # nothing and folded into None — which a caller reading `not draft` treats as
    # "clear to type", and typing then APPENDS to the half-written line and sends
    # Enter, submitting it. Match the border forms too.
    #
    # NBSP is normalized AFTER the border-strip, not before. The strip's own
    # charset is ASCII space only, so a raw NBSP survives it as the marker's
    # own significant trailing character while ordinary padding does not --
    # normalizing first would let the strip consume it like any other space
    # and lose the one thing that told it apart from padding.
    for line in reversed([l for l in r.stdout.splitlines() if l.strip()][-3:]):
        body = line.strip().lstrip("│|┃ ").rstrip("│|┃ ").replace(_NBSP, " ")
        for marker in ("> ", "❯ "):
            if body.startswith(marker):
                return len(body[len(marker):].strip()) > 0
    return None


def _cmux_draft(binary, surface):
    try:
        r = subprocess.run([binary, "read-screen", "--surface", surface, "--lines", "30"],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    # Unlike tmux, cmux draws no border around the composer and the prompt
    # marker sits on its own line — but cmux renders a multi-line status footer
    # (title, model, git branch) BELOW the composer, so the composer is never
    # the last captured line the way it is in a tmux pane. Scan the whole
    # capture for the marker rather than assuming a fixed offset from the end.
    #
    # lstrip ONLY. An empty composer's own line is exactly the marker plus its
    # trailing space ("❯ ", nothing typed after it) — a plain `.strip()` eats
    # that trailing space along with any padding, so the same 2-char marker
    # check that correctly flags a draft would fold an EMPTY composer into "no
    # marker found" and read it as unreadable instead of empty. cmux has no
    # border-strip charset to collide with NBSP, so normalizing it here
    # (unlike tmux) is safe at any point before the marker check.
    for line in reversed([l.replace(_NBSP, " ") for l in r.stdout.splitlines() if l.strip()]):
        body = line.lstrip()
        for marker in ("> ", "❯ "):
            if body.startswith(marker):
                return len(body[len(marker):].strip()) > 0
    return None


def draft_in_composer():
    """True when the owner has typed something not yet submitted, False when
    the composer reads empty, None when it cannot be read at all — a caller
    must treat None as UNREADABLE, never as empty: typing over a half-written
    line destroys it, and the box does not clear itself.

    Resolved in the same precedence typist() uses, tmux then cmux, so a caller
    already holding the typist typist() picked is reading the box that typist
    will type into."""
    pane = os.environ.get("TMUX_PANE")
    if pane and shutil.which("tmux"):
        return _tmux_draft(pane)

    surface = os.environ.get("CMUX_SURFACE_ID")
    binary = cmux_bin()
    if surface and binary:
        return _cmux_draft(binary, surface)

    return None
