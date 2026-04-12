# Claude Code Debate System

[中文](README.md)

Two Claude Code instances debate each other in a tmux split-pane layout.

```
┌─────────────────────────────────────────┐
│           📋  Log                        │
├───────────────────┬─────────────────────┤
│   ✅  PRO          │   ❌  CON           │
│                   │                     │
│  Claude Code #1   │  Claude Code #2     │
│                   │                     │
└───────────────────┴─────────────────────┘
```

## How It Works

1. The launcher creates a tmux session with split panes and starts one Claude Code instance in each pane
2. The PRO side speaks first. When it finishes, a **Stop hook** fires → the relay script extracts the reply and injects it into the CON pane
3. CON responds, which triggers the same Stop hook → the reply is injected back into the PRO pane
4. This alternation continues for the specified number of rounds, after which both sides deliver closing statements and the debate ends

The entire relay mechanism leverages Claude Code's [Stop hooks](https://docs.anthropic.com/en/docs/claude-code/hooks): every time Claude finishes a response, the hook triggers `relay.sh`, which extracts text, updates state, and injects the argument into the opponent's pane.

## Quick Start

### Prerequisites

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (the `claude` command)
- [tmux](https://github.com/tmux/tmux)
- [jq](https://jqlang.github.io/jq/)
- Python 3.8+

### Run

```bash
# Classic mode: plain-text debate
python3 debate_cc.py "AI will eventually surpass humanity" 5

# Search mode: debaters can search the web and cite sources
python3 debate_cc.py "AI will eventually surpass humanity" 5 --search
```

tmux opens automatically and the debate runs in real time. Press **ESC** to exit when finished.

## Two Modes

| | Classic | Search (`--search`) |
|---|---|---|
| Length per turn | 100-150 chars | 150-200 chars |
| Tool use | None | WebSearch, Bash |
| Output format | Direct argument | Research process + 【最终论点】 marker |
| `--dangerously-skip-permissions` | No | Yes (needed for tool use) |

## File Structure

```
cc-debate/
├── debate_cc.py        # Entry point: init state + launch tmux
├── relay.sh            # Relay hub: triggered by Stop hook
├── extract_text.py     # Extract assistant reply from transcript
├── state/              # Runtime state (gitignored)
│   ├── state.json      # Rounds, phase, message counts, etc.
│   ├── pro/            # PRO working directory
│   │   ├── .claude/settings.json  # Stop hook config
│   │   ├── system_prompt.txt      # System prompt
│   │   └── start.py               # Launch script
│   └── con/            # CON working directory (same structure)
└── YYYYMMDD_topic/     # Debate records (auto-generated)
    ├── 正方.md         # PRO transcript
    └── 反方.md         # CON transcript
```

## Key Design Decisions

- **Stop hooks as IPC** — Uses Claude Code's hook mechanism for inter-instance communication without any external message queue
- **Atomic state management** — `state.json` updated atomically via `jq` to prevent race conditions
- **Message-count dedup** — Tracks processed message count per side to prevent the Stop hook from reading the same message twice
- **Retry mechanism** — Thinking blocks may be written before text blocks, so text extraction retries up to 5 times
- **tmux visualization** — Watch the debate unfold in real time with a relay log panel on top

## Example Output

After running `python3 debate_cc.py "AI will eventually surpass humanity" 3`, the `20260412_ai_will_eventually_surpass_humanity/` directory contains:

- **正方.md** (PRO) — Arguments per round + closing statement
- **反方.md** (CON) — Arguments per round + closing statement

## License

**Non-Commercial License (CC BY-NC-SA 4.0)**

This project is licensed under the [Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International](https://creativecommons.org/licenses/by-nc-sa/4.0/) license.

- You are free to use, modify, and share this project **for non-commercial purposes only**
- Commercial use (including but not limited to paid products, commercial services, or enterprise deployment) **requires explicit authorization** from the author and a profit-sharing agreement on mutually agreed terms
- Unauthorized commercial use will be subject to legal action

For commercial licensing inquiries, please contact the project author.
