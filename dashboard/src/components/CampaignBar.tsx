import type { Task } from '../types';

// Per-project roll-up computed live from the task map (no backend aggregate call).
// State counts are generic; geomean speedup shows only if some task reports one.
const STATES = ['QUEUED', 'RUNNING', 'DONE', 'FAILED', 'STUCK', 'PAUSED'] as const;

export function CampaignBar({ tasks }: { tasks: Task[] }) {
  const total = tasks.length;
  const by: Record<string, number> = {};
  for (const t of tasks) by[t.state] = (by[t.state] || 0) + 1;

  const speeds = tasks
    .map((t) => t.speedup)
    .filter((v): v is number => typeof v === 'number' && v > 0);
  const geomean = speeds.length
    ? Math.exp(speeds.reduce((a, v) => a + Math.log(v), 0) / speeds.length)
    : null;

  const finished = (by.DONE || 0) + (by.FAILED || 0);
  const pct = total ? Math.round((finished / total) * 100) : 0;

  if (total === 0) return null;
  return (
    <div className="campaign">
      <div className="campaign-stats">
        <span className="kpi-pill kpi-total">{total} total</span>
        {STATES.map((s) =>
          by[s] ? (
            <span key={s} className={'kpi-pill kpi-' + s.toLowerCase()}>
              {by[s]} {s.toLowerCase()}
            </span>
          ) : null,
        )}
        {geomean !== null && (
          <span className="kpi-pill kpi-metric" title={`geomean over ${speeds.length} task(s) reporting speedup`}>
            geomean {geomean.toFixed(2)}×
          </span>
        )}
      </div>
      <div className="campaign-bar" title={`${finished}/${total} finished`}>
        <div className="campaign-fill" style={{ width: pct + '%' }} />
      </div>
    </div>
  );
}
