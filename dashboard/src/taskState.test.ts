import { describe, expect, it } from 'vitest';
import type { TaskRecord, TaskState } from './types';
import { mergeKnownTask, reconcileTaskSnapshot } from './taskState';

const record = (id: string, state: TaskState = 'QUEUED'): TaskRecord => ({
  id,
  name: id,
  state,
  runtime: 'claude_tmux',
});

describe('task snapshot reconciliation', () => {
  it('applies buffered SSE after REST so newer telemetry wins', () => {
    const tasks = reconcileTaskSnapshot(
      [record('t1')],
      [{ id: 't1', state: 'RUNNING', speedup: 1.5, rounds: 2 }],
    );

    expect(tasks.t1).toMatchObject({
      state: 'RUNNING',
      speedup: 1.5,
      rounds: 2,
      runtime: 'claude_tmux',
    });
  });

  it('uses REST membership to reject stale or unknown SSE replays', () => {
    const tasks = reconcileTaskSnapshot(
      [record('kept')],
      [
        { id: 'deleted', state: 'RUNNING', speedup: 9 },
        { id: 'kept', state: 'RUNNING' },
      ],
      new Set(['deleted']),
    );

    expect(tasks.deleted).toBeUndefined();
    expect(tasks.kept.state).toBe('RUNNING');
  });

  it('does not create a task from a live delta alone', () => {
    const tasks = reconcileTaskSnapshot([], [{ id: 'stale', state: 'RUNNING' }]);
    const merged = mergeKnownTask(tasks, { id: 'stale', state: 'DONE' });

    expect(merged).toBe(tasks);
    expect(merged).toEqual({});
  });

  it('removes a known task when an SSE tombstone arrives', () => {
    const tasks = reconcileTaskSnapshot([record('t1')]);
    const merged = mergeKnownTask(tasks, { id: 't1', deleted: true });

    expect(merged).toEqual({});
  });
});
