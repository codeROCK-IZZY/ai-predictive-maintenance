import { Area, CartesianGrid, ComposedChart, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { PredictionSummary, Series, Thresholds } from "../api/types";

const fmtTime = (ts: string) => new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const fmtFull = (ts: string) => new Date(ts).toLocaleString();

export function SensorChart({ series, label }: { series: Series; label: string }) {
  const data = series.points.map((p) => ({ ts: p.ts, avg: p.avg, range: [p.min, p.max] }));
  return (
    <figure>
      <figcaption className="mb-1 text-xs font-medium text-slate-600">{label} ({series.unit})</figcaption>
      <div className="h-40">
        <ResponsiveContainer>
          <ComposedChart data={data} margin={{ left: -10, right: 8 }}>
            <CartesianGrid stroke="#f1f5f9" />
            <XAxis dataKey="ts" tickFormatter={fmtTime} tick={{ fontSize: 11 }} minTickGap={40} />
            <YAxis tick={{ fontSize: 11 }} domain={["auto", "auto"]} width={50} />
            <Tooltip labelFormatter={(v) => fmtFull(String(v))}
              formatter={(v: number | number[], name) => [Array.isArray(v) ? `${v[0]?.toFixed(2)} – ${v[1]?.toFixed(2)}` : v.toFixed(2),
                name === "range" ? "min–max" : "average"]} />
            <Area dataKey="range" stroke="none" fill="#bae6fd" fillOpacity={0.6} isAnimationActive={false} />
            <Line dataKey="avg" stroke="#0369a1" dot={false} strokeWidth={1.5} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

export function RiskHistoryChart({ history, thresholds }: { history: PredictionSummary[]; thresholds?: Thresholds }) {
  const data = history.map((p) => ({ ts: p.as_of, p: p.failure_probability }));
  return (
    <div className="h-48">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ left: -10, right: 8 }}>
          <CartesianGrid stroke="#f1f5f9" />
          <XAxis dataKey="ts" tickFormatter={fmtTime} tick={{ fontSize: 11 }} minTickGap={40} />
          <YAxis domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} tick={{ fontSize: 11 }} width={45} />
          <Tooltip labelFormatter={(v) => fmtFull(String(v))} formatter={(v: number) => [`${(v * 100).toFixed(1)}%`, "failure probability"]} />
          {thresholds && <>
            <ReferenceLine y={thresholds.medium} stroke="#f59e0b" strokeDasharray="4 4" />
            <ReferenceLine y={thresholds.high} stroke="#f97316" strokeDasharray="4 4" />
            <ReferenceLine y={thresholds.critical} stroke="#dc2626" strokeDasharray="4 4" />
          </>}
          <Line dataKey="p" stroke="#0f172a" dot={data.length < 40} strokeWidth={1.5} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
