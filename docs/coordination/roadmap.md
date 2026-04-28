# Coordination Layer Roadmap — Agent Messenger

## Vision

Agent Messenger becomes the **coordination backbone** for multi-agent systems by adding typed handoff envelopes on top of encrypted messaging. Agents no longer reconstruct task state from prose — they read structured, versioned handoffs carrying task boundaries, assumptions, changed artifacts, approval gates, and verification receipts.

**Tagline:** *SMS for agents, with receipts.*

---

## Phase 0 — Documentation & Community Proposal (PR #X)

**Status:** Draft PR open — feedback phase

**Deliverables:**
- [x] `docs/coordination/handoff-schema.md` — JSON Schema v0.1 spec
- [x] `docs/coordination/subagent-patterns.md` — Python/Node.js patterns
- [x] `examples/subagent-coordination/` — complete working code
- [x] `SKILL.md` updated with Coordination section
- [x] Draft PR with implementation plan

**Goal:** Gather community + ETHPrague team feedback before code changes to SpacetimeDB module.

**Expected outcome:** Schema finalization (v1.0), priority agreement, contributor volunteers.

---

## Phase 1 — Validator Library (non-breaking)

**Priority:** P1
**Effort:** 1-2 days
**Owner:** Core maintainers or community contributor

### Changes

**New files:**
- `shared/handoff-envelope.ts` — TypeScript types (importable by CLI + webapp)
- `shared/handoff-validator.ts` — runtime validation using Ajv (JSON Schema validator)

### Scope

- No SpacetimeDB schema changes
- No new reducers
- No breaking changes to existing messages
- Adds optional runtime validation for agents that choose to emit/consume handoffs

### API

```typescript
import { validateHandoffEnvelope, type HandoffEnvelope } from '@/shared/handoff-envelope';

const payload = JSON.parse(messageBody);
const result = validateHandoffEnvelope(payload);

if (!result.valid) {
  throw new Error(`Invalid handoff: ${result.errors.join(', ')}`);
}
```

### Completion criteria

- Ajv added to `dependencies` (or devDependencies)
- Schema compiled to Ajv-compatible format
- Unit tests for validator (valid envelope, missing fields, type mismatches)
- CLI example using validator without breaking existing behavior

---

## Phase 2 — CLI Convenience Flags

**Priority:** P1
**Effort:** 2-3 days
**Owner:** CLI maintainers

### User Story

As an agent operator, I want to send typed handoffs without manually constructing JSON.

### Changes

**Update** `cli/src/services/send-message.ts`:
- Detect `--handoff` flag → auto-generate envelope skeleton with UUIDs
- Add `--handoff-file <path>` to load envelope JSON from disk
- Add `--handoff-state <state>` shortcut for common states
- Optionally `--handoff-task-id <uuid>` (auto-gen if omitted)

**Examples:**

```bash
# Auto-generate minimal handoff envelope
masumi-agent-messenger thread reply 12345 "Work done" --handoff --handoff-state completed

# Handoff with rich metadata
masumi-agent-messenger thread reply 12345 --handoff --handoff-state waiting_approval \
  --handoff-task-id task-42 \
  --header "X-Handoff-Approver: sarthi" \
  --header "X-Handoff-Reason: security review"
```

**Behind the scenes:** The CLI builds the envelope object, serializes to JSON, sends as `application/json`.

### Completion criteria

- Flags functional in `thread send` and `thread reply`
- `--handoff` auto-populates required envelope fields (with help text if missing task_id)
- Help docs updated (`--help` shows handoff options)
- Example scripts (`task_worker.py`) updated to demonstrate flag usage

---

## Phase 3 — Webapp Handoff Timeline View

**Priority:** P2 (depends on Phase 1-2 adoption)
**Effort:** 1 week
**Owner:** Frontend team

### Feature

New route: `/tasks` (or `/$slug/tasks`) displaying per-agent task tracker rendered from handoff messages.

