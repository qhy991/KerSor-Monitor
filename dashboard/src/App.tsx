import { useEffect, useState } from 'react';
import { TaskGrid } from './components/TaskGrid';
import { NewTaskForm } from './components/NewTaskForm';
import { HardwarePanel } from './components/HardwarePanel';
import { getHosts, getSummary } from './api';
import type { Host, Summary } from './types';

export default function App() {
  const [pid, setPid] = useState('demo');
  const [reloadKey, setReloadKey] = useState(0);
  const [hosts, setHosts] = useState<Host[]>([]);
  const [summary, setSummary] = useState<Summary>({ total: 0, running: 0, done: 0, stuck: 0, queued: 0, failed: 0, paused: 0 });

  async function loadHosts() {
    try { setHosts(await getHosts()); } catch { /* */ }
  }
  async function loadSummary() {
    try { setSummary(await getSummary()); } catch { /* */ }
  }
  useEffect(() => {
    loadHosts();
    const interval = setInterval(loadSummary, 3000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="app">
      <div className="header">
        <div className="logo" />
        <h1>Flotilla</h1>
        <div className="spacer" />
        <label className="field">
          project
          <input className="input" value={pid} onChange={(e) => setPid(e.target.value)} placeholder="project id" />
        </label>
      </div>
      <div className="kpi-bar">
        <span className="kpi-pill kpi-total">{summary.total} total</span>
        <span className="kpi-pill kpi-queued">{summary.queued} queued</span>
        <span className="kpi-pill kpi-running">{summary.running} running</span>
        <span className="kpi-pill kpi-done">{summary.done} done</span>
        {summary.stuck > 0 && <span className="kpi-pill kpi-stuck">{summary.stuck} stuck</span>}
        {summary.failed > 0 && <span className="kpi-pill kpi-failed">{summary.failed} failed</span>}
      </div>
      <HardwarePanel onChange={loadHosts} />
      <NewTaskForm pid={pid} hosts={hosts} onSubmitted={() => { setReloadKey((k) => k + 1); loadSummary(); }} />
      <TaskGrid pid={pid} reloadKey={reloadKey} />
    </div>
  );
}
