# TARS — Terminal Agent Runtime Scanner

> *"Everybody good? Plenty of slaves for my robot colony?"* — TARS, Interstellar

A terminal-based command center for monitoring, interacting with, and orchestrating multiple Claude Code sessions in real-time.

Built with Python + [Textual](https://textual.textualize.io/), gruvbox-themed.

---

## What it does

TARS watches your running Claude Code sessions and gives you a single dashboard to manage them all — see what each agent is doing, send prompts to any session, transfer context between sessions, delegate tasks across multiple agents, and generate per-session journals.

## Features

### Monitor
- Live session list with status (active / idle / dead), tmux pane, duration, message & tool counts
- Sessions auto-grouped by tmux session name
- Real-time transcript tailing — watch Claude think, call tools, and respond as it happens
- 3-second auto-refresh with CPU-based activity detection

### Interact
- Send prompts to any live session directly from TARS via `tmux send-keys`
- Multiline prompt editor (ctrl+s to send)
- Jump to any session's tmux window with one keypress — works across tmux sessions
- Resume closed sessions from an in-app picker

### Orchestrate
- **Context transfer** — send one session's transcript as context to another session
- **Task delegation** — describe a task, TARS uses `claude -p` to break it into sub-tasks and spawns parallel agents
- Spawn new named sessions without leaving the dashboard

### Document
- Per-session journal generation — uses `claude -p` to summarize a session's work into a markdown journal entry

---

## Install

```bash
git clone <repo-url> ~/tars
cd ~/tars
uv venv && uv pip install -e .
```

## Run

```bash
cd ~/tars && .venv/bin/python -m tars
```

Or after activating the venv:

```bash
tars
```

## Requirements

- Python 3.11+
- [tmux](https://github.com/tmux/tmux) (for session interaction, spawn, resume)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude` command available in PATH)
- macOS or Linux

---

## Keybindings

### Home Screen

| Key | Action |
|-----|--------|
| `j` / `k` | Navigate sessions |
| `enter` | Open session detail |
| `n` | Spawn new Jarvis session |
| `a` | Resume a closed session (picker) |
| `d` | Delegate task to multiple agents |
| `x` | Kill session + close tmux window |
| `/` | Filter sessions |
| `r` | Refresh |
| `q` | Quit |

### Detail Screen

| Key | Action |
|-----|--------|
| `i` | Send prompt to session |
| `t` | Transfer context to another session |
| `w` | Generate journal for this session |
| `o` | Jump to session's tmux window |
| `j` / `k` | Scroll within focused section |
| `J` / `K` | Switch section focus (Prompts / Transcript) |
| `g` / `G` | Jump to top / bottom |
| `r` | Refresh session metadata |
| `q` / `esc` | Back to home |

---

## How it works

### Session Discovery
TARS reads Claude Code's local files:
- `~/.claude/sessions/*.json` — active session metadata (PID, session ID, cwd, name)
- `~/.claude/projects/{encoded-cwd}/{uuid}.jsonl` — full conversation transcripts

### Tmux Integration
Sessions are mapped to tmux panes by walking the process tree with `psutil`:
```
claude (PID) → zsh (parent) → tmux pane
```
This lets TARS know which tmux window each session lives in.

### Activity Detection
A session is marked "active" if any of these are true:
- Transcript file modified in the last 30 seconds
- Session JSON file modified recently
- Process or its children using >1% CPU

### Interaction via send-keys
When you send a prompt from TARS, it runs:
```bash
tmux send-keys -t ai:5 "your prompt here" Enter
```
This types directly into the running Claude session. The live transcript tail then picks up Claude's response in real-time.

### Task Delegation
1. You describe a task in the delegate modal
2. TARS runs `claude -p` (headless) to break it into 2-4 sub-tasks
3. Each sub-task spawns a new Jarvis session in the `ai` tmux session
4. After boot, each session receives its specific sub-task prompt

### Context Transfer
1. Extracts the last 30 transcript entries from the source session
2. Formats them as readable context (user messages, assistant responses, tool calls)
3. Sends the formatted context to the target session via `tmux send-keys`

### Journal Generation
1. Extracts transcript context from the selected session
2. Runs `claude -p` to generate a markdown summary
3. Writes to `journal/{date}_{session-name}.md`

---

## Architecture

```
~/tars/
├── pyproject.toml              # Package config, dependencies, CLI entrypoint
├── .gitignore
├── README.md
└── tars/
    ├── __init__.py
    ├── __main__.py             # python -m tars
    ├── app.py                  # App entry point (29 lines)
    ├── helpers.py              # Shared utilities — time_ago, truncate, escape_markup
    ├── theme.py                # Gruvbox color scheme, ASCII banner
    ├── scanner/                # Data layer — reads Claude Code files
    │   ├── models.py           # Session, TranscriptEntry, HistoricalSession
    │   ├── utils.py            # File I/O, PID checks, path resolution
    │   ├── tmux.py             # Pane detection, send-keys, spawn, resume, switch
    │   ├── sessions.py         # Scan, cleanup, history, resumable list
    │   ├── transcripts.py      # Parse JSONL transcripts, tail for live updates
    │   └── actions.py          # Context transfer, task delegation, journaling
    ├── screens/                # UI screens
    │   ├── home.py             # Card-based session list with grouping
    │   └── detail.py           # Session info, prompt history, live transcript
    └── modals/                 # Floating dialogs
        ├── confirm.py          # y/n confirmation
        ├── spawn.py            # Name input for new session
        ├── delegate.py         # Task description editor
        ├── prompt.py           # Send-keys text editor
        ├── session_picker.py   # Pick target for context transfer
        └── resume.py           # Browse and resume historical sessions
```

Each file is under 420 lines, most under 200. Single responsibility throughout.

---

## Tech Stack

- **[Textual](https://textual.textualize.io/)** — TUI framework
- **[Rich](https://rich.readthedocs.io/)** — terminal formatting
- **[psutil](https://github.com/giampaolo/psutil)** — process introspection
- **Gruvbox** — color scheme via custom `ColorSystem`
- **tmux** — session multiplexing and interaction
- **Claude Code CLI** — `claude -p` for headless AI operations

---

## Name

Named after TARS from *Interstellar* — the AI companion that monitors systems, reports status, and has a great sense of humor.
