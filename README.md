# agent-plugins

Yann Lauwers' Claude Code plugins, served as one marketplace named `yannlauwers`.

| Plugin | What it does |
| --- | --- |
| `continuous-compaction` | When a turn ends above the fill floor (default 80%), it writes what to keep, summarizes the conversation through Claude Code itself, and sends the next prompt to carry on. It waits while helper agents still run, up to 85% or 30 minutes. Works in any terminal. |

## `/compaction`

`/compaction` opens a panel with how full the context is, when the next summary happens, and one row for this conversation and one for all conversations. Click the rows, then use ↑↓ to pick a row, ←→ to change its % by 5, `d` to turn it on or off, `r` to make this conversation follow all conversations again, and `n` to summarize now.

Typed forms: `/compaction 60` · `/compaction all 70` · `/compaction on|off` · `/compaction all on|off` · `/compaction reset` · `/compaction fire`.

The all-conversations values also sit in `/config`, as "Summarize conversations automatically" and "Summarize at % full".

## Install

From GitHub:

```sh
claude plugin marketplace add yann-lauwers/agent-plugins
claude plugin install continuous-compaction@yannlauwers --scope user
```

From a local clone:

```sh
claude plugin marketplace add /path/to/agent-plugins
claude plugin install continuous-compaction@yannlauwers --scope user
```

After pulling a new version, run `claude plugin update continuous-compaction@yannlauwers`, then `/reload-plugins` in open sessions.
