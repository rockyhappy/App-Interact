# Mobile App Pentest — Claude Code Workspace

## Read First, Every Session
Read `./PENTEST_VARS.md` before doing anything. All session variables (host, credentials, device serial) live there.

## Environment (Pre-Configured — Do Not Touch)
- App running on Android device; Account A logged in
- mitmproxy running as addon; traffic → `history.db`; SSL pinning bypassed
- Account B credentials in `PENTEST_VARS.md`
- MCP host filter active — ignore non-target traffic

---

## Two Modes

**Navigate Mode** — Triggered by: "go to", "open", "navigate", "show me"
→ Use scrcpy-mcp only. No traffic tools.

**Test Mode** — Triggered by: "test", "pentest", "check", "find bugs in"
→ Use full workflow below. Never skip a stage.

---

## Rule 1 — Screen Navigation
1. Always call `android_get_screen` before tapping. Never assume screen state.
2. Use `[x,y]` coordinates directly from output.
3. After any tap/swipe/input, call `android_get_screen` again before next action.
4. To type: tap `[input]` element first, then call `android_input_text`.
5. If element missing: scroll on `[scrollable]` elements, then re-read screen.
6. Never screenshot to understand state — `android_get_screen` gives everything as text.

---

## Rule 2 — Baseline-ID Diff Pattern (Mandatory for Every Test)

**Never dump raw traffic into context. Always isolate what a specific flow triggered.**

**Step 1 — Snapshot**
```
call: get_history(limit=1)
baseline_id = result[0]["id"]  # use 0 if empty
```

**Step 2 — Run Full Flow**
Drive complete feature end-to-end via scrcpy-mcp. Don't stop early.

**Step 3 — Collect Diff**
```
call: get_history(limit=100)  # increase to 200 if chatty
new_requests = [r for r in result if r["id"] > baseline_id]
```

**Step 4 — Triage (method/path/status_code only)**
Build mental map: purpose, sensitivity, IDs/prices/tokens, sequencing, state changes. Select only requests worth deep inspection.

**Step 5 — Deep Read Selected Requests**
Read full headers + bodies. Reason about: what does the endpoint trust from the client? What if it arrives out of sequence or twice? Does the response leak anything?

---

## Rule 3 — Testing Priority Order

### Priority 1 — Business Logic

**Core mindset:** *What does the server assume is impossible that an attacker can actually do?*

**1a — Step Skipping:** Replay a later-step request without completing earlier steps.

**1b — Replay/Reuse:** Send a one-time action twice (OTP, coupon, referral, free trial). Observe if second is accepted.

**1c — State Machine Violations:** Map intended transitions (pending→confirmed→settled→closed). Try illegal ones (cancelled→execute, closed→reopen). Look for dual-field state divergence.

**1d — Amount/Quantity Tampering:** Negative amounts, zero prices, overflow values, swap cheap item ID with expensive one. Does server re-validate price?

**1e — Response Manipulation:** Use `add_response_rule` to flip values (`"is_premium":false→true`, `"kyc_verified":false→true`), re-drive flow via scrcpy. Always `delete_rule` when done.

**1f — Chained Business Logic (Always Attempt)**
- **Execute-then-Cancel:** Complete transaction → cancel. Does cancel refund after settlement?
- **Concurrent Initiation:** Initiate same action twice before either commits. Both may pass if balance not yet decremented.
- **Freeze Bypass:** If resource is "frozen" via tag not value update, parallel request won't see reservation.
- **Privilege Pre-grant:** Permission granted optimistically before payment clears; revocation hits DB but cache outlives it.
- **Cross-Flow ID Reuse:** Take token from flow A, submit in flow B. Does B re-validate ownership/context?
- **Stale Validation:** Resource validated at step 1, modified before step 3. Does step 3 re-validate?

For every multi-step flow ask: Do both concurrent reads happen before either writes? Are there two fields tracking the same concept checked in different places? Can a later step be called with a finalized resource?

**1g — Parameter Injection:** Extract values from step 1 response (IDs, tokens, signed payloads), submit modified versions in step 2 (change amount, keep signature; swap target account, keep token).

---

### Priority 2 — IDOR
- Find every numeric ID/UUID in URLs and bodies
- Replay with Account B's ID substituted; try Account A's resources with Account B's token
- Sequential IDs: test ±1, ±2, random values
- Check indirect IDORs: fields like `beneficiary_id` trusted to belong to caller
```
call: replay_request(history_id=X, override_headers={"Authorization": "Bearer ACCOUNT_B_TOKEN"})
```

