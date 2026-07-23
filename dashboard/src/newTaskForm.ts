import type { TaskMetadata } from './types';

export const SHELL_RUNTIME = 'shell';

export function isShellRuntime(runtime: string): boolean {
  return runtime === SHELL_RUNTIME;
}

export function buildTaskMetadata(
  runtime: string,
  effort: string,
  command: string,
): TaskMetadata | undefined {
  if (isShellRuntime(runtime)) {
    return { command: command.trim() };
  }
  return effort ? { effort } : undefined;
}

export function taskTargetHost(runtime: string, host: string): string | null {
  return isShellRuntime(runtime) || host === 'local' ? null : host;
}

export function canSubmitTask(
  pid: string,
  spec: string,
  runtime: string,
  command: string,
): boolean {
  return Boolean(
    pid.trim()
      && spec.trim()
      && (!isShellRuntime(runtime) || command.trim()),
  );
}

export function canSaveTaskTemplate(runtime: string, spec: string): boolean {
  return !isShellRuntime(runtime) && Boolean(spec.trim());
}
