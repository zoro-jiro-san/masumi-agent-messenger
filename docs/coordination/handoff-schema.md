# Typed Handoff Coordination — Proposed Enhancement

## Problem Statement (from r/ClaudeAI, April 2026)

> "This is a useful primitive. The part I'd be strict about is making the inbox carry typed handoff state, not just messages. For cross-agent work I'd want each handoff to include: task boundary, current assumptions, changed files/artifacts, approvals needed, and verification receipts. Otherwise async messaging solves transport but leaves each agent reconstructing authority from prose."

**Gap identified:** Agent Messenger currently provides **encrypted message transport** but no **structured handoff envelope**. Multi-agent workflows must reconstruct task boundaries, ownership, and completion status by parsing free-form text — brittle and unreliabile.

**Secondary issue:** "When one session is waiting for input and another one is working, it's easy to lose track." Lack of canonical task state tracking across agent sessions.

**Idempotency concern:** "If an agent reopens the project tomorrow, can it tell whether a request was already acted on, superseded, or still waiting for approval?" No replay/dup detection built into message semantics.

---

## Proposed Solution: HandoffEnvelope JSON Schema

A **handoff message** is a structured JSON payload (sent with `--content-type application/json`) carrying explicit coordination metadata. Agents parse the envelope, not prose, to determine:

1. **Task boundary** — what work unit begins/ends here
2. **Current assumptions** — invariants, constraints, context
3. **Changed artifacts** — files, keys, DB entries modified
4. **Approvals queue** — pending human/agent approvals required
5. **Verification receipt** — proof of prior completion
6. **Idempotency guard** — deduplication key for safe replay

### Schema v0.1 (draft)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://schemas.masumi.net/agent-messenger/handoff/v0.1.json",
  "title": "Agent Messenger Handoff Envelope",
  "description": "Structured coordination metadata for cross-agent task handoffs",
  "type": "object",
  "required": ["type", "handoff_id", "task_id", "state", "version"],
  "properties": {
    "type": {
      "type": "string",
      "const": "handoff_envelope",
      "description": "Discriminator — distinguishes handoff from plain messages"
    },
    "handoff_id": {
      "type": "string",
      "format": "uuid",
      "description": "Unique identifier for this handoff message (for dedup/replay tracking)"
    },
    "task_id": {
      "type": "string",
      "format": "uuid",
      "description": "Canonical task identifier — ties handoffs across threads/agents together"
    },
    "state": {
      "type": "string",
      "enum": ["in_progress", "waiting_input", "waiting_approval", "completed", "blocked", "cancelled", "superseded"],
      "description": "Current state of the task from sender's perspective"
    },
    "version": {
      "type": "integer",
      "minimum": 1,
      "description": "Monotonic handoff version for the same task_id — enables ordering and conflict detection"
    },
    "created_by": {
      "type": "string",
      "description": "Agent slug that created/owns this task"
    },
    "created_at": {
      "type": "string",
      "format": "date-time",
      "description": "When this handoff was generated"
    },
    "previous_handoff_id": {
      "type": ["string", "null"],
      "format": "uuid",
      "description": "If updating an existing task, the handoff_id of the immediately prior state"
    },
    "assumptions": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Key assumptions holding at task handoff — if violated, task context is stale"
    },
    "changed_artifacts": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["uri"],
        "properties": {
          "uri": {"type": "string", "description": "Artifact identifier (file path, DB key, URL, etc.)"},
          "operation": {"type": "string", "enum": ["created", "modified", "deleted", "read"]},
          "checksum": {"type": "string", "description": "SHA256 or similar content hash"},
          "summary": {"type": "string", "description": "One-line change description"}
        }
      },
      "description": "Artifacts touched by this task since last handoff"
    },
    "approvals_needed": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["type", "id"],
        "properties": {
          "type": {"type": "string", "enum": ["human_review", "agent_ack", "policy_check"]},
          "id": {"type": "string", "description": "Approval item ID for tracking"},
          "from": {"type": "string", "description": "Who must approve (agent slug, role, or identity)"},
          "reason": {"type": "string", "description": "Why approval is required"},
          "timeout": {"type": "string", "format": "duration", "description": "Auto-expiry if not granted"},
          "gate": {"type": "boolean", "description": "If true, task cannot proceed past this point without approval"}
        }
      },
      "description": "Pending approvals required before task can advance"
    },
    "verification_receipt": {
      "type": ["string", "null"],
      "description": "Proof of completion or checkpoint: hash/signature of artifact state or test result"
    },
    "idempotency_key": {
      "type": "string",
      "format": "uuid",
      "description": "Client-generated idempotency key for duplicate detection on retry"
    },
    "next_action_suggestion": {
      "type": ["string", "null"],
      "description": "Recommended follow-up for receiving agent (e.g., 'review PR', 'deploy', 'await human input')"
    },
    "metadata": {
      "type": "object",
      "additionalProperties": true,
      "description": "Extension point — agent/tool-specific data not covered by core schema"
    }
  },
  "additionalProperties": false
}
```

---

## Agent Workflow Pattern

### Sender emits a handoff

```bash
# Build envelope as JSON
ENVELOPE=$(cat <<'EOF'
{
  "type": "handoff_envelope",
  "handoff_id": "550e8400-e29b-41d4-a716-446655440000",
  "task_id": "task-auth-hardening-2026-04",
  "state": "waiting_approval",
  "version": 3,
  "created_by": "hermes-security-bot",
  "created_at": "2026-04-28T12:00:00Z",
  "assumptions": ["OWASP Top 10 addressed", "RLS policies validated"],
  "changed_artifacts": [
    {"uri": "src/authz/guard.ts", "operation": "modified", "checksum": "sha256:abc123"},
    {"uri": "migrations/004_rls.sql", "operation": "created"}
  ],
  "approvals_needed": [
    {"type": "human_review", "id": "apr-2026-04-28-1", "from": "sarthi", "gate": true}
  ],
  "verification_receipt": "test-suite: 234 passed, 0 failed",
  "idempotency_key": "idem-7c9a7b3e",
  "next_action_suggestion": "review_and_merge"
}
EOF
)

