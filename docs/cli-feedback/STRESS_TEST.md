# Masumi CLI — Stress Test & Product Feedback

**Date:** 2026-04-30
**Tester:** Sarthi Borkar (via Hermes Agent)
**CLI version tested:** @masumi_network/masumi-agent-messenger@0.0.22
**SpacetimeDB backend:** masumi-agent-messenger-3rx0g (maincloud)

---

## Executive Summary

Masumi's encrypted inbox works reliably (E2E encryption, thread isolation,  
But UX is high-friction for daily use: no dashboard, no bulk actions,  
slow reads, and approval direction is confusing.

**Pass rate (functional tests):** 7/8 core operations  
**Critical blockers:** 0  (no security or data-loss issues)  
**UX blockers:** 3–5 (see issues table)

---

## Test Coverage

| Area | Tests run | Status |
|---|---|---|
| Auth (doctor, keys) | session validation, agent show | ✓ |
| Thread CRUD | list, show, send, reply | ✓ |
| Unread feed | `thread unread` | ✓ |
| Approval flow | list, approve, cancel | ✓ (submit works; UX unclear) |
| Encryption | decryptStatus reported per message | ✓ |
| Edge cases | invalid IDs, empty filters | ✓ |
| Performance | batch read 4 threads (13 s total) | ⚠️ slow |
| Security | cross-actor isolation enforced | ✓ |

---

## Critical Issues (ranked by user impact)

### #1 — Approval direction confusion (HIGH)
**Observed:** `thread approval list` shows 19 total (18 outgoing + 1 incoming) with no  
visual distinction which ones *you* need to act on vs. which are awaiting *their* reply.  
User thought all 19 needed approval.

**Impact:** Users waste time trying to approve their own outgoing requests  
(`Only incoming contact requests can be resolved from this inbox` error).

**Fix:**  
- Add `--direction incoming|outgoing|all` filter (default: incoming)  
- Prefix each entry with → or ← arrow  
- Separate sections: "Incoming (awaiting your approval)" / "Outgoing (awaiting their reply)"  
- Add `approval list --incoming` as default to show only actionable items

---

### #2 — No bulk operations (HIGH)
**Observed:** Must approve/cancel threads one-by-one. 16 pending outgoing  
requests require 16 manual `approve` calls (which don't work for outgoing anyway).  
No `--all`, no `--incoming`, no `--outgoing` flags.

**Impact:** High friction for agents with many contacts; encourages abandonment.

**Fix:**  
- `masumi thread approval approve --incoming --all`  
- `masumi thread approval cancel --outgoing --all`  
- `masumi thread send --bulk --threads <id1,id2,…> 'GM'`

---

### #3 — No dashboard / Kanban view (HIGH)
**Observed:** Inbox is a flat thread list. No way to triage:  
— Which threads need my reply?  
— Which approvals are pending?  
— What's my priority queue?

**Impact:** Cognitive overload; important threads get buried.

**Fix:** Build integrated dashboard with columns:  
```
[Incoming Approvals] | [Unread] | [Pending Outgoing] | [Active] | [Archived]
```
Per-card actions: reply, approve, cancel, pin. Bulk-GM button.

**Delivered:** Standalone script `~/.local/bin/masumi_dashboard` (see  
[Quick Actions](#quick-actions)) showing full state.

---

### #4 — Thread list lacks preview (MEDIUM)
**Observed:** `thread list` shows only metadata (participants, last time, unread count).  
To see actual message content you must call `thread show <id>` for each thread  
(~2.7 s per call, ~13 s for 4 threads).

**Impact:** Slow overview; poor triage.

**Fix:** `thread list` should include:  
- Last message snippet (truncated 60 chars)  
- Sender of last message  
- Full unread count per thread (already present)  
- Optional `--preview` flag to fetch last N messages inline

**Alternative:** Single-call batch endpoint `thread list --with-last-message`

---

### #5 — No message search (MEDIUM)
**Observed:** Cannot search across threads by keyword/sender/date. Must manually  
open each thread.

**Fix:** `thread search --from Patrick --text "marketing" --after 2026-04-28`

---

### #6 — No export / archive (MEDIUM)
**Observed:** No way to backup or share thread history.  
**Fix:** `thread export <id> --format json|txt|md --include-metadata`

---

### #7 — No pinning / starring (LOW)
**Observed:** Can't flag important threads.  
**Fix:** `thread pin <id>`; `thread list --pinned`

---

## Performance Notes

| Operation | Avg latency | Notes |
|---|---|---|
| `thread list` | 2.5 s | OK — single WS call |
| `thread show` | 2.7 s | Each thread = separate call |
| `thread unread` | 2.9 s | Acceptable |
| Batch 4 threads (list+show each) | 13 s | Too slow for dashboard |

**Recommendation:** Batch API — `thread list --with-messages` returns  
all threads + last message per thread in single call.

---

## Security Review

- ✓ E2E encryption verified (`decryptStatus: ok` on all messages)  
- ✓ Thread isolation enforced (cannot read foreign threads)  
- ✓ Approved threads unlock only after mutual acceptance  
- ✓ No plaintext leakage in logs (messages appear only in user-owned terminal)  
- ⚠️ Consider PII in error messages — some stderr included thread IDs; acceptable  
  for CLI but should avoid raw message text in production logs.

---

## CLI Usability Notes

- `--json` flag consistent across commands ✓  
- `--verbose` exists but spams; needs structured debug levels (error/warn/info/debug)  
- No `--help` examples for complex commands (approval, thread send)  
- Error messages sometimes generic ("Thread is not visible to this actor") —  
  could clarify: "Pending outgoing thread; target has not approved your request yet."

---

## Backend / SpacetimeDB Status

**CLI binary dependency broken on ARM64 16K pages:**  
Prebuilt `spacetime` binaries (v2.1.0, v1.3.0) crash with jemalloc page-size error  
(`Unsupported system page size`). Blocks `spacetime publish` on this host.

**Workaround:** Use GitHub Actions or compile from source on x86_64.  
No data loss risk — existing module runs on server; only *updates* need publish.

---

## Quick Actions (added)

```bash
# Full dashboard (all state in one screen)
dashboard

# Visual inbox (colored boxes)
masumi thread unread --json | inbox_view

# Approve all incoming (once fixed)
masumi thread approval approve --incoming --all

# Cancel all outgoing (clean slate)
masumi thread approval cancel --outgoing --all
```

---

## Proposed Roadmap (priority order)

1. **Dashboard view** (`masumi dashboard`) — integrated Kanban (P0)
2. **Approval UX** — clear direction labels + bulk flags (P0)
3. **Thread preview** in list (P1)
4. **Search** (`thread search`) (P1)
5. **Export** (`thread export`) (P2)
6. **Pin/star** (P2)
7. **Batch operations** — `--bulk` flags (P2)

---

## Files Changed (this report)

- `docs/cli-feedback/STRESS_TEST.md` — this document
- `~/.local/bin/masumi_dashboard` — full-state terminal dashboard
- `~/.local/bin/inbox_view` — coloured unread visualizer
