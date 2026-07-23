import type { Task, TaskRecord, TaskTelemetry } from './types';

export type TaskMap = Record<string, Task>;

export function enrichTask(task: Task, telemetry: TaskTelemetry): Task {
  return {
    ...task,
    ...telemetry,
    id: task.id,
    name: telemetry.name ?? task.name,
    state: telemetry.state ?? task.state,
    runtime: telemetry.runtime ?? task.runtime,
  };
}

export function reconcileTaskSnapshot(
  records: TaskRecord[],
  bufferedTelemetry: TaskTelemetry[] = [],
  deletedTaskIds: ReadonlySet<string> = new Set(),
): TaskMap {
  const tasks: TaskMap = {};
  for (const record of records) {
    if (!deletedTaskIds.has(record.id)) tasks[record.id] = { ...record };
  }
  for (const telemetry of bufferedTelemetry) {
    if (telemetry.deleted) {
      delete tasks[telemetry.id];
      continue;
    }
    const current = tasks[telemetry.id];
    if (current && !deletedTaskIds.has(telemetry.id)) {
      tasks[telemetry.id] = enrichTask(current, telemetry);
    }
  }
  return tasks;
}

export function mergeKnownTask(
  tasks: TaskMap,
  telemetry: TaskTelemetry,
  deletedTaskIds: ReadonlySet<string> = new Set(),
): TaskMap {
  const current = tasks[telemetry.id];
  if (telemetry.deleted || deletedTaskIds.has(telemetry.id)) {
    if (!current) return tasks;
    const next = { ...tasks };
    delete next[telemetry.id];
    return next;
  }
  if (!current) return tasks;
  return { ...tasks, [telemetry.id]: enrichTask(current, telemetry) };
}
