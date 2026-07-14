import { useEffect, useState } from 'react';
import { TaskGrid } from './components/TaskGrid';
import { NewTaskForm } from './components/NewTaskForm';
import { NewProjectForm } from './components/NewProjectForm';
import { HardwarePanel } from './components/HardwarePanel';
import { getHosts, getProjects } from './api';
import type { Host, Project } from './types';

export default function App() {
  const [pid, setPid] = useState('demo');
  const [projects, setProjects] = useState<Project[]>([]);
  const [showNewProject, setShowNewProject] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [hosts, setHosts] = useState<Host[]>([]);

  async function loadHosts() {
    try { setHosts(await getHosts()); } catch { /* */ }
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
      <HardwarePanel onChange={loadHosts} />
      <NewTaskForm pid={pid} hosts={hosts} onSubmitted={() => { setReloadKey((k) => k + 1); loadProjects(); }} />
      {/* Per-project campaign roll-up + progress lives in TaskGrid (computed live from the task map). */}
      <TaskGrid pid={pid} reloadKey={reloadKey} />
    </div>
  );
}
