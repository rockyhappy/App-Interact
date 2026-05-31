#!/usr/bin/env python3
"""
android_mcp — Wrapper MCP server for Android device control.

Provides a compact, non-bloated UI view via parse_ui.py plus
passthrough action tools (tap, swipe, scroll, input, key press).

Usage:
    python3 server.py
"""

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Optional
from enum import Enum

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

# ── Constants ─────────────────────────────────────────────────────────────────

PARSE_UI_SCRIPT = Path(__file__).parent / "parse_ui.py"

# Common Android keycodes
KEYCODES = {
    "back": 4,
    "home": 3,
    "recents": 187,
    "enter": 66,
    "delete": 67,
    "tab": 61,
    "up": 19,
    "down": 20,
    "left": 21,
    "right": 22,
    "volume_up": 24,
    "volume_down": 25,
    "power": 26,
    "menu": 82,
    "search": 84,
    "escape": 111,
    "page_up": 92,
    "page_down": 93,
}

# ── Server Init ───────────────────────────────────────────────────────────────

mcp = FastMCP("android_mcp")

# ── Shared ADB helper ─────────────────────────────────────────────────────────

def adb_cmd(serial: Optional[str]) -> list[str]:
    """Build the base adb command with optional device serial."""
    return ["adb"] + (["-s", serial] if serial else [])


