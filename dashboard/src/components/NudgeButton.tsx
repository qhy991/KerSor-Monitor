import { useState } from 'react';
import { actuate } from '../api';

export function NudgeButton({ tid }: { tid: string }) {
  const [text, setText] = useState('try a different tiling');
  return (
    <div className="nudge">
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="steering message"
      />
      <button className="btn" onClick={() => actuate(tid, 'nudge', { text })}>
        Nudge
      </button>
    </div>
  );
}
