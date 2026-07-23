import { useEffect, useState } from 'react';
import type { Host } from '../types';
import { getHosts, createHost, deleteHost } from '../api';

export function HardwarePanel({ onChange }: { onChange: () => void }) {
  const [hosts, setHosts] = useState<Host[]>([]);
  const [id, setId] = useState('');
  const [sshAlias, setSshAlias] = useState('');
  const [remoteRoot, setRemoteRoot] = useState('/home/qinhaiyan/flotilla-workspaces');
  const [gpu, setGpu] = useState('');
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    try {
      setHosts(await getHosts());
    } catch {
      /* api down */
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!id.trim() || !sshAlias.trim()) return;
    setErr(null);
    try {
      await createHost({
        id: id.trim(),
        ssh_alias: sshAlias.trim(),
        remote_root: remoteRoot,
        gpu: gpu || null,
      });
      setId('');
      setSshAlias('');
      setGpu('');
      await load();
      onChange();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : 'add failed');
    }
  }

  async function remove(hid: string) {
    setErr(null);
    try {
      await deleteHost(hid);
      await load();
      onChange();
    } catch (error) {
      setErr(error instanceof Error ? error.message : 'remove failed');
    }
  }

  return (
    <div className="hardware">
      <div className="hardware-head">Hardware</div>
      <div className="host-list">
        {hosts.length === 0 ? (
          <span className="hint">no hosts configured — tasks run locally</span>
        ) : (
          hosts.map((h) => (
            <div key={h.id} className="host-row">
              <span className="host-id">{h.id}</span>
              <span className="host-meta">
                ssh={h.ssh_alias}
                {h.gpu ? ` · ${h.gpu}` : ''}
              </span>
              <span className="host-root">{h.remote_root}</span>
              <button className="btn btn-mini" onClick={() => remove(h.id)}>
                remove
              </button>
            </div>
          ))
        )}
      </div>
      <form className="host-add" onSubmit={add}>
        <input
          className="input input-sm"
          value={id}
          onChange={(e) => setId(e.target.value)}
          placeholder="id (verda)"
        />
        <input
          className="input input-sm"
          value={sshAlias}
          onChange={(e) => setSshAlias(e.target.value)}
          placeholder="ssh alias"
        />
        <input
          className="input input-sm"
          value={gpu}
          onChange={(e) => setGpu(e.target.value)}
          placeholder="gpu (B200)"
        />
        <input
          className="input input-wide"
          value={remoteRoot}
          onChange={(e) => setRemoteRoot(e.target.value)}
          placeholder="remote workspaces root"
        />
        <button className="btn" disabled={!id.trim() || !sshAlias.trim()}>
          ＋ Add host
        </button>
      </form>
      {err && <div className="err">{err}</div>}
    </div>
  );
}
