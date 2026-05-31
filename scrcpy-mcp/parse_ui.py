#!/usr/bin/env python3
"""
parse_ui.py — Android screen parser for Claude Code
Outputs a compact, spatially-ordered screen summary.
Preserves context: what text is near what action, top-to-bottom.

Usage:
    python3 scripts/parse_ui.py [device_serial]
"""

import subprocess, sys, re, time
from xml.etree import ElementTree as ET

# ── fetch UI XML from device ──────────────────────────────────────────────────
serial = sys.argv[1] if len(sys.argv) > 1 else None
adb_base = ['adb'] + (['-s', serial] if serial else [])
TMP = '/sdcard/.parse_ui_tmp.xml'

def adb_shell(args, **kw):
    return subprocess.run(adb_base + ['shell'] + args,
                          capture_output=True, text=True, **kw)

def set_animations(scale):
    """Enable (scale=1) or disable (scale=0) system animations."""
    for key in ['window_animation_scale',
                'transition_animation_scale',
                'animator_duration_scale']:
        adb_shell(['settings', 'put', 'global', key, str(scale)], timeout=5)

MAX_RETRIES = 3
xml_str = ''
last_err = ''

for attempt in range(MAX_RETRIES):
    # Strategy A: plain dump to temp file — works for most apps.
    # We always try to read the file regardless of exit code because
    # some apps may emit "could not get idle state" but still write XML.
    dump = subprocess.run(
        adb_base + ['shell', 'uiautomator', 'dump', TMP],
        capture_output=True, text=True, timeout=15
    )
    last_err = (dump.stdout + dump.stderr).strip()

    cat = adb_shell(['cat', TMP], timeout=10)
    xml_str = cat.stdout.strip()

    if xml_str:
        break

    # Strategy B: disable system animations so the UI reaches idle even on
    # animation-heavy apps (Instagram Reels, TikTok, etc.), dump, then restore.
    try:
        set_animations(0)
        time.sleep(0.5)  # let the UI settle

        dump2 = subprocess.run(
            adb_base + ['shell', 'uiautomator', 'dump', TMP],
            capture_output=True, text=True, timeout=15
        )
        last_err = (dump2.stdout + dump2.stderr).strip()
        cat2 = adb_shell(['cat', TMP], timeout=10)
        xml_str = cat2.stdout.strip()
    finally:
        set_animations(1)  # always restore, even if dump fails

    if xml_str:
        break

    if attempt < MAX_RETRIES - 1:
        time.sleep(1)

# Clean up temp file (best-effort)
adb_shell(['rm', '-f', TMP], timeout=5)

if not xml_str:
    print("[ERROR] uiautomator dump produced no output after retries.")
    print(f"        Last error: {last_err}")
    print("        Device may be locked, in a secure screen, or heavily animating.")
    sys.exit(1)

try:
    root = ET.fromstring(xml_str)
except ET.ParseError as e:
    print(f"[ERROR] XML parse failed: {e}")
    sys.exit(1)

# ── helpers ───────────────────────────────────────────────────────────────────
def bounds(node):
    nums = list(map(int, re.findall(r'\d+', node.get('bounds', ''))))
    if len(nums) == 4:
        return nums  # [x1, y1, x2, y2]
    return [0, 0, 0, 0]

def center(node):
    b = bounds(node)
    return (b[0]+b[2])//2, (b[1]+b[3])//2

def top(node):
    return bounds(node)[1]

def height(node):
    b = bounds(node)
    return b[3] - b[1]

def best_label(node):
    for attr in ['content-desc', 'text']:
        v = node.get(attr, '').strip()
        if v:
            return v[:80] + ('…' if len(v) > 80 else '')
    rid = node.get('resource-id', '')
    if '/' in rid:
        rid = rid.split('/')[-1]
    return rid if rid else ''

def cls_short(node):
    return node.get('class', '').split('.')[-1]

def is_actionable(node):
    return any(node.get(a) == 'true'
               for a in ['clickable', 'long-clickable', 'scrollable', 'checkable'])

def is_pure_text(node):
    t = node.get('text', '').strip()
    return bool(t) and not is_actionable(node)

def state_flags(node):
    flags = []
    if node.get('focused')  == 'true': flags.append('focused')
    if node.get('checked')  == 'true': flags.append('checked')
    if node.get('selected') == 'true': flags.append('selected')
    if node.get('scrollable') == 'true': flags.append('scrollable')
    if node.get('class') == 'android.widget.EditText': flags.append('input')
    if node.get('password') == 'true': flags.append('password')
    return flags

# ── collect all leaf-level items with position ───────────────────────────────
items = []   # list of dicts: {y, x, kind, label, flags, node}

def walk(node):
    lbl = best_label(node)

    if is_actionable(node) and lbl:
        x, y = center(node)
        items.append({
            'y': y, 'x': x,
            'kind': 'ACTION',
            'label': lbl,
            'flags': state_flags(node),
            'cls': cls_short(node),
        })

    elif is_pure_text(node):
        t = node.get('text', '').strip()
        # truncate long text but keep it meaningful
        display = t if len(t) <= 100 else t[:97] + '…'
        x, y = center(node)
        items.append({
            'y': y, 'x': x,
            'kind': 'TEXT',
            'label': display,
            'flags': [],
            'cls': cls_short(node),
        })

    for child in node:
        walk(child)

walk(root)

# ── deduplicate (same label + same y-band = same element) ────────────────────
seen = set()
unique = []
for item in items:
    key = (item['kind'], item['label'], item['y'] // 40)  # 40px band
    if key not in seen:
        seen.add(key)
        unique.append(item)

# ── sort top-to-bottom, left-to-right ────────────────────────────────────────
unique.sort(key=lambda i: (i['y'], i['x']))

# ── detect current screen / app context ──────────────────────────────────────
pkg = root.find('.//*[@package]')
package = pkg.get('package', 'unknown') if pkg is not None else 'unknown'

# ── render ────────────────────────────────────────────────────────────────────
print(f"── SCREEN  [{package}] ──────────────────────────────────")
print()

prev_y = -1
for item in unique:
    # vertical gap hint — helps Claude understand section breaks
    if prev_y >= 0 and (item['y'] - prev_y) > 120:
        print()   # blank line = visual gap on screen

    flag_str = f"  [{', '.join(item['flags'])}]" if item['flags'] else ''

    if item['kind'] == 'ACTION':
        x, y = item['x'], item['y']
        print(f"  [{x},{y}]  🔘 {item['label']}{flag_str}")
    else:
        print(f"            📄 {item['label']}")

    prev_y = item['y']

print()
print(f"── {len([i for i in unique if i['kind']=='ACTION'])} actions  "
      f"{len([i for i in unique if i['kind']=='TEXT'])} text nodes ──")
