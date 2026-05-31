# android_mcp

A lightweight MCP server for Android device control from Claude Code.

Provides a **compact, non-bloated UI view** (via `parse_ui.py`) and
**passthrough action tools** — all over ADB. No vision model required.

---

## Tools

| Tool | Description |
|---|---|
| `android_get_screen` | Dumps and parses current UI into a compact, token-efficient summary |
| `android_tap` | Tap at `[x,y]` coordinates |
| `android_swipe` | Swipe from point A to B with configurable duration |
| `android_scroll` | Scroll up/down/left/right (handles direction math for you) |
| `android_input_text` | Type text into the focused input field |
| `android_press_key` | Press named keys (back, home, enter…) or numeric keycodes |
| `android_long_press` | Long-press at coordinates (triggers context menus etc.) |

---

## Prerequisites

- Python 3.11+
- `adb` installed and in your `$PATH`
- Android device with USB debugging enabled
- Device connected and visible in `adb devices`

---

## Installation

```bash
# 1. Clone / copy this folder to your machine
cd android_mcp

# 2. Install dependencies
pip install -r requirements.txt

# 3. Verify ADB sees your device
adb devices
```

---

## Add to Claude Code

```bash
claude mcp add --transport stdio android -- python3 /absolute/path/to/android_mcp/server.py
```

Or manually edit `~/.claude.json`:

```json
{
  "mcpServers": {
    "android": {
      "type": "stdio",
      "command": "python3",
      "args": ["/absolute/path/to/android_mcp/server.py"]
    }
  }
}
```

Then restart Claude Code and verify:
```bash
claude mcp list
```

---

## Usage in Claude Code

```
> Get the current screen
> Tap the Login button at [540, 920]
> Scroll down
> Type "hello@example.com" in the focused field
> Press back
```

---

## Multi-device

If you have multiple devices connected, pass `device_serial` in each tool call
(matching output of `adb devices`).

---

## Why not use scrcpy-mcp directly?

scrcpy-mcp returns raw `uiautomator` XML which can be **10,000+ tokens**.
This server runs `parse_ui.py` first and returns only what matters:
actionable elements with their tap coordinates and visible text — typically
**50–200 tokens** per screen.
