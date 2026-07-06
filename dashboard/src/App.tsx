import { useEffect, useState } from 'react';
import { TaskGrid } from './components/TaskGrid';
import { NewTaskForm } from './components/NewTaskForm';
import { HardwarePanel } from './components/HardwarePanel';
import { getHosts } from './api';
import type { Host } from './types';

export default function App() {
  const [pid, setPid] = useState('demo');
  const [reloadKey, setReloadKey] = useState(0);
  const [hosts, setHosts] = useState<Host[]>([]);

  async function loadHosts() {
    try {
      setHosts(await getHosts());
    } catch {
      /* api down */
    }
  }
  useEffect(() => {
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
          <input
            className="input"
            value={pid}
            onChange={(e) => setPid(e.target.value)}
            placeholder="project id"
          />
        </label>
      </div>
      <HardwarePanel onChange={loadHosts} />
      <NewTaskForm pid={pid} hosts={hosts} onSubmitted={() => setReloadKey((k) => k + 1)} />
      <TaskGrid pid={pid} reloadKey={reloadKey} />
    </div>
  );
}
