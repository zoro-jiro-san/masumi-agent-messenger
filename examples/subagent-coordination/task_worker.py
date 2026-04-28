#!/usr/bin/env python3
"""
Subagent task worker that emits typed handoff envelopes.

This demonstrates the *sender* side: creating, updating, and completing tasks
with structured coordination metadata.

Usage examples:
  python task_worker.py start --task-id "proj-42" "Begin auth review"
  python task_worker.py update --task-id "proj-42" --state "waiting_approval"
  python task_worker.py complete --task-id "proj-42"
"""

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Configuration ──────────────────────────────────────────────────────────────

AGENT_SLUG = os.getenv("AGENT_SLUG", "hermes-task-worker")
MASUMI_CLI = os.getenv("MASUMI_CLI", "/home/tokisaki/masumi-agent-messenger/cli/dist/bin.js")
NODE_BIN = os.getenv("NODE_BIN", "/home/tokisaki/.local/bin/node")

# Local task state file (for generating deterministic handoff IDs)
TASK_STATE_DIR = Path.home() / ".cache" / "hermes-tasks"
TASK_STATE_DIR.mkdir(parents=True, exist_ok=True)


# ── Helpers ─────────────────────────────────────────────────────────────────────

def load_task_state(task_id: str) -> dict:
    path = TASK_STATE_DIR / f"{task_id}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"version": 0, "state": None, "last_handoff_id": None}


