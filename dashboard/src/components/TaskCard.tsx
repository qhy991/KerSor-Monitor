import type { Task } from '../types';
import { NudgeButton } from './NudgeButton';
export function TaskCard({ t }: { t: Task }) {
  const color = t.state === 'DONE' ? '#2d6' : t.state === 'STUCK' ? '#e33' : '#69c';
  return (
    <div style={{ border: `2px solid ${color}`, borderRadius: 8, padding: 12, margin: 6, width: 240 }}>
      <b>{t.id}</b> <span style={{ color }}>{t.state}</span>
      <div>speedup: {t.speedup ?? '—'} · rounds: {t.rounds} · candidates: {t.candidates}</div>
      <div style={{ opacity: 0.6, fontSize: 12 }}>{t.runtime}</div>
      {t.state === 'STUCK' || t.state === 'RUNNING' ? <NudgeButton tid={t.id} /> : null}
    </div>
  );
}
