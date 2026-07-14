import { useEffect, useRef, useState } from 'react';
import type { Task } from '../types';
import { listTasks, subscribeProject, deleteTask } from '../api';
import { TaskCard } from './TaskCard';
import { CampaignBar } from './CampaignBar';

export function TaskGrid({ pid, reloadKey }: { pid: string; reloadKey: number }) {
  const [tasks, setTasks] = useState<Record<string, Task>>({});
  const [hostFilter, setHostFilter] = useState('');
  // When each task last produced an update (client clock) — drives "Xs ago" + staleness.
  const lastSeen = useRef<Record<string, number>>({});
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let closed = false;
    lastSeen.current = {};
    const stamp = (id: string) => {
      lastSeen.current[id] = Date.now();
    };
    // One SSE stream for the whole project; merge each task update by id.
    const es = subscribeProject(pid, (live) => {
      stamp(live.id);
      setTasks((prev) => ({ ...prev, [live.id]: { ...prev[live.id], ...live } }));
    });
    listTasks(pid).then((ts) => {
      if (closed) return;
      ts.forEach((t) => stamp(t.id));
      setTasks(Object.fromEntries(ts.map((t) => [t.id, t])));
    });
    return () => {
      closed = true;
      es.close();
    };
  }, [pid, reloadKey]);

  // Tick so "Xs ago" and the stale flag advance between SSE updates.
  useEffect(() => {
    const iv = setInterval(() => setNow(Date.now()), 10000);
    return () => clearInterval(iv);
  }, []);

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
  const hostsInView = Array.from(new Set(all.map((t) => t.target_host || 'local'))).sort();
  const list = hostFilter ? all.filter((t) => (t.target_host || 'local') === hostFilter) : all;

  return (
    <>
      <CampaignBar tasks={all} />
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
        <div className="grid">
          {list.map((t) => (
            <TaskCard key={t.id} t={t} onDelete={removeTask} lastSeen={lastSeen.current[t.id]} now={now} />
          ))}
        </div>
      )}
    </>
  );
}
