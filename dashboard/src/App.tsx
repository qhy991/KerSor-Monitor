import { useState } from 'react';
import { TaskGrid } from './components/TaskGrid';

export default function App() {
  const [pid, setPid] = useState('demo');
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
      <TaskGrid pid={pid} />
    </div>
  );
}
