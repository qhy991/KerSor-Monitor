import { useState } from 'react';
import { actuate } from '../api';

export function NudgeButton({ tid }: { tid: string }) {
  const [text, setText] = useState('try a different tiling');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function submit() {
    if (!text.trim() || busy) return;
    setBusy(true);
    setMessage(null);
    try {
      await actuate(tid, 'nudge', { text: text.trim() });
      setMessage('sent');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Nudge failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="nudge">
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="steering message"
      />
      <button className="btn" disabled={busy || !text.trim()} onClick={submit}>
        {busy ? 'Sending…' : 'Nudge'}
      </button>
      {message && <span className={message === 'sent' ? 'hint' : 'err'} role="status">{message}</span>}
    </div>
  );
}
