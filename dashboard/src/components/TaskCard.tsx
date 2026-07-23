import { useState } from 'react';
import type { Task } from '../types';
import { NudgeButton } from './NudgeButton';
import { TaskDetail } from './TaskDetail';

const stateClass = (s: string) => 's-' + s.toLowerCase();
const STALE_MS = 3 * 60 * 1000; // running/stuck with no update in 3m → flag

function fmtAge(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

export function TaskCard({
  t,
  onDelete,
  lastSeen,
  now,
}: {
  t: Task;
  onDelete?: (tid: string) => void;
  lastSeen?: number;
  now: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const effort = t.metadata?.effort;
  // Generic metadata chips: whatever the submitter attached (minus effort, shown separately).
  const metaChips = Object.entries(t.metadata || {}).filter(
    ([k, v]) => k !== 'effort' && v != null && v !== '',
  );
  // Optional domain metrics — rendered only when the task actually reports them.
  const hasSpeedup = typeof t.speedup === 'number';
  const hasMetrics = hasSpeedup || (t.rounds ?? 0) > 0 || (t.candidates ?? 0) > 0;

  const ageMs = typeof lastSeen === 'number' ? now - lastSeen : null;
  const stale =
    ageMs !== null &&
    (t.state === 'DISPATCHING' || t.state === 'RUNNING' || t.state === 'STUCK') &&
    ageMs > STALE_MS;
  const activity =
    t.last_activity ||
    (t.state === 'DONE'
      ? 'finished'
      : t.state === 'FAILED'
        ? 'failed'
        : t.state === 'CANCELLED'
          ? 'cancelled'
          : t.state === 'LOST'
            ? 'worker lost'
            : '…');

  return (
    <div className={'card ' + stateClass(t.state)}>
      <div className="card-head">
        <div className="card-head-left">
          <span className="card-id">{t.id}</span>
          {t.target_host && <span className="badge badge-host">{t.target_host}</span>}
          <span className="badge badge-runtime">{t.runtime}</span>
          {t.owner && <span className="badge badge-effort">@{t.owner}</span>}
          {effort && <span className="badge badge-effort">{effort}</span>}
          {metaChips.map(([k, v]) => (
            <span key={k} className="badge badge-meta" title={`${k}=${String(v)}`}>
              {k}:{String(v)}
            </span>
          ))}
        </div>
        <span className={'badge ' + stateClass(t.state)}>{t.state}</span>
      </div>

      {/* generic progress: what the agent is doing + how fresh */}
      <div className="card-activity-row">
        {t.last_tool && <span className="act-tool">{t.last_tool}</span>}
        <span className="act-text">{activity}</span>
        {ageMs !== null && <span className={'act-age' + (stale ? ' stale' : '')}>{fmtAge(ageMs)}</span>}
      </div>

      {/* optional metrics — only if recorded */}
      {hasMetrics && (
        <div className="card-metrics">
          {hasSpeedup && (
            <span>
              speedup <b>{(t.speedup as number).toFixed(2)}×</b>
            </span>
          )}
          {(t.rounds ?? 0) > 0 && (
            <span>
              rounds <b>{t.rounds}</b>
            </span>
          )}
          {(t.candidates ?? 0) > 0 && (
            <span>
              candidates <b>{t.candidates}</b>
            </span>
          )}
        </div>
      )}

      {t.session_uuid && (
        <div className="card-session" title={t.session_uuid}>
          session <b>{t.session_uuid.slice(0, 8)}</b>
          {typeof t.tokens === 'number' && t.tokens > 0 ? ` · ${t.tokens.toLocaleString()} tok` : ''}
        </div>
      )}
      {t.pane_tail && (
        <div className="card-pane"><pre>{t.pane_tail}</pre></div>
      )}

      {(t.state === 'STUCK' || t.state === 'RUNNING' || t.state === 'PAUSED') && <NudgeButton tid={t.id} />}

      <div className="card-actions">
        <button className="btn btn-mini" onClick={() => setExpanded((e) => !e)}>
          {expanded ? '▴ hide progress' : '▾ progress'}
        </button>
        {onDelete && (
          <button className="btn btn-mini card-delete" onClick={() => onDelete(t.id)}>
            Delete
          </button>
        )}
      </div>
      {expanded && <TaskDetail tid={t.id} />}
    </div>
  );
}
