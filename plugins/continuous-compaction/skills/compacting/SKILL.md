---
description: >-
  Compacts a Claude session on demand — this one, or another one by session id — composing the focus from that session's own facts and re-priming it afterwards.
  Use when the request says "compact this", "compact that session", "run the compact", "compact it for me", "this session is too full", "compact the ones that are full", "sweep the full sessions", or names a session to compact.
  Also fires on the ACT — a session found near its context limit whose work has to continue rather than be restarted. The automatic half is the `compact-at-seam` hook, which needs no invocation.
allowed-tools: Bash(compact-sweep *), Bash(hook-decisions *), Bash(ls /tmp/.ccctx-*), Bash(ls /tmp/.ccseat-*), Read, Grep
---

# Compacting a session

`/compact` typed by hand costs the one thing compaction is meant to protect: the
person has to write the focus, from a conversation they have not read, at the
moment they least want to. `compact-sweep` composes it from the session's own
facts, types it, and lets `postcompact-resume` re-prime the other side.

**Two halves, one seam.** The percentage belongs to the automatic half only.

| | Automatic | On demand |
| --- | --- | --- |
| What | `compact-at-seam`, a `Stop` hook | `compact-sweep`, this skill |
| Fires | a turn ends at ≥80% fill (the `pct_floor` option) with no subagent or workflow in flight | you ask |
| Reaches | the session it runs inside | any session, by id |
| Threshold | yes, `pct_floor` (default 80%) | **none — naming a session is the reason** |

Both compose through this plugin's own `hooks/lib/hook_compact_focus.py` and hand
the far side a resume line through `hooks/lib/hook_compact_handoff.py`, so a session
gets the same brief whichever route reached it. `${CLAUDE_PLUGIN_ROOT}` is the root
those paths hang off, and it is exported into every hook this plugin runs.

## Run

```sh
compact-sweep --session <session-id> --go      # one named session, any fill
compact-sweep --session <id> --session <id2>   # several; dry run without --go
compact-sweep --min-pct 70 --go                # sweep everything that full
compact-sweep --min-pct 70                     # see what it would say first
```

<instructions>
1. **Dry run first when the target was not named by the user.** A sweep types into
   panes nobody asked about; a named session was asked for. Without `--go` it
   prints the composed focus and resume and sends nothing.
2. **Report the composed focus back.** It is the only part a person can judge, and
   a wrong one is cheap to catch before it is typed and expensive after.
3. **Read the outcome from the decision log, not from the exit code.** The command
   returns 0 when it typed; whether the seam CROSSED is two rows:
   `hook-decisions --check compact-at-seam` and `--check postcompact-resume`.
   A crossing is `resume=composed` on the near side answered by `msg=composed` on
   the far side, same session, seconds apart.
</instructions>

## Finding the session id

`/tmp/.ccctx-<session-id>` exists per live session and holds its fill; the
transcript at `~/.claude/projects/<slug>/<session-id>.jsonl` names the directory.
`compact-sweep --min-pct 0` lists every session it can resolve, with its fill, its
directory and what it would compose — the cheapest way to find the one you mean.

## What it refuses, and why each refusal is right

Typing into the wrong pane puts a `/compact` into somebody's live work, so
resolution has to be exact rather than probable.

| Refusal | Cause |
| --- | --- |
| `no recorded seat` | that session has not rendered its status line since `.ccseat-` existed, so its surface id is unknown. Self-heals — it records one on its next render |
| `no closing message to compose from` | nothing to write a focus against |
| `its last recorded Stop reported dispatched work` | unattended passes only: the last deferral row named tasks the transcript has not yet reported finished. A named session prints it as a `NOTE` and compacts anyway. `--ignore-dispatch` overrides |
| `cooldown` · `budget spent` · `inert` | an unattended pass already swept it; `inert` means that sweep's compaction freed too little, read from its own boundary in the transcript. A named session is exempt from all three |

**A surface is the only safe target, and there is one place it is recorded.** cmux
nests workspace → pane → surface, and a Claude session is a *surface*. One
workspace can hold several, so `cmux workspace list` cannot identify a session even
when it returns exactly one row — observed, one workspace and one pane holding
`surface:85` and `surface:168`. Worse, `cmux send --workspace` delivers to whichever
surface is active there, so resolving by workspace does not fail cleanly; it types
into a different session inside the right workspace.

So resolution reads `/tmp/.ccseat-<session>` and nothing else. `~/.claude/statusline.sh`
writes it because it is the only process holding both the session id and the surface
it inherited. A session with no seat is reported unreachable rather than guessed at.

## Reading a result

| Row | Meaning |
| --- | --- |
| `how=composed` | the model wrote the focus; `fallback` means it timed out and the generic one went |
| `resume=composed` | a resume line was handed to the far side |
| `msg=composed` (far side) | that line was typed — the seam crossed |
| `msg=generic` after `resume=composed` | the handoff was written and lost between the hooks — suspect the TTL |
| `why=dispatched work in flight n=X of Y … held=Ns hold=B … ids=` | held by a subagent or workflow; the other `Y-X` tasks — teammates, watchers, background shells — were correctly ignored. `held` is how long this session has been held, across every task, against the bound `B` (`task_stale_s`) |
| `released` · `why=hold spent` or `why=ceiling` | dispatched work stopped holding this Stop. The fire can still meet the inert, budget, cooldown, peer and composer checks after it; a `stamped` row that follows carries `over=` |
| `inert` · `why=compaction freed too little freed=F pre=P post=Q` | a compaction this hook fired landed and freed only `F` fill points, so the check stands down for this session |
| `silent` · `why=inert freed=F …` | that verdict still standing, written at every Stop that reaches it |

## Where the rest lives

Every path below hangs off `${CLAUDE_PLUGIN_ROOT}`, the directory this plugin was
installed into, which Claude Code exports into each of its hooks.

| Question | File |
| --- | --- |
| what the hook decides, and what arms it | `hooks/compact-at-seam.py`, module docstring |
| how the focus and resume are composed | `hooks/lib/hook_compact_focus.py` |
| how the resume crosses the seam | `hooks/lib/hook_compact_handoff.py` |
| the sweeper's own refusals and resolution order | `bin/compact-sweep`, module docstring |
| the floor, and whether it fires on its own | `.claude-plugin/plugin.json`, `userConfig` — `pct_floor` and `armed`. `/plugin configure` sets them, and a host application sets them the same way |
| anything else about this skill | this file; there are no cookbooks yet |

`hook-decisions` reads a log written by a hook engine this plugin does not ship. Where
this machine has one under `~/.claude/hooks/lib`, the hooks find it and their verdicts
are readable there; where it does not, they run and log nothing, which is the ordinary
state rather than a fault.
