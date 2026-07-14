import { useEffect, useState } from 'react';
import type { TaskPoint } from '../types';
import { getTaskHistory } from '../api';
import { Sparkline } from './Sparkline';

function shortTs(ts: string): string {
  const m = /T(\d{2}:\d{2}:\d{2})/.exec(ts || '');
  return m ? m[1] : (ts || '').slice(0, 16);
}

// Expanded per-task view: the recorded trajectory. Metric sparklines render only
// when the task actually reported that metric — a generic (e.g. shell) task shows
// just its state/activity timeline.
export function TaskDetail({ tid }: { tid: string }) {
  const [points, setPoints] = useState<TaskPoint[] | null>(null);
  useEffect(() => {
    let alive = true;
    getTaskHistory(tid)
      .then((r) => alive && setPoints(r.points || []))
      .catch(() => alive && setPoints([]));
    return () => {
      alive = false;
    };
  }, [tid]);

  if (points === null) return <div className="detail">loading history…</div>;
  if (points.length === 0) return <div className="detail">no recorded history yet</div>;

  const last = points[points.length - 1];
  const speedups = points.map((p) => (typeof p.speedup === 'number' ? p.speedup : NaN));
  const rounds = points.map((p) => (typeof p.rounds === 'number' ? p.rounds : NaN));
  const hasSpeedup = speedups.some((v) => !Number.isNaN(v));
  const hasRounds = rounds.some((v) => !Number.isNaN(v) && v > 0);
  const recent = points.slice(-8).reverse();

  return (
    <div className="detail">
      {hasSpeedup && (
        <div className="detail-metric">
          <span className="detail-label">speedup</span>
          <Sparkline values={speedups} />
          <b>{typeof last.speedup === 'number' ? last.speedup.toFixed(2) + '×' : '—'}</b>
        </div>
      )}
      {hasRounds && (
        <div className="detail-metric">
          <span className="detail-label">rounds</span>
          <Sparkline values={rounds} color="#60a5fa" />
          <b>{last.rounds ?? '—'}</b>
        </div>
      )}
      <div className="detail-timeline">
        {recent.map((p, i) => (
          <div key={i} className="tl-row">
            <span className="tl-ts">{shortTs(p.ts)}</span>
            <span className="tl-state">{p.state || '—'}</span>
            {typeof p.speedup === 'number' && <span className="tl-sp">{p.speedup.toFixed(2)}×</span>}
            {p.last_tool && <span className="tl-tool">{p.last_tool}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}
