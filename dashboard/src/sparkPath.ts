// Inline-SVG sparkline helper kept separate from the React component so it can
// be tested independently without breaking Fast Refresh component boundaries.
export function sparkPath(values: number[], w = 120, h = 26, pad = 2): string {
  const nums = values.filter((value) => typeof value === 'number' && isFinite(value));
  if (nums.length < 2) return '';
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const span = max - min || 1;
  const count = values.length;
  return values
    .map((value, index) => {
      const x = pad + (index / (count - 1)) * (w - 2 * pad);
      const finiteValue = typeof value === 'number' && isFinite(value) ? value : min;
      const y = h - pad - ((finiteValue - min) / span) * (h - 2 * pad);
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}
