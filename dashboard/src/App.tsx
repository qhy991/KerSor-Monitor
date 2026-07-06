import { useState } from 'react';
import { TaskGrid } from './components/TaskGrid';
export default function App() {
  const [pid, setPid] = useState('demo');
  return (
    <div style={{ fontFamily: 'sans-serif', padding: 16 }}>
      <h1>Flotilla</h1>
      <input value={pid} onChange={e => setPid(e.target.value)} placeholder="project id" />
      <TaskGrid pid={pid} />
    </div>
  );
}
