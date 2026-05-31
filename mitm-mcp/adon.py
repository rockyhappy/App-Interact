"""
mitmproxy addon — logs traffic to SQLite + applies live match/replace rules.

Usage:
    mitmdump -s addon.py --listen-port 8080
"""

import fnmatch
import json
import sqlite3
from datetime import datetime

from mitmproxy import http

DB_PATH = "history.db"
TARGET_HOST = "xdevs.tech"          # change or remove to capture all hosts


# ── DB bootstrap ──────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    """Return a per-call connection (mitmproxy is async; don't share across hooks)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # safe concurrent reads/writes
    conn.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT,
            host        TEXT,
            method      TEXT,
            path        TEXT,
            status_code INTEGER,
            req_headers TEXT,
            req_body    TEXT,
            res_headers TEXT,
            res_body    TEXT
        )
    """)
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


# ── Rule engine ───────────────────────────────────────────────────────────────

def _matches(host_pattern: str, path_pattern, host: str, path: str) -> bool:
    if not fnmatch.fnmatch(host, host_pattern):
        return False
    if path_pattern and not fnmatch.fnmatch(path, path_pattern):
        return False
    return True


def apply_request_rules(flow: http.HTTPFlow, rules):
    for rule in rules:
        if not _matches(rule["host_pattern"], rule["path_pattern"],
                        flow.request.host, flow.request.path):
            continue

        # match_body / replace_body
        if rule["replace_body"] is not None:
            try:
                body = flow.request.text
            except Exception:
                body = flow.request.content.decode("utf-8", errors="replace")
            if rule["match_body"] is None or rule["match_body"] in body:
                flow.request.text = rule["replace_body"]

        # set_header_key / set_header_value
        if rule["set_header_key"] and rule["set_header_value"]:
            flow.request.headers[rule["set_header_key"]] = rule["set_header_value"]

        # remove_header
        if rule["remove_header"] and rule["remove_header"] in flow.request.headers:
            del flow.request.headers[rule["remove_header"]]


def apply_response_rules(flow: http.HTTPFlow, rules):
    for rule in rules:
        if not _matches(rule["host_pattern"], rule["path_pattern"],
                        flow.request.host, flow.request.path):
            continue

        # status_code_filter
        if rule["status_code_filter"] and flow.response.status_code != rule["status_code_filter"]:
            continue

        # match_body / replace_body
        if rule["replace_body"] is not None:
            try:
                body = flow.response.text
            except Exception:
                body = flow.response.content.decode("utf-8", errors="replace")
            if rule["match_body"] is None or rule["match_body"] in body:
                flow.response.text = rule["replace_body"]

        # set_header_key / set_header_value
        if rule["set_header_key"] and rule["set_header_value"]:
            flow.response.headers[rule["set_header_key"]] = rule["set_header_value"]

        # remove_header
        if rule["remove_header"] and rule["remove_header"] in flow.response.headers:
            del flow.response.headers[rule["remove_header"]]


# ── Addon ─────────────────────────────────────────────────────────────────────

class LogRequests:

    def _load_rules(self, table: str):
        conn = get_conn()
        rows = conn.execute(f"SELECT * FROM {table} WHERE active = 1").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── REQUEST hook ──────────────────────────────────────────────────────────
    def request(self, flow: http.HTTPFlow):
        if TARGET_HOST and not flow.request.host.endswith(TARGET_HOST):
            return

        req_rules = self._load_rules("request_rules")
        if req_rules:
            apply_request_rules(flow, req_rules)
            print(f"[⚙]  Applied {len(req_rules)} request rule(s) to {flow.request.url}")

        print(f"[→] {flow.request.method} {flow.request.url}")

        # Stash partial row for the response hook
        try:
            req_body = flow.request.text
        except Exception:
            req_body = "<binary>"

        flow.metadata["db_req"] = {
            "timestamp":   datetime.now().isoformat(),
            "host":        flow.request.host,
            "method":      flow.request.method,
            "path":        flow.request.path,
            "req_headers": json.dumps(dict(flow.request.headers)),
            "req_body":    req_body,
        }

    # ── RESPONSE hook ─────────────────────────────────────────────────────────
    def response(self, flow: http.HTTPFlow):
        if TARGET_HOST and not flow.request.host.endswith(TARGET_HOST):
            return

        res_rules = self._load_rules("response_rules")
        if res_rules:
            apply_response_rules(flow, res_rules)
            print(f"[⚙]  Applied {len(res_rules)} response rule(s) to {flow.request.url}")

        print(f"[←] {flow.response.status_code} {flow.request.url}")

        req = flow.metadata.get("db_req", {})

        try:
            res_body = flow.response.text
        except Exception:
            res_body = "<binary>"

        conn = get_conn()
        conn.execute("""
            INSERT INTO history
                (timestamp, host, method, path, status_code,
                 req_headers, req_body, res_headers, res_body)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            req.get("timestamp"),
            req.get("host"),
            req.get("method"),
            req.get("path"),
            flow.response.status_code,
            req.get("req_headers"),
            req.get("req_body"),
            json.dumps(dict(flow.response.headers)),
            res_body,
        ))
        conn.commit()
        conn.close()

get_conn()
addons = [LogRequests()]