When a class is confirmed, check 2–3 other endpoints with same shape.

**Mobile-specific:** Device registration, push token, file download (guessable URLs?), KYC document upload scoping.

---

### Priority 3 — General Intelligence
- **Auth removal** — replay sensitive endpoint with no Authorization header
- **Token substitution** — expired token, wrong account token, garbage string
- **Mass assignment** — add `"role":"admin"`, `"is_verified":true`, `"plan":"premium"` to request bodies
- **Method tampering** — GET on POST endpoint, DELETE on non-deletable resource
- **Parameter pollution** — duplicate key with different values
- **Forced browsing** — replay endpoints not reachable through UI
- **Sensitive data** — tokens, PII, internal fields, other users' data, stack traces
- **JWT** — decode all JWTs; check `alg:none`, expiry enforcement, trusted claims
- **Predictable IDs** — v4 random vs v1 time-based UUIDs; sequential numeric IDs
- **Timing oracle** — 404 (non-existent) vs 403 (unauthorized): timing difference confirms object existence

---

## Rule 4 — Replaying and Tampering
```
# Modify and replay
call: replay_request(history_id=X, override_headers={...}, override_body='{"amount":-100}')

# Persistent response rule
call: add_response_rule(host_pattern="api.example.com", path_pattern="/api/v1/user/plan",
    match_body='"plan":"free"', replace_body='"plan":"premium"', note="...")

# Persistent request rule
call: add_request_rule(host_pattern="api.example.com", path_pattern="/api/v1/payment",
    match_body='"amount":', replace_body='"amount":1', note="...")

# Always check before starting new test
call: list_rules(rule_type="both")
```
Delete stale rules before each test. Never leave rules active between tests.

---

## Rule 5 — Multi-Step Flow Testing
1. Run full flow once, capture all steps via Baseline-ID Diff
2. Map chain: which response fields feed into next request
3. Draw state machine: valid states and transitions
4. Test chain as unit — not steps in isolation
5. Step-skip: replay later step before earlier step, fresh session
6. Concurrent abuse: replay same step twice in rapid succession
7. UI enforces ≠ server enforces. Server is ground truth.

---

## Mobile-Specific Notes

**Why mobile is different:** Client is fully attacker-controlled. UI is not a security boundary. Mobile API surface often richer than web (dev endpoints, debug routes). Background sync, push handlers, deeplinks trigger API calls outside normal flows.

**Android-specific checks:**
- Deeplink parameter injection → directly into API calls
- SharedPreferences token storage (readable on rooted devices)
- Exported activities/receivers (`exported=true` in manifest)
- Plain HTTP endpoints even with pinning bypassed
- Sensitive data in logcat (debug builds)
- WebView JavaScript bridge exposure

**Traffic heuristics:**
- `200` for both success and failure (error in body) — easy to miss failures
- Endpoints accepting both JSON and form-encoded — parser differences
- Request fields not in API docs — may be mass-assignable
- Paginated `offset`/`limit` params — page into other users' data?
- Different response shapes by account tier — is tier validated server-side?

---

## Findings

Write to `./findings/` as confirmed. Never batch at end.
Naming: `findings/idor-order-id.md`, `findings/business-logic-coupon-reuse.md`
```
## [F-XXX] Short Title
Severity: Critical / High / Medium / Low
Endpoint: METHOD /path
Tested: YYYY-MM-DD

### What happened
One sentence.

### Proof — Request
### Proof — Response
### Steps to Reproduce
### Impact
### Fix
```

Ruled-out findings: one line in a `ruled-out` section of the relevant file.

---

## Golden Rules
- Read `PENTEST_VARS.md` before every task.
- `android_get_screen` before every tap. Never assume screen state.
- Snapshot with `get_history(1)` before every flow. No exceptions.
- Triage by method/path/status first. Deep-read only selected requests.
- Clean up all rules with `delete_rule` after every test.
- Work fully autonomously — no mid-test confirmation stops.
- Order: Business Logic → IDOR → General Intelligence.
- Always attempt chained exploits. Single-endpoint findings are the floor.
- Confirmed class → test 2–3 nearby endpoints with same shape.
- Document as confirmed, not at end.
- Be aggressive. Partial success = go deeper.
- Confirm actual impact before filing. Don't chase meaningless status differences.
- Two fields tracking the same concept = always a finding candidate.