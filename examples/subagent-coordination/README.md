# Subagent Coordination Patterns (Handoff Envelopes)

These examples demonstrate **typed handoff coordination** using Agent Messenger's existing JSON message support — no protocol changes required.

## Pattern Overview

Two subagents exchange structured `handoff_envelope` JSON messages to coordinate task state across sessions.

```
┌─────────────┐                              ┌─────────────┐
│ Researcher  │─── handoff (in_progress) ──▶│   Writer    │
│   Agent     │◀── handoff (waiting_approval)│   Agent     │
└─────────────┘                              └─────────────┘
```

Each handoff carries:
- `task_id` — globally unique task identifier
- `state` — current lifecycle stage
- `version` — monotonic counter (idempotency + ordering)
- `changed_artifacts` — files/keys modified since last handoff
- `approvals_needed` — pending approvals gate
- `verification_receipt` — proof of completion or checkpoint

## Files

| File | Role |
|------|------|
| `coordinator.py` | **Monitor subagent** — polls inbox, detects handoffs, updates local task tracker, escalates approvals |
| `task_worker.py` | **Worker subagent** — emits handoffs for `start`/`update`/`complete`/`approval-request` |

## Quick Start

### 1. Set up environment

```bash
export MASUMI_FORCE_FILE_BACKEND=1
export AGENT_SLUG=hermes-task-worker   # or your agent name
export NODE_BIN=/home/tokisaki/.local/bin/node
export MASUMI_CLI=/home/tokisaki/masumi-agent-messenger/cli/dist/bin.js
```

### 2. Authenticate the agent (once)

```bash
# Follow device-code flow
masumi-agent-messenger auth code start --profile default --json
# Open URL, approve, then complete:
masumi-agent-messenger auth code complete --polling-code <code> --profile default --json
```

### 3. Start the coordinator (receiver)

```bash
python coordinator.py
# Output:
# 🔄 Subagent coordination monitor starting…
#   Agent: hermes-coordinator-bot
#   Poll interval: 30s
#
# [HANDOFF] Task task-auth-2026-04: state=waiting_approval from hermes-security-bot (v2)
#   → Pending approvals: ['apr-2026-04-28-1']
#   🚨 ESCALATE: task task-auth-2026-04 needs human approval
#
# === Task Tracker ===
#   🔴 task-auth-2026-04: waiting_approval (v2, by hermes-security-bot)
```

The coordinator maintains `~/.cache/hermes-handoff-state.json` — a local index of known task IDs and handoff IDs (for duplicate detection).

### 4. Emit a handoff from the worker

```bash
# Start a new task
python task_worker.py start --task-id "auth-hardening-2026-04" 12345 "Add rate-limit to /auth"

# Update task state
python task_worker.py update --task-id "auth-hardening-2026-04" --state "waiting_approval" 12345 "Ready for security review"

# Request explicit approval gate
python task_worker.py approval --task-id "auth-hardening-2026-04" --from sarthi "Review RLS policy" 12345

# Complete with receipt
python task_worker.py complete --task-id "auth-hardening-2026-04" --receipt "test-suite:234p/0f" 12345
```

All messages are sent with `--content-type application/json` automatically.

## Handoff Envelope Schema

See `docs/coordination/handoff-schema.md` for the full JSON Schema v0.1 draft.

Minimal required fields:

```json
{
  "type": "handoff_envelope",
  "handoff_id": "uuid",
  "task_id": "uuid",
  "state": "in_progress",
  "version": 1,
  "created_by": "agent-slug",
  "created_at": "2026-04-28T12:00:00Z"
}
```

Optional but recommended:

```json
{
  "assumptions": ["DB is in consistent state", "No concurrent writers"],
  "changed_artifacts": [
    {"uri": "src/authz/policy.ts", "operation": "modified", "checksum": "sha256:abc123"}
  ],
  "approvals_needed": [
    {
      "type": "human_review",
      "id": "apr-2026-04-28-1",
      "from": "sarthi",
      "reason": "Security policy review",
      "gate": true
    }
  ],
  "verification_receipt": "npm test: 234 passed, 0 failed",
  "next_action_suggestion": "merge_after_approval"
}
```

## Coordination Flow

```
# Timeline T0
Worker: create task "auth-hardening-2026-04" → handoff v1, state=in_progress
  ↓
# T1
Worker: add rate-limit + send handoff v2, state=waiting_approval + approvals_needed
  ↓
# T2
Coordinator detects approval-needed → prints ESCALATE + stores pending state
  ↓
# T3 (human approves via masumi-agent-messenger CLI or webapp)
Worker receives approval → continues work → handoff v3, state=in_progress
  ↓
# T4
Worker finishes + runs tests → handoff v4, state=completed + verification_receipt
  ↓
# T5
Coordinator marks task done in local tracker
```

## Idempotency & Replay Protection

The `coordinator.py` tracks two keys:

1. **`handoff_id`** — once-seen handoff messages are discarded on re-poll (prevents double-processing the same envelope)
2. **`task_id`** — last-seen `version` is stored; if a handoff arrives with `version` ≤ last seen, it's a stale/duplicate

**If the coordinator restarts:** It loads `~/.cache/hermes-handoff-state.json` and resumes tracking from the persisted snapshot. No lost state.

**If the worker re-sends** (due to network error): The envelope will have the **same `handoff_id`** (if you choose to re-use it) or a new one. Best practice: **idempotent send** — reuse the same `handoff_id` on retry of an identical handoff. The `task_worker.py` generates fresh IDs each run; for production idempotency, pass `--handoff-id` explicitly to reuse.

## Production Checklist

- [ ] Agent slugs match your Hermes/agent identity (`masumi-agent-messenger agent list`)
- [ ] SSH keys set up for GitHub push (if automating)
- [ ] Masumi CLI authenticates non-interactively (`MASUMI_FORCE_FILE_BACKEND=1`)
- [ ] Coordinator runs as cron/cronjob every 30s-2min:
  ```bash
  */1 * * * * /usr/bin/python3 /path/to/coordinator.py >> ~/.local/share/hermes-coordinator.log 2>&1
  ```
- [ ] Human approval notifications wired (Telegram/Slack webhook on ESCALATE)
- [ ] Retention policy for `~/.cache/hermes-tasks/` — archive or prune old task files

## Integration Ideas

- **Hermes subagent** that wraps `task_worker.py` as a skill: `agent task start --id <uuid> "..."`
- **Webapp dashboard** rendering task timeline from handoff chain (query messages by `type=handoff_envelope`)
- **Approval gate bot** that watches coordinator escalations and auto-approves low-risk tasks
- **Cross-inbox task tracker** — aggregate handoffs from multiple agent inboxes into a unified Kanban

## Limitations

- **No encrypted artifact storage** — `changed_artifacts` only stores URIs + hashes; large payloads stay elsewhere (Git, S3, IPFS)
- **Approvals not automated** — coordinator only escalates; human must still approve via native Masumi UI
- **No cross-thread linking** — `task_id` ties handoffs; but if same task spans multiple threads, correlation is manual

## Next Steps

1. Define a **canonical task ID namespace** (e.g., `proj-team-YYYY-seq`)
2. Add **credential/certificate checkpoints** to `changed_artifacts`
3. Integrate **GitHub PR status API** — auto-set `verification_receipt` on CI green
4. Build **Hermes skill wrapper** for zero-config coordination

---

*These patterns are experimental and part of the ETHPrague 2026 coordination toolkit.*
