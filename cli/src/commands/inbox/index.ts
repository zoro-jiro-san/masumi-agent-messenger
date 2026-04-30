import type { Command } from 'commander';
import { registerHeartbeatCommand } from './heartbeat';

export function registerInboxCommands(program: Command): void {
  registerHeartbeatCommand(program);
}
