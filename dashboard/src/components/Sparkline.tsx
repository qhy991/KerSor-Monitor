import { sparkPath } from '../sparkPath';

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
