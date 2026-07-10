import { useState } from 'react';
import { createProject } from '../api';

export function NewProjectForm({ onCreated }: { onCreated: (pid: string) => void }) {
  const [id, setId] = useState('');
  const [name, setName] = useState('');
  const [showFeishu, setShowFeishu] = useState(false);
  const [feishuBase, setFeishuBase] = useState('');
  const [feishuTable, setFeishuTable] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!id.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await createProject(id.trim(), name.trim() || id.trim(), {
        feishu_base: feishuBase.trim() || null,
        feishu_table: feishuTable.trim() || null,
      });
      onCreated(id.trim());
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : 'create failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="host-add" onSubmit={submit}>
      <input className="input input-sm" value={id} onChange={(e) => setId(e.target.value)} placeholder="project id" />
      <input className="input input-sm" value={name} onChange={(e) => setName(e.target.value)} placeholder="display name" />
      <button type="button" className="btn btn-mini" onClick={() => setShowFeishu((v) => !v)}>
        {showFeishu ? 'hide feishu' : 'feishu…'}
      </button>
      {showFeishu && (
        <>
          <input className="input input-sm" value={feishuBase} onChange={(e) => setFeishuBase(e.target.value)} placeholder="feishu base token" />
          <input className="input input-sm" value={feishuTable} onChange={(e) => setFeishuTable(e.target.value)} placeholder="feishu table id" />
        </>
      )}
      <button className="btn" disabled={busy || !id.trim()}>＋ Create project</button>
      {err && <span className="err">{err}</span>}
    </form>
  );
}
