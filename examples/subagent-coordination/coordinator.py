#!/usr/bin/env python3
"""
Subagent coordination example using typed handoff envelopes.

This subagent monitors its inbox for handoff messages and maintains
a local task tracker to answer: "what work is in progress, what's done,
and what approvals are pending?"

Run: python subagent-coordinator.py
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Configuration ──────────────────────────────────────────────────────────────

AGENT_SLUG = os.getenv("AGENT_SLUG", "hermes-coordinator-bot")
MASUMI_CLI = os.getenv("MASUMI_CLI", "/home/tokisaki/masumi-agent-messenger/cli/dist/bin.js")
NODE_BIN = os.getenv("NODE_BIN", "/home/tokisaki/.local/bin/node")
STATE_FILE = Path.home() / ".cache" / "hermes-handoff-state.json"

# Ensure state directory exists
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


# ── Local state tracker ────────────────────────────────────────────────────────

class TaskTracker:
    """Keeps a local index of task_id → latest handoff for idempotency."""

    def __init__(self, path: Path):
        self.path = path
        self.tasks: dict[str, dict] = {}
        self.processed_handoffs: set[str] = set()
        self._load()

    def _load(self):
        if self.path.exists():
            with open(self.path) as f:
                data = json.load(f)
            self.tasks = data.get("tasks", {})
            self.processed_handoffs = set(data.get("processed_handoffs", []))
        else:
            self.tasks = {}
            self.processed_handoffs = set()

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(
                {"tasks": self.tasks, "processed_handoffs": list(self.processed_handoffs)},
                f,
                indent=2,
            )

    def is_duplicate(self, handoff_id: str) -> bool:
        return handoff_id in self.processed_handoffs

    def record_handoff(self, envelope: dict):
        handoff_id = envelope["handoff_id"]
        task_id = envelope["task_id"]
        self.processed_handoffs.add(handoff_id)
        self.tasks[task_id] = {
            "latest_handoff_id": handoff_id,
            "state": envelope["state"],
            "created_by": envelope["created_by"],
            "created_at": envelope["created_at"],
            "version": envelope["version"],
        }
        self._save()

    def get_pending_approvals(self) -> list[dict]:
        """Return list of task handoffs waiting for human approval."""
        pending = []
        for task_id, meta in self.tasks.items():
            if meta["state"] == "waiting_approval":
                pending.append({"task_id": task_id, **meta})
        return pending


# ── Masumi CLI wrapper ─────────────────────────────────────────────────────────

def run_masumi(args: list[str]) -> dict:
    """Run masumi-agent-messenger CLI and return parsed JSON."""
    cmd = [NODE_BIN, MASUMI_CLI, "--json", "--headless"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] CLI failed: {result.stderr[:200]}", file=sys.stderr)
        return {"ok": False, "error": result.stderr}
    try:
        return json.loads(result.stdout) if result.stdout else {"ok": False}
    except json.JSONDecodeError:
        return {"ok": False, "error": "Invalid JSON from CLI"}


# ── Inbox polling ──────────────────────────────────────────────────────────────

def check_unread_messages() -> list[dict]:
    """Fetch unread messages across all threads."""
    result = run_masumi(["thread", "unread", "--agent", AGENT_SLUG])
    if not result.get("ok"):
        return []
    return result.get("data", {}).get("messages", [])


def get_thread_messages(thread_id: str) -> list[dict]:
    """Fetch full thread history."""
    result = run_masumi(["thread", "show", thread_id, "--agent", AGENT_SLUG])
    if not result.get("ok"):
        return []
    return result.get("data", {}).get("messages", [])


# ── Handoff processor ──────────────────────────────────────────────────────────

def is_handoff_envelope(body: str) -> Optional[dict]:
    """Return parsed envelope if body is valid JSON handoff, else None."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
    if payload.get("type") == "handoff_envelope":
        return payload
    return None


def process_message(msg: dict, tracker: TaskTracker) -> bool:
    """
    Process a single message. Returns True if handoff was newly processed.
    """
    text = msg.get("text", "")
    envelope = is_handoff_envelope(text)
    if not envelope:
        return False

    handoff_id = envelope.get("handoff_id")
    if not handoff_id:
        print(f"[WARN] Handoff missing handoff_id — skipping")
        return False

    if tracker.is_duplicate(handoff_id):
        print(f"[DUP] Ignoring duplicate handoff {handoff_id}")
        return False

    # Validate required fields
    required = ["task_id", "state", "version", "created_by"]
    missing = [k for k in required if k not in envelope]
    if missing:
        print(f"[WARN] Handoff {handoff_id} missing keys: {missing}")
        return False

    # Record in local tracker
    tracker.record_handoff(envelope)

    task_id = envelope["task_id"]
    state = envelope["state"]
    created_by = envelope["created_by"]

    print(f"[HANDOFF] Task {task_id}: state={state} from {created_by} (v{envelope['version']})")

    # Route based on state
    if state == "waiting_approval":
        approvals = envelope.get("approvals_needed", [])
        print(f"  → Pending approvals: {[a['id'] for a in approvals]}")
        # ESCALATE to human (in real deployment, send notification)
        print(f"  🚨 ESCALATE: task {task_id} needs human approval")

    elif state == "completed":
        receipt = envelope.get("verification_receipt", "none")
        print(f"  ✅ Task marked complete — verification: {receipt}")

    elif state == "blocked":
        print(f"  ⚠️  Task blocked — check assumptions: {envelope.get('assumptions', [])}")

    return True


# ── Reporting ──────────────────────────────────────────────────────────────────

def print_task_summary(tracker: TaskTracker):
    """Print one-line summary of all tracked tasks."""
    if not tracker.tasks:
        print("\n[NO TRACKED TASKS]")
        return

    print("\n=== Task Tracker ===")
    for task_id, meta in tracker.tasks.items():
        state_icon = {
            "in_progress": "🟡",
            "waiting_input": "⏳",
            "waiting_approval": "🔴",
            "completed": "✅",
            "blocked": "🛑",
        }.get(meta["state"], "❓")
        print(f"  {state_icon} {task_id}: {meta['state']} (v{meta['version']}, by {meta['created_by']})")


# ── Main loop ──────────────────────────────────────────────────────────────────

def main(poll_interval: int = 30):
    print("🔄 Subagent coordination monitor starting…")
    print(f"  Agent: {AGENT_SLUG}")
    print(f"  Poll interval: {poll_interval}s")
    print("  Press Ctrl+C to stop\n")

    tracker = TaskTracker(STATE_FILE)

    while True:
        try:
            # 1. Check for new unread messages
            unread = check_unread_messages()
            if unread:
                print(f"\n[{datetime.utcnow().isoformat()}] {len(unread)} unread message(s)")

            processed_new = 0
            for msg in unread:
                thread_id = msg["threadId"]
                # Fetch full thread to get handoff envelopes (may span multiple messages)
                thread_msgs = get_thread_messages(thread_id)
                for tm in thread_msgs:
                    if process_message(tm, tracker):
                        processed_new += 1

            # 2. Brief summary every few iterations
            if processed_new > 0 or (int(time.time()) % 180 == 0):
                print_task_summary(tracker)

            # 3. Mark threads read after processing
            for msg in unread:
                run_masumi(["thread", "read", msg["threadId"], "--agent", AGENT_SLUG])

            time.sleep(poll_interval)

        except KeyboardInterrupt:
            print("\n\n👋 Stopping.")
            print_task_summary(tracker)
            break
        except Exception as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            time.sleep(poll_interval)


if __name__ == "__main__":
    main(poll_interval=int(os.getenv("POLL_INTERVAL", "30")))
