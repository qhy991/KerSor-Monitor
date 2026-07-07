import type { Task } from '../types';
import { NudgeButton } from './NudgeButton';

const stateClass = (s: string) => 's-' + s.toLowerCase();

export function TaskCard({ t }: { t: Task }) {
  return (
    <div className={'card ' + stateClass(t.state)}>
      <div className="card-head">
        <span className="card-id">{t.id}</span>
        <span className={'badge ' + stateClass(t.state)}>{t.state}</span>
      </div>
      <div className="card-metrics">
        <span>
          speedup <b>{t.speedup ?? '—'}</b>
        </span>
        <span>
          rounds <b>{t.rounds ?? 0}</b>
        </span>
        <span>
          candidates <b>{t.candidates ?? 0}</b>
        </span>
      </div>
      <div className="card-runtime">
        {t.runtime}
        {t.target_host ? ` · host=${t.target_host}` : ' · local'}
      </div>
      {t.session_uuid && (
        <div className="card-session" title={t.session_uuid}>
          session {t.session_uuid.slice(0, 8)}
          {typeof t.tokens === 'number' && t.tokens > 0 ? ` · ${t.tokens} tok` : ''}
        </div>
      )}
      {t.last_activity && (
        <div className="card-activity" title={t.last_activity}>
          {t.last_tool ? `${t.last_tool}: ` : ''}
          {t.last_activity}
        </div>
      )}
      {(t.state === 'STUCK' || t.state === 'RUNNING' || t.state === 'PAUSED') && (
        <NudgeButton tid={t.id} />
      )}
    </div>
  );
}
