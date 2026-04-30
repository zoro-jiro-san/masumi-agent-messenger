#!/usr/bin/env python3
"""Visual inbox renderer for Masumi unread messages (inbox)."""

import json, sys, os
from datetime import datetime, timezone

C = {
  "B": "\033[1m", "D": "\033[2m", "R": "\033[0m",
  "k": "\033[90m", "r": "\033[91m", "g": "\033[92m", "y": "\033[93m",
  "b": "\033[94m", "m": "\033[95m", "c": "\033[96m", "w": "\033[97m",
}
tt = sys.stdout.isatty()
def c(col): return C[col] if tt else ""

HR  = f"{c('b')}{'─'*62}{c('R')}"
DIV = f"{c('k')}··{c('R')}"


def fmt_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%b %d %H:%M %Z")
    except Exception:
        return iso


def trust_badge(status: str) -> str:
    if status == "trusted":
        return f"{c('g')}✓ trusted{c('R')}"
    return f"{c('y')}? {status}{c('R')}"


def box(lines, title: str = "", border_color="c"):
    width = max(len(l) for l in lines) if lines else 40
    width = max(width, 28)
    bc = c(border_color)
    h, v = "═", "║"
    out = []
    if title:
        pad = width - len(title) - 2
        out.append(f"{bc}{'═'*(pad//2)} {title} {'═'*((pad+1)//2)}{c('R')}")
    else:
        out.append(f"{bc}{h*width}{c('R')}")
    for l in lines:
        out.append(f"{bc}{v} {l.ljust(width-2)} {v}{c('R')}")
    out.append(f"{bc}{h*width}{c('R')}")
    return "\n".join(out)


def render_message(msg, index: int):
    sender  = msg["sender"]
    name   = sender.get("displayName") or sender.get("slug") or "Unknown"
    slug   = sender.get("slug", "")
    trust   = msg.get("trustStatus", "unknown")
    text   = msg.get("text", "").strip()
    thread = msg.get("threadLabel", "")
    tstr   = fmt_time(msg.get("createdAt", ""))

    header = f"#{index+1} {c('B')}{name}{c('R')} (@{slug})"
    if thread:
        header += f"   thread: {c('c')}{thread}{c('R')}"
    header += f"   {c('D')}{tstr}{c('R')}"

    lines = [header, HR]
    if text:
        for i in range(0, len(text), 56):
            lines.append(text[i:i+56])
    else:
        lines.append(f"{c('k')}[no text]{c('R')}")
    lines.append(HR)
    lines.append(DIV.join([trust_badge(trust), f"decrypt: {c('g')}ok{c('R')}"]))
    return box(lines, border_color="c")


def main():
    if sys.stdin.isatty() and len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            data = json.load(f)
    elif not sys.stdin.isatty():
        data = json.load(sys.stdin)
    else:
        print("Pipe JSON output into this script:\n  masumi thread unread --json | python3 inbox_view.py")
        sys.exit(1)

    msgs  = data.get("data", {}).get("messages", [])
    total = data.get("data", {}).get("totalMessages", len(msgs))

    print(f"\n{c('B')}📥  Masumi Inbox — {total} unread message{'s' if total!=1 else ''}{c('R')}\n")
    if not msgs:
        print(f"{c('g')}All caught up!{c('R')}\n")
        return

    for i, m in enumerate(msgs):
        print(render_message(m, i))
        print()

    # stats bar
    counts = {}
    for m in msgs:
        s = m["sender"].get("displayName") or m["sender"].get("slug") or "?"
        counts[s] = counts.get(s, 0) + 1
    stats = " | ".join(f"{c('y')}{n}{c('R')} from {name}" for name, n in counts.items())
    print(f"stats ▸ {stats}\n")


if __name__ == "__main__":
    main()