# Send with JSON content type
masumi-agent-messenger thread reply 12345 "$ENVELOPE" --content-type application/json
```

### Receiver processes handoff

```python
import json
from masumi_agent_messenger import list_threads, get_thread

def process_handoff(thread_id):
    # Fetch thread messages
    thread = get_thread(thread_id)
    
    for msg in thread.messages:
        if msg.content_type != 'application/json':
            continue
            
        payload = json.loads(msg.body)
        if payload.get('type') != 'handoff_envelope':
            continue
        
        # Idempotency check — has this handoff_id been processed?
        if already_processed(payload['handoff_id']):
            print(f"Duplicate handoff {payload['handoff_id']} — skipping")
            continue
        
        # Task state routing
        task_id = payload['task_id']
        state = payload['state']
        
        if state == 'waiting_approval':
            route_to_human_approval_queue(task_id, payload)
        elif state == 'in_progress':
            update_task_tracker(task_id, payload)
        elif state == 'completed':
            mark_task_done(task_id, payload['verification_receipt'])
        
        mark_handoff_processed(payload['handoff_id'], payload['task_id'])
```

---

## Changes Required (three-part PR)

### Part A — Documentation (in `masumi-agent-messenger` repo)

**Files added:**
- `docs/coordination/handoff-schema.md` — this spec
- `docs/coordination/subagent-patterns.md` — how to build send/receive agents using handoffs
- `examples/subagent-coordination/` — complete working Python/Node.js examples

**Files updated:**
- `README.md` — mention coordination layer in feature list
- `skills/masumi-agent-messenger/SKILL.md` — add handoff section with shell snippet examples
- `AGENTS.md` — note new recommended practice for multi-agent work

### Part B — Schema Validation Library (optional, non-breaking)

Add TypeScript types + JSON Schema validator in `shared/`:
- `shared/handoff-envelope.ts` — TypeScript types
- `shared/handoff-validator.ts` — runtime validation using Ajv
- Register as ESM/CJS for CLI and webapp consumption

No SpacetimeDB table changes needed — handoffs live in message ciphertext.

### Part C — CLI Convenience Flags (non-breaking)

```bash
# New: --handoff emits envelope with auto-generated IDs
masumi-agent-messenger thread reply 12345 "Task done" --handoff --handoff-state completed

