import { useEffect, useState } from 'react';
import type { Task } from '../types';
import { listTasks, subscribe } from '../api';
import { TaskCard } from './TaskCard';

export function TaskGrid({ pid, reloadKey }: { pid: string; reloadKey: number }) {
  const [tasks, setTasks] = useState<Record<string, Task>>({});
  useEffect(() => {
    let closed = false;
    const timers: EventSource[] = [];
    listTasks(pid).then((ts) => {
      if (closed) return;
      setTasks(Object.fromEntries(ts.map((t) => [t.id, t])));
      // Subscribe AFTER the initial fetch resolves (task ids now known), so
      // live SSE updates actually arrive. The closed flag avoids subscribing
      // to a stale pid on fast unmount/re-switch.
      for (const t of ts) {
        timers.push(
          subscribe(t.id, (live) =>
            setTasks((prev) => ({ ...prev, [live.id]: { ...prev[live.id], ...live } })),
          ),
        );
      }
    });
    return () => {
      closed = true;
      timers.forEach((t) => t.close());
    };
  }, [pid, reloadKey]);

  const list = Object.values(tasks);
  return list.length === 0 ? (
    <div className="empty">No tasks in project &ldquo;{pid}&rdquo; yet — submit one above.</div>
  ) : (
    <div className="grid">{list.map((t) => <TaskCard key={t.id} t={t} />)}</div>
  );
}
