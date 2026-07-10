import { useEffect, useState } from 'react';
import type { Task } from '../types';
import { listTasks, subscribe, deleteTask } from '../api';
import { TaskCard } from './TaskCard';

export function TaskGrid({ pid, reloadKey }: { pid: string; reloadKey: number }) {
  const [tasks, setTasks] = useState<Record<string, Task>>({});
  const [hostFilter, setHostFilter] = useState('');
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

  async function removeTask(tid: string) {
    if (!window.confirm(`Delete task ${tid}? Stops the worker if running and removes the card. Workspace files on the host are kept.`)) return;
    await deleteTask(tid);
    setTasks((prev) => {
      const next = { ...prev };
      delete next[tid];
      return next;
    });
  }

  const all = Object.values(tasks);
  // "GPU env" = the task's target_host (local when unset). Only show the filter
  // once tasks span more than one host, so single-host projects stay uncluttered.
  const hostsInView = Array.from(new Set(all.map((t) => t.target_host || 'local'))).sort();
  const list = hostFilter ? all.filter((t) => (t.target_host || 'local') === hostFilter) : all;

  return (
    <>
      {hostsInView.length > 1 && (
        <div className="grid-filter">
          <label className="field">
            gpu env
            <select className="select" value={hostFilter} onChange={(e) => setHostFilter(e.target.value)}>
              <option value="">all hosts</option>
              {hostsInView.map((h) => (
                <option key={h} value={h}>{h}</option>
              ))}
            </select>
          </label>
        </div>
      )}
      {list.length === 0 ? (
        <div className="empty">
          No tasks {hostFilter ? `on ${hostFilter}` : `in project “${pid}”`} yet — submit one above.
        </div>
      ) : (
        <div className="grid">{list.map((t) => <TaskCard key={t.id} t={t} onDelete={removeTask} />)}</div>
      )}
    </>
  );
}
