import { useEffect, useState } from 'react';
import { TaskGrid } from './components/TaskGrid';
import { NewTaskForm } from './components/NewTaskForm';
import { NewProjectForm } from './components/NewProjectForm';
import { HardwarePanel } from './components/HardwarePanel';
import { getHosts, getProjects, getSummary } from './api';
import type { Host, Project, Summary } from './types';

export default function App() {
  const [pid, setPid] = useState('demo');
  const [projects, setProjects] = useState<Project[]>([]);
  const [showNewProject, setShowNewProject] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [hosts, setHosts] = useState<Host[]>([]);
  const [summary, setSummary] = useState<Summary>({ total: 0, running: 0, done: 0, stuck: 0, queued: 0, failed: 0, paused: 0 });

  async function loadHosts() {
    try { setHosts(await getHosts()); } catch { /* */ }
  }
  async function loadSummary() {
    try { setSummary(await getSummary()); } catch { /* */ }
  }
  async function loadProjects() {
    try {
      const ps = await getProjects();
      setProjects(ps);
      // Land on a real project if the current id isn't one (e.g. the default
      // 'demo' before any project exists), so the grid isn't stuck on an empty id.
      setPid((cur) => (ps.some((p) => p.id === cur) || ps.length === 0 ? cur : ps[0].id));
    } catch { /* */ }
  }
  useEffect(() => {
    loadProjects();
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
          <select className="select" value={pid} onChange={(e) => setPid(e.target.value)}>
            {!projects.some((p) => p.id === pid) && <option value={pid}>{pid}</option>}
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.name || p.id}</option>
            ))}
          </select>
        </label>
        <button className="btn btn-mini" onClick={() => setShowNewProject((v) => !v)}>
          {showNewProject ? 'cancel' : '＋ New project'}
        </button>
      </div>
      {showNewProject && (
        <NewProjectForm
          onCreated={(newPid) => {
            setShowNewProject(false);
            setPid(newPid);
            loadProjects();
          }}
        />
      )}
      <div className="kpi-bar">
        <span className="kpi-pill kpi-total">{summary.total} total</span>
        <span className="kpi-pill kpi-queued">{summary.queued} queued</span>
        <span className="kpi-pill kpi-running">{summary.running} running</span>
        <span className="kpi-pill kpi-done">{summary.done} done</span>
        {summary.stuck > 0 && <span className="kpi-pill kpi-stuck">{summary.stuck} stuck</span>}
        {summary.failed > 0 && <span className="kpi-pill kpi-failed">{summary.failed} failed</span>}
      </div>
      <HardwarePanel onChange={loadHosts} />
      <NewTaskForm pid={pid} hosts={hosts} onSubmitted={() => { setReloadKey((k) => k + 1); loadSummary(); loadProjects(); }} />
      <TaskGrid pid={pid} reloadKey={reloadKey} />
    </div>
  );
}
