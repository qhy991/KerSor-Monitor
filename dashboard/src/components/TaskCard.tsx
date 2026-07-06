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
      <div className="card-runtime">{t.runtime}</div>
      {(t.state === 'STUCK' || t.state === 'RUNNING' || t.state === 'PAUSED') && (
        <NudgeButton tid={t.id} />
      )}
    </div>
  );
}