# New: --handoff-file reads envelope JSON from file
masumi-agent-messenger thread reply 12345 --handoff-file ./handoff.json
```

Both map to existing `--content-type application/json` under the hood, adding no breaking changes.

---

## Roadmap (phased)

| Phase | Deliverable | Priority |
|---|---|---|
| **Phase 0 (this PR)** | Spec + docs + examples | P0 |
| **Phase 1** | Validator library in shared/ | P1 |
| **Phase 2** | CLI `--handoff` convenience flags | P1 |
| **Phase 3** | Webapp handoff timeline view (new `/tasks` page) | P2 |
| **Phase 4** | Built-in task dedup/idempotency cache (SpacetimeDB table) | P2 |

---

## Integration with Existing Agent Messenger Features

| Existing Feature | Handoff Interop |
|---|---|
| Threads | Handoffs are JSON messages inside threads — no new transport layer |
| Content-Type `application/json` | Handoff uses it — no new ciphertext handling |
| Secret Envelopes | Binary encryption already handles envelope encryption — schema is plaintext within |
| `threadSeq` sequencing | Handoff `version` is separate (task-level vs message-level ordering) |
| Approvals gateway | `approvals_needed` array mirrors existing contact request approvals — can trigger interactive prompts |

**Zero breaking changes.** Handoff is purely a **convention** layered atop current message format.

---

## Alternative Considered (and rejected)

**Option:** Add a dedicated `handoff` SpacetimeDB table with foreign key to `message`.

**Rejected because:**
1. Escala complexity — every query needs joins
2. Diffuses authority — agents could modify handoff records separately from message ciphertext
3. Versioning hell — two state machines (message seq + handoff seq) to keep in sync
4. Encryption semantics — ciphertext must remain single source of truth; separating metadata risks leaks

**Chosen approach:** **Immutability within message**. Each handoff is a self-contained, signed, encrypted message. State is reconstructed by replay. Simpler. More secure. Compatible.

---

## Hackathon Angle (ETHPrague 2026)

This feature turns Agent Messenger from "encrypted chat" into **coordination backbone** for agentic apps:

- **Track task state across sessions** — crucial for 24h hackathon builds
- **Human-in-the-loop approval queues** — demo-friendly
- **Replay & audit trail** — every artifact change timestamped + signed
- **Idempotent retry** — prevents duplicate work during debugging

**Demo idea:** Two Hermes subagents (Researcher + Writer) coordinating on a report, handoffs showing task state transitions on a live dashboard.

---

## Call for Feedback

This is a **draft proposal**. Seeking:
1. Schema review — is the right metadata captured?
2. Priority discussion — should this be P0 for ETHPrague?
3. Implementation ownership — community contributions welcome

**Open questions:**
- Should `changed_artifacts` include full diffs or just hashes?
- Should `verification_receipt` be structured (test results) or free-text?
- Can `approvals_needed` integrate with existing Contact Request approvals?
- Cross-thread task_id scope — global or per-inbox?

---

## Related Work

- **A2A (Google, 2025):** Task delegation + state cards. HandoffEnvelope parallels `TaskStatus` + `Artifact`
- **MCP (Anthropic, 2024):** Tool access only — no coordination layer
- **ACP (IBM):** Bearer-token auth focus, not multi-agent state
- **OpenFused:** File-based messaging, no structured handoff

Agent Messenger's sweet spot: **encrypted + typed coordination layer** that sits on top of A2A/MCP.

---

*Draft PR — comments welcome before full implementation*
