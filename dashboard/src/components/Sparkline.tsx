// Inline-SVG sparkline — no charting dependency. sparkPath is a pure function
// (values -> SVG path string) so the mapping is unit-testable on its own.
export function sparkPath(values: number[], w = 120, h = 26, pad = 2): string {
  const nums = values.filter((v) => typeof v === 'number' && isFinite(v));
  if (nums.length < 2) return '';
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const span = max - min || 1;
  const n = values.length;
  return values
    .map((v, i) => {
      const x = pad + (i / (n - 1)) * (w - 2 * pad);
      const val = typeof v === 'number' && isFinite(v) ? v : min;
      const y = h - pad - ((val - min) / span) * (h - 2 * pad);
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}

export function Sparkline({
  values,
  w = 120,
  h = 26,
  color = '#4ade80',
}: {
  values: number[];
  w?: number;
  h?: number;
  color?: string;
}) {
  const d = sparkPath(values, w, h);
  if (!d) return <span className="spark-empty">—</span>;
  return (
    <svg className="spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <path d={d} fill="none" stroke={color} strokeWidth="1.5" />
    </svg>
  );
}
