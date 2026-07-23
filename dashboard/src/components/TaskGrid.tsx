import { useEffect, useRef, useState } from 'react';
import type { Task, TaskTelemetry } from '../types';
import { deleteTask, isAbortError, listTasks, subscribeProject } from '../api';
import { enrichTask, mergeKnownTask, reconcileTaskSnapshot } from '../taskState';
import { TaskCard } from './TaskCard';
import { CampaignBar } from './CampaignBar';

type StreamState = 'connecting' | 'open' | 'error';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Request failed';
}

export function TaskGrid({ pid, reloadKey }: { pid: string; reloadKey: number }) {
  const [tasks, setTasks] = useState<Record<string, Task>>({});
  const [hostFilter, setHostFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [streamState, setStreamState] = useState<StreamState>('connecting');
  const [streamMessage, setStreamMessage] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  // When each task last produced an update (client clock) — drives "Xs ago" + staleness.
  const lastSeen = useRef<Record<string, number>>({});
  const [now, setNow] = useState(() => Date.now());
  const deletedTaskIds = useRef(new Set<string>());
  const knownTaskIds = useRef(new Set<string>());
  const pendingUnknown = useRef(new Map<string, TaskTelemetry>());

  useEffect(() => {
    let closed = false;
    const controller = new AbortController();
    const deleted = new Set<string>();
    const known = new Set<string>();
    const buffered: TaskTelemetry[] = [];
    const unknown = new Map<string, TaskTelemetry>();
    const resolvingUnknown = new Set<string>();
    let loaded = false;

    deletedTaskIds.current = deleted;
    knownTaskIds.current = known;
    pendingUnknown.current = unknown;
    lastSeen.current = {};
    setTasks({});
    setHostFilter('');
    setLoading(true);
    setLoadError(null);
    setActionError(null);
    setStreamState('connecting');
    setStreamMessage(null);

    const stamp = (id: string) => {
      lastSeen.current[id] = Date.now();
    };

    // A live event for an unknown id may be either a newly-created task or a
    // stale replay for a deleted task. REST membership decides which one it is.
    const confirmUnknownTask = (id: string) => {
      if (resolvingUnknown.has(id)) return;
      resolvingUnknown.add(id);
      void listTasks(pid, { signal: controller.signal })
        .then((records) => {
          if (closed || deleted.has(id)) return;
          const record = records.find((task) => task.id === id);
          const telemetry = unknown.get(id);
          if (!record || !telemetry) return;
          known.add(id);
          stamp(id);
          setTasks((previous) => ({
            ...previous,
            [id]: enrichTask({ ...record }, telemetry),
          }));
          unknown.delete(id);
        })
        .catch((error: unknown) => {
          if (!closed && !isAbortError(error)) {
            setLoadError(`Could not reconcile live task: ${errorMessage(error)}`);
          }
        })
        .finally(() => resolvingUnknown.delete(id));
    };

    const onLive = (live: TaskTelemetry) => {
      if (closed) return;
      if (live.deleted) {
        deleted.add(live.id);
        known.delete(live.id);
        unknown.delete(live.id);
        delete lastSeen.current[live.id];
        setTasks((previous) => mergeKnownTask(previous, live, deleted));
        return;
      }
      if (deleted.has(live.id)) return;
      stamp(live.id);
      if (!loaded) {
        // Keep events that arrive while the REST snapshot is in flight. They
        // are applied after the snapshot so newer telemetry cannot be lost.
        if (buffered.length >= 1000) buffered.shift();
        buffered.push(live);
        return;
      }
      if (!known.has(live.id)) {
        unknown.set(live.id, live);
        confirmUnknownTask(live.id);
        return;
      }
      setTasks((previous) => mergeKnownTask(previous, live, deleted));
    };

    // Subscribe before fetching REST so no updates are missed; onLive buffers
    // until REST establishes authoritative project membership.
    const es = subscribeProject(pid, {
      onEvent: onLive,
      onOpen: () => {
        if (closed) return;
        setStreamState('open');
        setStreamMessage(null);
      },
      onError: (error) => {
        if (closed) return;
        setStreamState('error');
        setStreamMessage(error.message);
      },
    });

    void listTasks(pid, { signal: controller.signal })
      .then((records) => {
        if (closed) return;
        for (const telemetry of buffered) {
          if (telemetry.deleted) deleted.add(telemetry.id);
        }
        const next = reconcileTaskSnapshot(records, buffered, deleted);
        for (const task of Object.values(next)) {
          known.add(task.id);
          stamp(task.id);
        }
        loaded = true;
        setTasks(next);
        setLoading(false);
        setLoadError(null);
      })
      .catch((error: unknown) => {
        if (closed || isAbortError(error)) return;
        setLoading(false);
        setLoadError(errorMessage(error));
      });

    return () => {
      closed = true;
      controller.abort();
      es.close();
    };
  }, [pid, reloadKey, retryKey]);

  // Tick so "Xs ago" and the stale flag advance between SSE updates.
  useEffect(() => {
    const iv = setInterval(() => setNow(Date.now()), 10000);
    return () => clearInterval(iv);
  }, []);

  async function removeTask(tid: string) {
    if (!window.confirm(`Delete task ${tid}? Stops the worker if running and removes the card. Workspace files on the host are kept.`)) return;
    setActionError(null);
    try {
      await deleteTask(tid);
      deletedTaskIds.current.add(tid);
      knownTaskIds.current.delete(tid);
      pendingUnknown.current.delete(tid);
      delete lastSeen.current[tid];
      setTasks((previous) => {
        const next = { ...previous };
        delete next[tid];
        return next;
      });
    } catch (error) {
      setActionError(`Delete failed: ${errorMessage(error)}`);
    }
  }

  const all = Object.values(tasks);
  const hostsInView = Array.from(new Set(all.map((t) => t.target_host || 'local'))).sort();
  const list = hostFilter ? all.filter((t) => (t.target_host || 'local') === hostFilter) : all;

  return (
    <>
      {(loading || loadError || actionError || streamState !== 'open') && (
        <div className="task-sync" role="status" aria-live="polite">
          {loading && <span>Loading tasks…</span>}
          {!loading && streamState === 'connecting' && <span>Connecting live updates…</span>}
          {streamState === 'error' && <span className="warn">{streamMessage}</span>}
          {loadError && <span className="err">{loadError}</span>}
          {actionError && <span className="err">{actionError}</span>}
          {(loadError || streamState === 'error') && (
            <button className="btn btn-mini" onClick={() => setRetryKey((key) => key + 1)}>
              retry
            </button>
          )}
        </div>
      )}
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
      {loading ? null : list.length === 0 ? (
        <div className="empty">
          {loadError
            ? `Tasks for “${pid}” could not be loaded.`
            : `No tasks ${hostFilter ? `on ${hostFilter}` : `in project “${pid}”`} yet — submit one above.`}
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
