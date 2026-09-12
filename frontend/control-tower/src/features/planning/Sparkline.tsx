/** Daily demand as bars plus the forecast rate as a dashed line; inline SVG, no library. */
import type { LineDemand } from "./api";

export function Sparkline({ demand, width = 360, height = 72 }: { demand: LineDemand; width?: number; height?: number }) {
  const days = demand.days;
  if (!days.length) return null;
  const max = Math.max(demand.forecast_daily, ...days.map((d) => d.ordered), 1);
  const barWidth = width / days.length;
  const y = (value: number) => height - (value / max) * (height - 4);
  return (
    <svg
      role="img"
      aria-label={`demand ${days.length} days`}
      viewBox={`0 0 ${width} ${height}`}
      className="h-20 w-full text-primary"
      preserveAspectRatio="none"
    >
      {days.map((d, i) => (
        <rect
          key={d.day}
          x={i * barWidth}
          y={y(d.ordered)}
          width={Math.max(barWidth - 1, 0.5)}
          height={height - y(d.ordered)}
          fill="currentColor"
          opacity={0.45}
        >
          <title>{`${d.day}: ${d.ordered}`}</title>
        </rect>
      ))}
      <line
        x1={0}
        x2={width}
        y1={y(demand.forecast_daily)}
        y2={y(demand.forecast_daily)}
        stroke="oklch(0.55 0.2 27)"
        strokeDasharray="4 3"
        strokeWidth={1.5}
      />
    </svg>
  );
}
