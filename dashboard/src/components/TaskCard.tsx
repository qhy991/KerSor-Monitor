import type { Task } from '../types';
import { NudgeButton } from './NudgeButton';

const stateClass = (s: string) => 's-' + s.toLowerCase();

export function TaskCard({ t }: { t: Task }) {
  const effort = t.metadata?.effort;
  return (
    <div className={'card ' + stateClass(t.state)}>
      <div className="card-head">
        <div className="card-head-left">
          <span className="card-id">{t.id}</span>
          {t.target_host && <span className="badge badge-host">{t.target_host}</span>}
          {effort && <span className="badge badge-effort">{effort}</span>}
        </div>
        <span className={'badge ' + stateClass(t.state)}>{t.state}</span>
      </div>
      <div className="card-metrics">
        <span>speedup <b>{t.speedup ?? '—'}</b></span>
        <span>rounds <b>{t.rounds ?? 0}</b></span>
        <span>candidates <b>{t.candidates ?? 0}</b></span>
      </div>
      <div className="card-runtime">{t.runtime}</div>
      {t.session_uuid && (
        <div className="card-session" title={t.session_uuid}>
          session <b>{t.session_uuid.slice(0, 8)}</b>
          {typeof t.tokens === 'number' && t.tokens > 0 ? ` · ${t.tokens.toLocaleString()} tok` : ''}
        </div>
      )}
      {t.last_activity && (
        <div className="card-activity" title={t.last_activity}>
          {t.last_tool ? `${t.last_tool}: ` : ''}
          {t.last_activity}
        </div>
      )}
      {t.pane_tail && (
        <div className="card-pane">
          <pre>{t.pane_tail}</pre>
        </div>
      )}
      {(t.state === 'STUCK' || t.state === 'RUNNING' || t.state === 'PAUSED') && (
        <NudgeButton tid={t.id} />
      )}
    </div>
  );
}