**UI:**
- Table of active tasks (latest handoff state)
- Expandable timeline (all handoffs for a task, ordered by `version`)
- Color-coded state badges
- Approval gates flagged with approver identity
- Changed artifacts with links to GitHub/files

### Implementation

- New route component: `webapp/src/routes/tasks.tsx` (or `$slug.tasks.tsx`)
- Query SpacetimeDB for messages filtered by `text.type = 'handoff_envelope'` (requires partial JSON indexing if backend supports; otherwise client-side filter)
- Pagination / infinite scroll for long task histories
- Export to CSV / JSON for audit

**Non-breaking:** Existing routes unchanged; adds optional new view.

---

## Phase 4 — Built-in Idempotency & Replay Protection

**Priority:** P2 (long-term quality)
**Effort:** 3-4 days backend + client changes
**Owner:** SpacetimeDB maintainers

### Problem

Currently duplicate detection is local-agent-only. If two agents run coordinators independently, they may both process the same handoff. Spoofing risk: malicious actor replays old handoff to trick state.

### Solution

Add **message-level idempotency key as secondary index**:

1. Extend `MessageRow` with optional `idempotencyKey` column (string, indexed)
2. Reducer checks `idempotencyKey` uniqueness on insert — rejects duplicates
3. When sending, agents include `Idempotency-Key` header → stored alongside message headers

**Schema change:**

```diff
  messageTable = table({ ... }, {
    ...
    signature: t.string(),
+   idempotencyKey: t.string().optional(),
    replyToMessageId: t.u64().optional(),
    createdAt: t.timestamp(),
  })
```

**Reducer gate:** Before insert, check if `idempotencyKey` already exists on ANY message (thread-global or globally?). Likely **thread-scoped** to allow same key reuse across unrelated threads.

### Benefits

- Network-level duplicate protection (no double-processing)
- Replay attack surface reduced
- Clients can safely retry transient failures

### Drawbacks

- Schema migration needed (SpacetimeDB online upgrade required)
- Additional index storage cost (minimal)

---

## Phase 5 — Approvals as First-Class Handoff Field

**Priority:** P3 (nice-to-have)
**Effort:** 2-3 days

Tie `approvals_needed` array into Masumi's existing approval system:

- When handoff contains `approvals_needed`, automatically create a **Thread Approval Request** entry
- Human approval through webapp/cli clears the gate → automatable callback URL (webhook) signals waiting agent
- Approver identity authenticated via existing Masumi OIDC

This makes handoff-driven workflows **native to the platform UI** rather than agent-local conventions.

---

## Milestone: ETHPrague 2026 Ready (May 1, 2026)

**Minimum Viable Coordination Kit:**

- [x] Handoff schema v1.0 finalized
- [x] Python `task_worker.py` + `coordinator.py` examples
- [x] CLI validator (`shared/handoff-validator.ts`) + basic tests
- [x] Demo: two subagents coordinating a task with approval gate
- [ ] (stretch) Webapp `/tasks` read-only view

**Deliverable:** Hackers can clone the repo, run the examples, and build multi-agent apps with visible, typed coordination from Day 1.

---

## Risk Mitigation

| Risk | Mitigation |
|---|---|
| Schema churn → breaking existing examples | Lock v1.0 before ETHPrague; mark v0.1 as draft |
| Low adoption → wasted dev time | Start with docs only (Phase 0); wait for community signal |
| SpacetimeDB migration complexity | Keep Phase 4 optional; plan for post-launch |
| Coordination layer too heavyweight | Keep envelope optional — agents can still use plain text |
| Idempotency collisions | Use UUIDs + thread-scoped index to minimize conflict probability |

---

## Success Metrics

- **Adoption:** ≥ 3 ETHPrague teams use handoff envelopes in their submissions
- **Engagement:** ≥ 5 GitHub issues/PRs on coordination layer
- **Stability:** Zero breaking changes to core messaging after v1.0 schema lock
- **Demo quality:** Coordinator + task_worker demo runs end-to-end in 5 minutes from `git clone`

---

*Linked from:* `docs/coordination/handoff-schema.md`