async def run_adb(args: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Run an adb command asynchronously. Returns (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode(), stderr.decode()
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"ADB command timed out after {timeout}s: {' '.join(args)}")


def format_adb_error(returncode: int, stderr: str, context: str) -> str:
    """Format a consistent ADB error message."""
    hint = ""
    if "device not found" in stderr or "no devices" in stderr.lower():
        hint = " Hint: check 'adb devices' — device may be disconnected or serial wrong."
    elif "offline" in stderr:
        hint = " Hint: device is offline. Try unplugging and reconnecting."
    return f"Error: {context} failed (exit {returncode}). {stderr.strip()}{hint}"


# ── Input Models ──────────────────────────────────────────────────────────────

class DeviceInput(BaseModel):
    """Base model with optional device serial."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    device_serial: Optional[str] = Field(
        default=None,
        description="ADB device serial (e.g. 'emulator-5554' or 'R3CN20ABCDE'). "
                    "Omit when only one device is connected."
    )


class TapInput(DeviceInput):
    x: int = Field(..., description="X coordinate to tap (pixels)", ge=0)
    y: int = Field(..., description="Y coordinate to tap (pixels)", ge=0)


class SwipeInput(DeviceInput):
    x1: int = Field(..., description="Start X coordinate (pixels)", ge=0)
    y1: int = Field(..., description="Start Y coordinate (pixels)", ge=0)
    x2: int = Field(..., description="End X coordinate (pixels)", ge=0)
    y2: int = Field(..., description="End Y coordinate (pixels)", ge=0)
    duration_ms: int = Field(
        default=300,
        description="Swipe duration in milliseconds. Use 100-200 for fast flings, "
                    "500-800 for slow deliberate swipes.",
        ge=50, le=5000
    )


class ScrollDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class ScrollInput(DeviceInput):
    direction: ScrollDirection = Field(
        ...,
        description="Scroll direction: 'up', 'down', 'left', 'right'."
    )
    x: int = Field(
        default=540,
        description="X center of the scroll gesture (pixels). Default: screen center.",
        ge=0
    )
    y: int = Field(
        default=960,
        description="Y center of the scroll gesture (pixels). Default: screen center.",
        ge=0
    )
    distance: int = Field(
        default=500,
        description="Scroll distance in pixels.",
        ge=50, le=2000
    )
    duration_ms: int = Field(
        default=300,
        description="Scroll duration in milliseconds.",
        ge=50, le=2000
    )


class InputTextInput(DeviceInput):
    text: str = Field(
        ...,
        description="Text to type. Special chars are auto-escaped. "
                    "For non-ASCII, prefer android_press_key with keycode.",
        min_length=1, max_length=500
    )


class PressKeyInput(DeviceInput):
    key: str = Field(
        ...,
        description=(
            "Key name or numeric keycode. Named keys: "
            "back, home, recents, enter, delete, tab, up, down, left, right, "
            "volume_up, volume_down, power, menu, search, escape, page_up, page_down. "
            "Or pass a numeric keycode string, e.g. '66' for ENTER."
        )
    )


class LongPressInput(DeviceInput):
    x: int = Field(..., description="X coordinate to long-press (pixels)", ge=0)
    y: int = Field(..., description="Y coordinate to long-press (pixels)", ge=0)
    duration_ms: int = Field(
        default=800,
        description="Long press duration in milliseconds.",
        ge=500, le=5000
    )


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="android_get_screen",
    annotations={
        "title": "Get Android Screen (Compact UI)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def android_get_screen(params: DeviceInput) -> str:
    """Dump and parse the current Android screen into a compact, token-efficient summary.

    Returns a spatially-ordered list of interactive elements (with tap coordinates)
    and visible text nodes — NOT raw XML. This keeps the LLM context small.

    Each actionable element is shown as:
        [x,y]  🔘 Label  [flags]

    Each text node is shown as:
        📄 Text content, although sometimes, actionable can also be shown with this, so you may click if you feel it should be clikable

    Flags can include: focused, checked, selected, scrollable, input, password.

    Args:
        params (DeviceInput): Optional device serial.

    Returns:
        str: Compact screen summary ready for LLM consumption.
    """
    cmd = [sys.executable, str(PARSE_UI_SCRIPT)]
    if params.device_serial:
        cmd.append(params.device_serial)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        return "Error: UI dump timed out after 30s. Device may be locked or heavily animating."

    output = stdout.decode().strip()
    err = stderr.decode().strip()

    if proc.returncode != 0 or output.startswith("[ERROR]"):
        return output or f"Error: parse_ui.py failed. {err}"

    return output


@mcp.tool(
    name="android_tap",
    annotations={
        "title": "Tap Android Screen",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
async def android_tap(params: TapInput) -> str:
    """Tap at a specific coordinate on the Android screen.

    Use coordinates from android_get_screen output (shown as [x,y] next to 🔘 elements).

    Args:
        params (TapInput):
            - x (int): X coordinate in pixels.
            - y (int): Y coordinate in pixels.
            - device_serial (Optional[str]): ADB device serial.

    Returns:
        str: Success confirmation or error message.
    """
    args = adb_cmd(params.device_serial) + ["shell", "input", "tap", str(params.x), str(params.y)]
    rc, _, stderr = await run_adb(args)
    if rc != 0:
        return format_adb_error(rc, stderr, f"Tap at ({params.x},{params.y})")
    return f"✅ Tapped ({params.x}, {params.y})"


@mcp.tool(
    name="android_swipe",
    annotations={
        "title": "Swipe Android Screen",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
async def android_swipe(params: SwipeInput) -> str:
    """Swipe from one coordinate to another on the Android screen.

    Also used for drag-and-drop. Use short duration (100-200ms) for flings/scrolls
    and longer (500ms+) for deliberate drags.

    Args:
        params (SwipeInput):
            - x1, y1 (int): Start coordinates.
            - x2, y2 (int): End coordinates.
            - duration_ms (int): Swipe duration (default 300ms).
            - device_serial (Optional[str]): ADB device serial.

    Returns:
        str: Success confirmation or error message.
    """
    args = adb_cmd(params.device_serial) + [
        "shell", "input", "swipe",
        str(params.x1), str(params.y1),
        str(params.x2), str(params.y2),
        str(params.duration_ms)
    ]
    rc, _, stderr = await run_adb(args, timeout=params.duration_ms // 1000 + 5)
    if rc != 0:
        return format_adb_error(rc, stderr, f"Swipe ({params.x1},{params.y1})→({params.x2},{params.y2})")
    return f"✅ Swiped ({params.x1},{params.y1}) → ({params.x2},{params.y2}) over {params.duration_ms}ms"


@mcp.tool(
    name="android_scroll",
    annotations={
        "title": "Scroll Android Screen",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
async def android_scroll(params: ScrollInput) -> str:
    """Scroll the screen in a given direction using a swipe gesture.

    Prefer this over android_swipe for scrolling — it handles direction math for you.

    Args:
        params (ScrollInput):
            - direction (str): 'up', 'down', 'left', or 'right'.
            - x (int): Horizontal center of the gesture (default 540).
            - y (int): Vertical center of the gesture (default 960).
            - distance (int): Pixels to scroll (default 500).
            - duration_ms (int): Gesture duration (default 300ms).
            - device_serial (Optional[str]): ADB device serial.

    Returns:
        str: Success confirmation or error message.
    """
    cx, cy = params.x, params.y
    d = params.distance

    direction_map = {
        ScrollDirection.UP:    (cx, cy + d // 2, cx, cy - d // 2),
        ScrollDirection.DOWN:  (cx, cy - d // 2, cx, cy + d // 2),
        ScrollDirection.LEFT:  (cx + d // 2, cy, cx - d // 2, cy),
        ScrollDirection.RIGHT: (cx - d // 2, cy, cx + d // 2, cy),
    }
    x1, y1, x2, y2 = direction_map[params.direction]

    args = adb_cmd(params.device_serial) + [
        "shell", "input", "swipe",
        str(x1), str(y1), str(x2), str(y2),
        str(params.duration_ms)
    ]
    rc, _, stderr = await run_adb(args, timeout=params.duration_ms // 1000 + 5)
    if rc != 0:
        return format_adb_error(rc, stderr, f"Scroll {params.direction.value}")
    return f"✅ Scrolled {params.direction.value} by {params.distance}px"


@mcp.tool(
    name="android_input_text",
    annotations={
        "title": "Type Text on Android",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
async def android_input_text(params: InputTextInput) -> str:
    """Type text into the currently focused input field on the Android device.

    Tap the input field first using android_tap, then call this tool.
    Spaces are auto-escaped for ADB compatibility.

    Note: Non-ASCII characters (emoji, accented letters, CJK) may not type correctly
    via ADB. For those, consider using clipboard paste instead.

    Args:
        params (InputTextInput):
            - text (str): Text to type (ASCII works best).
            - device_serial (Optional[str]): ADB device serial.

    Returns:
        str: Success confirmation or error message.
    """
    # ADB requires spaces to be escaped as %s
    escaped = params.text.replace(" ", "%s")
    args = adb_cmd(params.device_serial) + ["shell", "input", "text", escaped]
    rc, _, stderr = await run_adb(args)
    if rc != 0:
        return format_adb_error(rc, stderr, "Input text")
    return f"✅ Typed: {params.text!r}"


@mcp.tool(
    name="android_press_key",
    annotations={
        "title": "Press Android Key",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
async def android_press_key(params: PressKeyInput) -> str:
    """Press a hardware or software key on the Android device.

    Named keys: back, home, recents, enter, delete, tab,
                up, down, left, right, volume_up, volume_down,
                power, menu, search, escape, page_up, page_down.

    Or pass a raw numeric keycode string (e.g. '66' for ENTER).
    Full keycode list: https://developer.android.com/reference/android/view/KeyEvent

    Args:
        params (PressKeyInput):
            - key (str): Key name or numeric keycode.
            - device_serial (Optional[str]): ADB device serial.

    Returns:
        str: Success confirmation or error message.
    """
    key_lower = params.key.lower()
    if key_lower in KEYCODES:
        keycode = str(KEYCODES[key_lower])
    elif params.key.isdigit():
        keycode = params.key
    else:
        valid = ", ".join(sorted(KEYCODES.keys()))
        return (
            f"Error: Unknown key '{params.key}'. "
            f"Named keys: {valid}. Or pass a numeric keycode."
        )

    args = adb_cmd(params.device_serial) + ["shell", "input", "keyevent", keycode]
    rc, _, stderr = await run_adb(args)
    if rc != 0:
        return format_adb_error(rc, stderr, f"Key press '{params.key}'")
    return f"✅ Pressed key: {params.key} (keycode {keycode})"


@mcp.tool(
    name="android_long_press",
    annotations={
        "title": "Long Press Android Screen",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
async def android_long_press(params: LongPressInput) -> str:
    """Long-press at a coordinate (implemented as a slow swipe in place).

    Useful for triggering context menus, drag handles, or selection modes.

    Args:
        params (LongPressInput):
            - x (int): X coordinate in pixels.
            - y (int): Y coordinate in pixels.
            - duration_ms (int): Press duration in ms (default 800ms, min 500ms).
            - device_serial (Optional[str]): ADB device serial.

    Returns:
        str: Success confirmation or error message.
    """
    # ADB has no direct long-press; a zero-distance swipe with long duration works.
    args = adb_cmd(params.device_serial) + [
        "shell", "input", "swipe",
        str(params.x), str(params.y),
        str(params.x), str(params.y),
        str(params.duration_ms)
    ]
    rc, _, stderr = await run_adb(args, timeout=params.duration_ms // 1000 + 5)
    if rc != 0:
        return format_adb_error(rc, stderr, f"Long press at ({params.x},{params.y})")
    return f"✅ Long-pressed ({params.x}, {params.y}) for {params.duration_ms}ms"


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
