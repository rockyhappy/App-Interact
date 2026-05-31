# AppTap

A Claude Code workspace for mobile app penetration testing. Combines two MCP servers — one for traffic interception and one for Android device control — so Claude can drive an end-to-end pentest autonomously: navigate the app, capture traffic, tamper requests/responses, and replay attacks.

## Demo
https://pub-2b95e43d27bd4426853976f68f492734.r2.dev/ezgif-7f11cde1cda048cb.mov

https://github.com/user-attachments/assets/1f6da0cf-370f-40b8-b132-968f087c3377

---

## Architecture

```
Claude Code
├── mitm-mcp/        MCP server — traffic history, replay, match/replace rules
│   ├── mcp_server.py    FastMCP server (tools exposed to Claude)
│   └── adon.py          mitmproxy addon (captures traffic → history.db)
│
└── scrcpy-mcp/      MCP server — Android device control via ADB
    ├── server.py        FastMCP server (tap, swipe, scroll, input, key)
    └── parse_ui.py      Parses uiautomator XML → compact token-efficient summary
```

Traffic flows: `Android app → mitmproxy (adon.py) → history.db ← mcp_server.py ← Claude`

---

## MCP Servers

### mitm-mcp — Traffic Interception & Manipulation

| Tool | Description |
|---|---|
| `get_history` | Fetch latest N requests from history (newest first) |
| `get_history_between` | Fetch requests between two ISO timestamps |
| `replay_request` | Replay a saved request with optional header/body overrides |
| `add_response_rule` | Add persistent rule to auto-modify matching responses |
| `add_request_rule` | Add persistent rule to auto-modify matching outgoing requests |
| `list_rules` | List all active request/response rules |
| `delete_rule` | Deactivate a rule by ID |

Rules support wildcards (`*.example.com`, `/api/v1/*`) and can match/replace body content, set or remove headers, or filter by status code.

### scrcpy-mcp — Android Device Control

| Tool | Description |
|---|---|
| `android_get_screen` | Dump current UI as a compact, token-efficient summary (50–200 tokens vs 10k+ raw XML) |
| `android_tap` | Tap at `[x,y]` coordinates |
| `android_swipe` | Swipe from point A to B with configurable duration |
| `android_scroll` | Scroll up/down/left/right (handles direction math) |
| `android_input_text` | Type text into the focused input field |
| `android_press_key` | Press named keys (`back`, `home`, `enter`…) or numeric keycodes |
| `android_long_press` | Long-press at coordinates (triggers context menus etc.) |

---

## Prerequisites

- Python 3.11+
- `adb` installed and in `$PATH`
- Android device with USB debugging enabled and visible in `adb devices`
- mitmproxy installed and SSL pinning bypassed on the target app
- Claude Code CLI

---

## Setup

```bash
# 1. Create and activate virtualenv
python3 -m venv .venv
source .venv/bin/activate

# 2. Install mitm-mcp dependencies
pip install -r mitm-mcp/requirements.txt

# 3. Install scrcpy-mcp dependencies
pip install -r scrcpy-mcp/requirements.txt
```

### Start mitmproxy with the addon

```bash
mitmdump -s mitm-mcp/adon.py --listen-port 8080 --ssl-insecure
```

This creates `history.db` in the project root and begins capturing traffic from the target host (`xdevs.tech` by default — edit `TARGET_HOST` in `adon.py` to change).

### Register MCP servers with Claude Code

```bash
# Traffic interception server
claude mcp add --transport stdio mitm-mcp -- python3 /absolute/path/to/mitm-mcp/mcp_server.py

# Android control server
claude mcp add --transport stdio scrcpy-mcp -- python3 /absolute/path/to/scrcpy-mcp/server.py
```

Verify:
```bash
claude mcp list
```

---

## Configuration

Copy and fill in session variables before each pentest:

```
PENTEST_VARS.md   — target host, Account B credentials, device serial
```

The `adon.py` addon filters traffic to `TARGET_HOST` by default. Update the constant at the top of the file to match your target.

---

## Pentest Workflow

The workspace follows a structured testing methodology defined in `CLAUDE.md`:

1. **Snapshot** — `get_history(limit=1)` to record baseline ID
2. **Drive flow** — use scrcpy-mcp to navigate the app end-to-end
3. **Diff** — collect only requests with `id > baseline_id`
4. **Triage** — scan method/path/status; select requests worth deep inspection
5. **Test** — replay, tamper, and chain attacks via mitm-mcp tools

Priority order: Business Logic → IDOR → General Intelligence

Findings are written to `findings/` as they are confirmed (not batched at the end).

---

## Project Structure

```
coworker/
├── CLAUDE.md           Pentest methodology and rules for Claude
├── PENTEST_VARS.md     Session variables (gitignored)
├── history.db          mitmproxy traffic log (gitignored)
├── mitm-mcp/
│   ├── adon.py         mitmproxy addon
│   ├── mcp_server.py   MCP server
│   └── requirements.txt
├── scrcpy-mcp/
│   ├── server.py       MCP server
│   ├── parse_ui.py     UI XML parser
│   └── requirements.txt
├── findings/           Confirmed vulnerabilities (gitignored)
├── scripts/            Utility scripts (gitignored)
└── apk/                Target APK files
```
