import { describe, expect, it } from 'vitest';
import {
  buildTaskMetadata,
  canSaveTaskTemplate,
  canSubmitTask,
  taskTargetHost,
} from './newTaskForm';

describe('new task form contract', () => {
  it('requires an explicit shell command', () => {
    expect(canSubmitTask('p1', 'run checks', 'shell', '')).toBe(false);
    expect(canSubmitTask('p1', 'run checks', 'shell', '   ')).toBe(false);
    expect(canSubmitTask('p1', 'run checks', 'shell', 'pytest -q')).toBe(true);
    expect(canSubmitTask('p1', 'run checks', 'claude_tmux', '')).toBe(true);
  });

  it('puts only the trimmed command in shell metadata', () => {
    expect(buildTaskMetadata('shell', 'max', '  pytest -q  ')).toEqual({
      command: 'pytest -q',
    });
    expect(buildTaskMetadata('claude_tmux', 'high', 'echo ignored')).toEqual({
      effort: 'high',
    });
  });

  it('forces shell tasks to local and prevents lossy shell templates', () => {
    expect(taskTargetHost('shell', 'gpu-host')).toBeNull();
    expect(taskTargetHost('claude_tmux', 'gpu-host')).toBe('gpu-host');
    expect(canSaveTaskTemplate('shell', 'run checks')).toBe(false);
    expect(canSaveTaskTemplate('claude_tmux', 'run checks')).toBe(true);
  });
});
