import { useState } from 'react';
import { actuate } from '../api';
export function NudgeButton({ tid }: { tid: string }) {
  const [text, setText] = useState('try a different tiling');
  return (
    <div>
      <input value={text} onChange={e => setText(e.target.value)} size={30} />
      <button onClick={() => actuate(tid, 'nudge', { text })}>Nudge</button>
    </div>
  );
}