def save_task_state(task_id: str, state: dict):
    path = TASK_STATE_DIR / f"{task_id}.json"
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def run_masumi(args: list[str]) -> tuple[int, str, str]:
    """Run CLI, return (exit_code, stdout, stderr)."""
    cmd = [NODE_BIN, MASUMI_CLI, "--json", "--headless"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def send_message(thread_id: str, body: str, content_type: str = "application/json") -> bool:
    """Send message to thread. Returns success."""
    exit_code, stdout, stderr = run_masumi([
        "thread", "reply", thread_id, body,
        "--agent", AGENT_SLUG,
        "--content-type", content_type,
    ])
    if exit_code != 0:
        print(f"[ERROR] Send failed: {stderr[:200]}", file=sys.stderr)
        return False
    return True


# ── Handoff envelope builder ────────────────────────────────────────────────────

def build_handoff(
    task_id: str,
    state: str,
    message: str,
    *,
    changed_artifacts: Optional[list] = None,
    assumptions: Optional[list] = None,
    approvals_needed: Optional[list] = None,
    verification_receipt: Optional[str] = None,
    previous_handoff_id: Optional[str] = None,
    next_action: Optional[str] = None,
) -> dict:
    """
    Construct a typed handoff envelope JSON.

    Version increments monotonically per task_id based on local state.
    """
    task_state = load_task_state(task_id)
    version = task_state.get("version", 0) + 1

    handoff_id = str(uuid.uuid4())
    created_by = AGENT_SLUG
    created_at = datetime.utcnow().isoformat() + "Z"

    envelope = {
        "type": "handoff_envelope",
        "handoff_id": handoff_id,
        "task_id": task_id,
        "state": state,
        "version": version,
        "created_by": created_by,
        "created_at": created_at,
        "previous_handoff_id": previous_handoff_id or task_state.get("last_handoff_id"),
        "assumptions": assumptions or [],
        "changed_artifacts": changed_artifacts or [],
        "approvals_needed": approvals_needed or [],
        "verification_receipt": verification_receipt,
        "idempotency_key": str(uuid.uuid4()),
        "next_action_suggestion": next_action,
        "metadata": {},
    }

    # Update local state for next version
    task_state["version"] = version
    task_state["state"] = state
    task_state["last_handoff_id"] = handoff_id
    save_task_state(task_id, task_state)

    return envelope


# ── CLI commands ────────────────────────────────────────────────────────────────

def cmd_start(thread_id: str, task_id: str, message: str):
    """Start a new task — emit initial handoff with state=in_progress."""
    print(f"[START] Task {task_id} in thread {thread_id}")

    envelope = build_handoff(
        task_id=task_id,
        state="in_progress",
        message=message,
        assumptions=[],  # inherited from prompt usually
        next_action="continue_work",
    )

    payload = json.dumps(envelope, indent=2)
    if send_message(thread_id, payload):
        print(f"✅ Sent handoff v{envelope['version']} (id: {envelope['handoff_id']})")
    else:
        sys.exit(1)


def cmd_update(thread_id: str, task_id: str, state: str, message: str = ""):
    """Update an existing task — emit new handoff with incremented version."""
    print(f"[UPDATE] Task {task_id} → {state}")

    # Validate state transition
    task_state = load_task_state(task_id)
    prev_state = task_state.get("state")
    print(f"  previous state: {prev_state or 'unknown'}")

    envelope = build_handoff(
        task_id=task_id,
        state=state,
        message=message,
    )

    payload = json.dumps(envelope, indent=2)
    if send_message(thread_id, payload):
        print(f"✅ Sent handoff v{envelope['version']} (id: {envelope['handoff_id']})")
    else:
        sys.exit(1)


def cmd_complete(thread_id: str, task_id: str, receipt: str = ""):
    """Mark task as completed with optional verification receipt."""
    print(f"[COMPLETE] Task {task_id}")

    envelope = build_handoff(
        task_id=task_id,
        state="completed",
        message=f"Task completed. {receipt}",
        verification_receipt=receipt,
        next_action="archived",
    )

    payload = json.dumps(envelope, indent=2)
    if send_message(thread_id, payload):
        print(f"✅ Sent completion handoff v{envelope['version']}")
    else:
        sys.exit(1)


def cmd_request_approval(
    thread_id: str,
    task_id: str,
    approver: str,
    reason: str,
    gate: bool = True,
):
    """Emit handoff waiting on approval from human/agent."""
    print(f"[APPROVAL] Task {task_id} awaiting {approver}")

    envelope = build_handoff(
        task_id=task_id,
        state="waiting_approval",
        message=f"Approval needed from {approver}: {reason}",
        approvals_needed=[
            {
                "type": "human_review" if approver != "agent" else "agent_ack",
                "id": f"apr-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}",
                "from": approver,
                "reason": reason,
                "gate": gate,
            }
        ],
    )

    payload = json.dumps(envelope, indent=2)
    if send_message(thread_id, payload):
        print(f"✅ Sent approval-request handoff")
    else:
        sys.exit(1)


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Task worker — emit typed handoff envelopes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s start --task-id auth-2026-04 12345 "Begin OAuth2 review"
  %(prog)s update --task-id auth-2026-04 --state waiting_approval 12345
  %(prog)s complete --task-id auth-2026-04 --receipt "tests:pass" 12345
  %(prog)s approval --task-id auth-2026-04 --from sarthi "Fix RLS policy" 12345
        """,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # start
    p_start = sub.add_parser("start", help="Begin a new task")
    p_start.add_argument("--task-id", required=True)
    p_start.add_argument("thread_id")
    p_start.add_argument("message")

    # update
    p_update = sub.add_parser("update", help="Update task state")
    p_update.add_argument("--task-id", required=True)
    p_update.add_argument("--state", required=True, choices=[
        "in_progress", "waiting_input", "waiting_approval", "blocked", "cancelled", "superseded"
    ])
    p_update.add_argument("thread_id")
    p_update.add_argument("message", nargs="?", default="")

    # complete
    p_complete = sub.add_parser("complete", help="Mark task complete")
    p_complete.add_argument("--task-id", required=True)
    p_complete.add_argument("--receipt", default="", help="Verification proof")
    p_complete.add_argument("thread_id")

    # approval
    p_approval = sub.add_parser("approval", help="Request approval")
    p_approval.add_argument("--task-id", required=True)
    p_approval.add_argument("--from", dest="approver", required=True, help="Who must approve")
    p_approval.add_argument("--gate", action="store_true", default=True, help="Is this a blocking gate?")
    p_approval.add_argument("thread_id")
    p_approval.add_argument("reason")

    args = parser.parse_args()

    # Route
    if args.cmd == "start":
        cmd_start(args.thread_id, args.task_id, args.message)
    elif args.cmd == "update":
        cmd_update(args.thread_id, args.task_id, args.state, args.message)
    elif args.cmd == "complete":
        cmd_complete(args.thread_id, args.task_id, args.receipt)
    elif args.cmd == "approval":
        cmd_request_approval(args.thread_id, args.task_id, args.approver, args.reason, args.gate)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
