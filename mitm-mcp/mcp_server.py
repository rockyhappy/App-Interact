import json
import sqlite3
from typing import Any, Optional, Union

import httpx
from mcp.server.fastmcp import FastMCP

DB_PATH = "history.db"

mcp = FastMCP("BurpMCP")

# ── DB helper ──────────────────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS response_rules (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            host_pattern       TEXT    NOT NULL,
            path_pattern       TEXT,
            status_code_filter INTEGER,
            match_body         TEXT,
            replace_body       TEXT,
            set_header_key     TEXT,
            set_header_value   TEXT,
            remove_header      TEXT,
            note               TEXT,
            active             INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS request_rules (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            host_pattern     TEXT    NOT NULL,
            path_pattern     TEXT,
            match_body       TEXT,
            replace_body     TEXT,
            set_header_key   TEXT,
            set_header_value TEXT,
            remove_header    TEXT,
            note             TEXT,
            active           INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.commit()
    return conn


def _coerce_str(val: Any) -> Optional[str]:
    """Serialize dicts/lists to JSON string; pass strings through unchanged."""
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    return str(val)

def row_to_dict(row):
    """Convert a sqlite3.Row to a clean dict, parsing JSON fields."""
    d = dict(row)
    for field in ("req_headers", "res_headers"):
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except Exception:
                pass
    return d

# ── Tool 1: get_history by limit ───────────────────────────────────────────────
@mcp.tool()
def get_history(limit: int = 20) -> list:
    """
    Fetch the latest N requests from history, newest first.
    Returns: list of {id, timestamp, host, method, path, status_code,
                       req_headers, req_body, res_headers, res_body}
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


# ── Tool 2: get_history between timestamps ─────────────────────────────────────
@mcp.tool()
def get_history_between(start: str, end: str, host_filter: Optional[str] = None) -> list:
    """
    Fetch requests between two ISO timestamps.
    Args:
        start: ISO timestamp e.g. "2024-01-01T10:00:00"
        end:   ISO timestamp e.g. "2024-01-01T11:00:00"
        host_filter: optional host substring e.g. "famapp.in"
    Returns: list of matching history rows
    """
    conn = get_conn()
    if host_filter:
        rows = conn.execute(
            "SELECT * FROM history WHERE timestamp BETWEEN ? AND ? AND host LIKE ? ORDER BY id DESC",
            (start, end, f"%{host_filter}%")
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM history WHERE timestamp BETWEEN ? AND ? ORDER BY id DESC",
            (start, end)
        ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


# ── Tool 3: replay a request with optional modifications ───────────────────────
@mcp.tool()
def replay_request(
    history_id: int,
    override_headers: Optional[dict] = None,
    override_body: Optional[Union[str, dict, list]] = None,
) -> dict:
    """
    Replay a saved request from history, optionally with modified headers/body.
    Args:
        history_id:       ID from the history table
        override_headers: dict of headers to add or overwrite  e.g. {"Authorization": "Bearer xyz"}
        override_body:    new request body as string, dict, or list (replaces original)
    Returns: {status_code, res_headers, res_body}
    """
    conn = get_conn()
    row = conn.execute("SELECT * FROM history WHERE id = ?", (history_id,)).fetchone()
    conn.close()

    if not row:
        return {"error": f"No history entry with id={history_id}"}

    row = row_to_dict(row)

    # build headers
    headers = row.get("req_headers") or {}
    if override_headers:
        headers.update(override_headers)

    # remove headers that would cause issues with httpx
    for h in ("host", "content-length", "transfer-encoding"):
        headers.pop(h, None)

    body = _coerce_str(override_body) if override_body is not None else row.get("req_body") or ""
    url = f"https://{row['host']}{row['path']}"

    try:
        with httpx.Client(verify=False) as client:
            response = client.request(
                method=row["method"],
                url=url,
                headers=headers,
                content=body.encode() if body else None,
                timeout=15,
            )
        return {
            "status_code": response.status_code,
            "res_headers": dict(response.headers),
            "res_body":    response.text,
        }
    except Exception as e:
        return {"error": str(e)}


# ── Tool 4: add a response rule (match & replace) ──────────────────────────────
@mcp.tool()
def add_response_rule(
    host_pattern: str,
    path_pattern: Optional[str] = None,
    status_code_filter: Optional[int] = None,
    match_body: Optional[str] = None,
    replace_body: Optional[Union[str, dict, list]] = None,
    set_header_key: Optional[str] = None,
    set_header_value: Optional[str] = None,
    remove_header: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    """
    Add a rule that automatically modifies responses matching the pattern.
    Supports wildcards in host_pattern and path_pattern e.g. "*.famapp.in", "/api/v1/*"

    Examples:
      - Fake premium: host_pattern="api.famapp.in", match_body='"premium":false', replace_body with true
      - Force 200: host_pattern="api.famapp.in", status_code_filter=403, replace_body='{"ok":true}'
      - Remove a header: host_pattern="*.famapp.in", remove_header="x-rate-limit"
    """
    conn = get_conn()
    cursor = conn.execute("""
        INSERT INTO response_rules
        (host_pattern, path_pattern, status_code_filter, match_body, replace_body,
         set_header_key, set_header_value, remove_header, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (host_pattern, path_pattern, status_code_filter, match_body, _coerce_str(replace_body),
          set_header_key, set_header_value, remove_header, note))
    conn.commit()
    rule_id = cursor.lastrowid
    conn.close()
    return {"status": "ok", "rule_id": rule_id}


# ── Tool 5: add a request rule (match & replace) ───────────────────────────────
@mcp.tool()
def add_request_rule(
    host_pattern: str,
    path_pattern: Optional[str] = None,
    match_body: Optional[str] = None,
    replace_body: Optional[Union[str, dict, list]] = None,
    set_header_key: Optional[str] = None,
    set_header_value: Optional[str] = None,
    remove_header: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    """
    Add a rule that automatically modifies outgoing requests matching the pattern.
    Supports wildcards e.g. "*.famapp.in", "/api/v1/*"

    Examples:
      - Swap auth token: host_pattern="api.famapp.in", set_header_key="Authorization", set_header_value="Bearer fake"
      - Modify request body: host_pattern="api.famapp.in", path_pattern="/api/login", match_body='"role":"user"', replace_body with admin
    """
    conn = get_conn()
    cursor = conn.execute("""
        INSERT INTO request_rules
        (host_pattern, path_pattern, match_body, replace_body,
         set_header_key, set_header_value, remove_header, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (host_pattern, path_pattern, match_body, _coerce_str(replace_body),
          set_header_key, set_header_value, remove_header, note))
    conn.commit()
    rule_id = cursor.lastrowid
    conn.close()
    return {"status": "ok", "rule_id": rule_id}


# ── Bonus: list and delete rules ───────────────────────────────────────────────
@mcp.tool()
def list_rules(rule_type: str = "both") -> dict:
    """
    List all active rules.
    Args:
        rule_type: "request", "response", or "both"
    """
    conn = get_conn()
    result = {}
    if rule_type in ("request", "both"):
        rows = conn.execute("SELECT * FROM request_rules WHERE active = 1").fetchall()
        result["request_rules"] = [dict(r) for r in rows]
    if rule_type in ("response", "both"):
        rows = conn.execute("SELECT * FROM response_rules WHERE active = 1").fetchall()
        result["response_rules"] = [dict(r) for r in rows]
    conn.close()
    return result


@mcp.tool()
def delete_rule(rule_type: str, rule_id: int) -> dict:
    """
    Deactivate a rule by ID.
    Args:
        rule_type: "request" or "response"
        rule_id:   ID of the rule to remove
    """
    table = "request_rules" if rule_type == "request" else "response_rules"
    conn = get_conn()
    conn.execute(f"UPDATE {table} SET active = 0 WHERE id = ?", (rule_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted", "rule_id": rule_id}


if __name__ == "__main__":
    mcp.run()