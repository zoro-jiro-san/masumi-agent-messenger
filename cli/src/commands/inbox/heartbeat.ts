import { Command } from 'commander';
import type { GlobalOptions } from '../../services/command-runtime';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);

type HeartbeatOptions = GlobalOptions & {
  agent?: string;
  interval?: string;
  approve?: boolean;
};

function parseInterval(value: string | undefined): number {
  const seconds = value ? parseInt(value, 10) : 10;
  if (Number.isNaN(seconds) || seconds < 1) {
    throw new Error('Interval must be a positive integer (seconds)');
  }
  return seconds * 1000;
}

// Two-Strike retry: attempt twice, then yield error in result
async function runWithRetry(
  fn: () => Promise<string>,
  label: string
): Promise<{ ok: boolean; data?: string; error?: string }> {
  try {
    return { ok: true, data: await fn() };
  } catch (err) {
    try {
      await new Promise((r) => setTimeout(r, 1000));
      return { ok: true, data: await fn() };
    } catch (retryErr) {
      const msg = retryErr instanceof Error ? retryErr.message : String(retryErr);
      return { ok: false, error: `${label} failed: ${msg}` };
    }
  }
}

function safeJsonParse(str: string): unknown {
  try {
    return JSON.parse(str);
  } catch {
    return null;
  }
}

function extractString(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object' && 'toString' in value) {
    return value.toString();
  }
  return String(value);
}

export function registerHeartbeatCommand(program: Command): void {
  program
    .command('heartbeat')
    .description('Poll inbox for unread messages and pending approvals (daemon mode)')
    .option('--agent <slug>', 'Agent slug to poll (required)')
    .option('--interval <seconds>', 'Polling interval in seconds', '10')
    .option('--approve', 'Auto-approve incoming approval requests', false)
    .option('--profile <name>', 'CLI profile', 'default')
    .option('--verbose', 'Show detailed progress output', false)
    .action(async (opts: HeartbeatOptions) => {
      const agentSlug = opts.agent;
      if (!agentSlug) {
        throw new Error('--agent is required');
      }
      const intervalMs = parseInterval(opts.interval);
      const approve = opts.approve || false;
      const verbose = opts.verbose || false;

      let cycle = 0;
      const baseArgs = ['--json', '--profile', opts.profile, '--agent', agentSlug];

      async function pollCycle(): Promise<void> {
        cycle++;
        const start = Date.now();
        let unreadCount = 0;
        let unreadPreview: unknown[] = [];
        let approvalCount = 0;
        let autoApproved = 0;
        let error: string | null = null;

        // 1. Unread messages (with Two-Strike retry)
        const unreadResult = await runWithRetry(
          async () => (await execFileAsync('masumi-agent-messenger', ['thread', 'unread', ...baseArgs])).stdout,
          'thread unread'
        );

        if (unreadResult.ok && unreadResult.data) {
          const parsed = safeJsonParse(unreadResult.data);
          if (parsed && typeof parsed === 'object') {
            const msgs = Array.isArray(parsed) ? parsed : (parsed as Record<string, unknown>).messages || (parsed as Record<string, unknown>).data || [];
            unreadCount = Array.isArray(msgs) ? msgs.length : 0;
            if (Array.isArray(msgs)) {
              unreadPreview = msgs.slice(0, 10).map((m: unknown) => {
                const obj = m as Record<string, unknown>;
                return {
                  threadId: extractString(obj.threadId || obj.thread_id),
                  messageId: extractString(obj.messageId || obj.message_id),
                  sender: extractString(obj.sender),
                  preview: extractString(obj.text || obj.body || '').slice(0, 80),
                };
              });
            }
          }
        } else {
          error = unreadResult.error || 'Unknown unread error';
        }

        // 2. Optionally process incoming approvals
        if (!error && approve) {
          try {
            const approvalResult = await runWithRetry(
              async () => (await execFileAsync('masumi-agent-messenger', ['thread', 'approval', 'list', '--incoming', ...baseArgs])).stdout,
              'thread approval list'
            );

            if (approvalResult.ok && approvalResult.data) {
              const parsed = safeJsonParse(approvalResult.data);
              if (parsed && typeof parsed === 'object') {
                const requests = Array.isArray(parsed) ? parsed : (parsed as Record<string, unknown>).requests || (parsed as Record<string, unknown>).data || [];
                if (Array.isArray(requests)) {
                  approvalCount = requests.length;

                  for (const req of requests) {
                    const reqObj = req as Record<string, unknown>;
                    const requestId = extractString(reqObj.id || reqObj.requestId);
                    if (requestId) {
                      try {
                        await execFileAsync('masumi-agent-messenger', ['thread', 'request', 'approve', requestId, ...baseArgs]);
                        autoApproved++;
                      } catch (e) {
                        console.error(`[heartbeat] approve ${requestId} failed: ${e}`);
                      }
                    }
                  }
                }
              }
            }
          } catch (e: unknown) {
            if (verbose) console.error(`[heartbeat] approval processing error: ${e}`);
          }
        }

        // 3. Emit structured result
        const result = {
          cycle,
          timestamp: new Date().toISOString(),
          unread_count: unreadCount,
          unread: unreadPreview,
          approvals_requested: approvalCount,
          approvals_auto_approved: autoApproved,
          error,
        };
        console.log(JSON.stringify(result));

        // 4. Schedule next cycle
        const elapsed = Date.now() - start;
        const sleepMs = Math.max(0, intervalMs - elapsed);
        setTimeout(pollCycle, sleepMs);
      }

      if (verbose) console.error(`[heartbeat] started (agent=${agentSlug}, interval=${intervalMs}ms, approve=${approve})`);
      await pollCycle();
    });
}

