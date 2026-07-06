import { useEffect, useState } from 'react';
import type { Task } from '../types';
import { listTasks, subscribe } from '../api';
import { TaskCard } from './TaskCard';
export function TaskGrid({ pid }: { pid: string }) {
  const [tasks, setTasks] = useState<Record<string, Task>>({});
  useEffect(() => {
    let closed = false;
    const timers: EventSource[] = [];
    listTasks(pid).then(ts => {
      if (closed) return;
      setTasks(Object.fromEntries(ts.map(t => [t.id, t])));
      // Subscribe AFTER the initial fetch resolves (task ids now known).
      // The original code captured `tasks` from the closure at first render,
      // when it was {}, so no EventSource ever opened and live updates never
      // arrived. The closed flag avoids subscribing to a stale pid on fast
      // unmount/re-switch.
      for (const t of ts) {
        timers.push(subscribe(t.id, live =>
          setTasks(prev => ({ ...prev, [live.id]: { ...prev[live.id], ...live } }))));
      }
    });
    return () => {
      closed = true;
      timers.forEach(t => t.close());
    };
  }, [pid]);
  return <div style={{ display: 'flex', flexWrap: 'wrap' }}>{Object.values(tasks).map(t => <TaskCard key={t.id} t={t} />)}</div>;
}
