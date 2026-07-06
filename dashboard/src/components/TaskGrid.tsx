import { useEffect, useState } from 'react';
import type { Task } from '../types';
import { listTasks, subscribe } from '../api';
import { TaskCard } from './TaskCard';
export function TaskGrid({ pid }: { pid: string }) {
  const [tasks, setTasks] = useState<Record<string, Task>>({});
  useEffect(() => {
    listTasks(pid).then(ts => setTasks(Object.fromEntries(ts.map(t => [t.id, t]))));
    const timers = Object.keys(tasks).map(id => subscribe(id, t =>
      setTasks(prev => ({ ...prev, [t.id]: { ...prev[t.id], ...t } }))));
    return () => timers.forEach(t => t.close());
  }, [pid]);
  return <div style={{ display: 'flex', flexWrap: 'wrap' }}>{Object.values(tasks).map(t => <TaskCard key={t.id} t={t} />)}</div>;
}
