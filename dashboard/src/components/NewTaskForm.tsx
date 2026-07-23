import { useEffect, useState } from 'react';
import type { Host, Template } from '../types';
import { ensureProjectAndCreateTasks, getTemplates, createTemplate } from '../api';
import {
  buildTaskMetadata,
  canSaveTaskTemplate,
  canSubmitTask,
  isShellRuntime,
  taskTargetHost,
} from '../newTaskForm';

export function NewTaskForm({
  pid,
  hosts,
  onSubmitted,
}: {
  pid: string;
  hosts: Host[];
  onSubmitted: () => void;
}) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [spec, setSpec] = useState('');
  const [runtime, setRuntime] = useState('claude_tmux');
  const [evaluator, setEvaluator] = useState('');
  const [host, setHost] = useState('local');
  const [effort, setEffort] = useState('');
  const [command, setCommand] = useState('');
  const [owner, setOwner] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const shellMode = isShellRuntime(runtime);
  const shellCommandMissing = shellMode && !command.trim();
  const canSubmit = canSubmitTask(pid, spec, runtime, command);
  const canSaveTemplate = canSaveTaskTemplate(runtime, spec);

  useEffect(() => { getTemplates().then(setTemplates).catch(() => {}); }, []);

  function selectTemplate(e: React.ChangeEvent<HTMLSelectElement>) {
    const tid = e.target.value;
    const t = templates.find((tm) => tm.id === tid);
    if (!t) return;
    setSpec(t.spec);
    const nextRuntime = t.runtime || 'claude_tmux';
    setRuntime(nextRuntime);
    setCommand('');
    if (isShellRuntime(nextRuntime)) {
      setHost('local');
      setEffort('');
    } else {
      setEffort(t.effort || '');
    }
    setEvaluator(t.evaluator || '');
  }

  function selectRuntime(nextRuntime: string) {
    setRuntime(nextRuntime);
    setCommand('');
    if (isShellRuntime(nextRuntime)) {
      setHost('local');
      setEffort('');
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setErr(null);
    const id = `t-${Math.random().toString(36).slice(2, 8)}`;
    const metadata = buildTaskMetadata(runtime, effort, command);
    try {
      await ensureProjectAndCreateTasks(pid, [
        { id, name: id, spec: spec.trim(), runtime, evaluator: evaluator || null,
          target_host: taskTargetHost(runtime, host),
          metadata,
        owner: owner || undefined },
      ]);
      onSubmitted();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : 'submit failed');
    } finally {
      setBusy(false);
    }
  }

  async function saveAsTemplate() {
    if (!canSaveTemplate) {
      setErr('Shell templates cannot be saved because templates do not store commands.');
      return;
    }
    const name = prompt('Template name:');
    if (!name) return;
    const slug = name.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '');
    try {
      await createTemplate({ id: slug, name, spec, runtime, effort: effort || '', evaluator: evaluator || null });
      setTemplates(await getTemplates());
    } catch (e3) {
      alert('Save failed: ' + (e3 instanceof Error ? e3.message : ''));
    }
  }

  return (
    <form className="newtask" onSubmit={submit}>
      <div className="newtask-row">
        <label className="field">
          template
          <select className="select select-tmpl" onChange={selectTemplate} defaultValue="">
            <option value="">-- choose or write freeform --</option>
            {templates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.builtin ? '📋 ' : '💾 '}{t.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          target host
          <select
            className="select"
            value={shellMode ? 'local' : host}
            onChange={(e) => setHost(e.target.value)}
            disabled={shellMode}
            aria-label="target host"
          >
            <option value="local">local</option>
            {hosts.map((h) => (
              <option key={h.id} value={h.id}>{h.id}{h.gpu ? ` (${h.gpu})` : ''}</option>
            ))}
          </select>
        </label>
        <button type="button" className="btn btn-ghost" onClick={() => setShowAdvanced(!showAdvanced)}>
          {showAdvanced ? '▴ hide' : '▾ advanced'}
        </button>
      </div>
      <textarea
        className="textarea"
        value={spec}
        onChange={(e) => setSpec(e.target.value)}
        rows={showAdvanced ? 6 : 3}
        placeholder="Write your prompt — the claude worker will read this and execute it…"
      />
      {shellMode && (
        <div className="shell-config">
          <label className="field shell-command-field">
            shell command
            <input
              className="input shell-command-input"
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              placeholder="e.g. pytest -q"
              required
              aria-invalid={shellCommandMissing}
              aria-describedby="shell-command-help"
            />
          </label>
          <span
            id="shell-command-help"
            className={shellCommandMissing ? 'err' : 'hint'}
            role={shellCommandMissing ? 'alert' : undefined}
          >
            {shellCommandMissing
              ? 'Shell runtime requires a command before this task can be submitted.'
              : 'Runs locally; effort is ignored. Shell templates cannot be saved.'}
          </span>
        </div>
      )}
      {showAdvanced && (
        <div className="advanced-row">
          <label className="field">
            runtime
            <select className="select" value={runtime} onChange={(e) => selectRuntime(e.target.value)}>
              <option value="claude_tmux">claude_tmux</option>
              <option value="shell">shell</option>
            </select>
          </label>
          <label className="field">
            effort
            <select
              className="select"
              value={shellMode ? '' : effort}
              onChange={(e) => setEffort(e.target.value)}
              disabled={shellMode}
            >
              <option value="">{shellMode ? 'ignored by shell' : 'default'}</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="xhigh">xhigh</option>
              <option value="max">max</option>
            </select>
          </label>
          <label className="field">
            owner
            <input className="input input-sm" value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="your name" />
          </label>
          <label className="field">
            evaluator
            <select className="select" value={evaluator} onChange={(e) => setEvaluator(e.target.value)}>
              <option value="">none</option>
              <option value="pytest">pytest</option>
            </select>
          </label>
        </div>
      )}
      <div className="newtask-row newtask-foot">
        <span className="hint">
          submits to project <b>{pid || '—'}</b> on <b>{shellMode ? 'local' : host}</b>{owner ? ` · by @${owner}` : ''}
          {!shellMode && effort ? ` · effort=${effort}` : ''}
        </span>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={saveAsTemplate}
            disabled={!canSaveTemplate}
            title={shellMode ? 'Shell templates cannot store commands' : undefined}
          >
            save as template
          </button>
          <button className="btn" disabled={busy || !canSubmit}>
            {busy ? 'Submitting…' : '＋ Submit task'}
          </button>
        </div>
      </div>
      {err && <div className="err">{err}</div>}
    </form>
  );
}
