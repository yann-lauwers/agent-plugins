# agent-plugins

Yann Lauwers' Claude Code plugins, served as one marketplace named `yannlauwers`.

| Plugin | What it does |
| --- | --- |
| `continuous-compaction` | At the end of a turn above the fill floor (default 80%), composes a one-line focus, compacts the session, and types a resume line once the compaction lands. Needs the session to run in tmux or cmux. |

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

Settings live in `/config` (search "continuous-compaction") or `/plugin configure continuous-compaction@yannlauwers`.
