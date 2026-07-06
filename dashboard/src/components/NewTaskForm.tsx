import { useState } from 'react';
import { ensureProjectAndCreateTasks } from '../api';

export function NewTaskForm({ pid, onSubmitted }: { pid: string; onSubmitted: () => void }) {
  const [spec, setSpec] = useState(
    'Write a pytest test_doubler.py that checks doubler(2) == 4.',
  );
  const [runtime, setRuntime] = useState('shell');
  const [evaluator, setEvaluator] = useState('pytest');
  const [host, setHost] = useState('local');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!spec.trim() || !pid.trim()) return;
    setBusy(true);
    setErr(null);
    const id = `t-${Math.random().toString(36).slice(2, 8)}`;
    try {
      await ensureProjectAndCreateTasks(pid, [
        {
          id,
          name: id,
          spec: spec.trim(),
          runtime,
          evaluator: evaluator || null,
          target_host: host === 'local' ? null : host,
        },
      ]);
      onSubmitted();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : 'submit failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="newtask" onSubmit={submit}>
      <div className="newtask-row">
        <label className="field">
          runtime
          <select className="select" value={runtime} onChange={(e) => setRuntime(e.target.value)}>
            <option value="shell">shell (lifecycle demo)</option>
            <option value="claude_tmux">claude_tmux (real agent)</option>
          </select>
        </label>
        <label className="field">
          evaluator
          <select className="select" value={evaluator} onChange={(e) => setEvaluator(e.target.value)}>
            <option value="">none</option>
            <option value="pytest">pytest</option>
          </select>
        </label>
        <label className="field">
          target host
          <select className="select" value={host} onChange={(e) => setHost(e.target.value)}>
            <option value="local">local</option>
            <option value="verda">verda (B200, ssh)</option>
          </select>
        </label>
      </div>
      <textarea
        className="textarea"
        value={spec}
        onChange={(e) => setSpec(e.target.value)}
        rows={2}
        placeholder="Describe the task — the worker's spec / prompt…"
      />
      <div className="newtask-row newtask-foot">
        <span className="hint">
          submits to project <b>{pid || '—'}</b> on <b>{host}</b>
          {runtime === 'claude_tmux' ? '' : ' · shell runs `true` (no real work)'}
        </span>
        <button className="btn" disabled={busy || !spec.trim() || !pid.trim()}>
          {busy ? 'Submitting…' : '＋ Submit task'}
        </button>
      </div>
      {err && <div className="err">{err}</div>}
    </form>
  );
}
