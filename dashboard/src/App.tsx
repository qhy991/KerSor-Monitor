import { useState } from 'react';
import { TaskGrid } from './components/TaskGrid';
import { NewTaskForm } from './components/NewTaskForm';

export default function App() {
  const [pid, setPid] = useState('demo');
  const [reloadKey, setReloadKey] = useState(0);
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
      <NewTaskForm pid={pid} onSubmitted={() => setReloadKey((k) => k + 1)} />
      <TaskGrid pid={pid} reloadKey={reloadKey} />
    </div>
  );
}
