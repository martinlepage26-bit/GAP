import { DollarSign } from "lucide-react";
import type { ExtractionMetric } from "@/lib/types";

interface CostChartProps {
  metrics: ExtractionMetric[];
  loading: boolean;
}

export function CostChart({ metrics, loading }: CostChartProps) {
  const sorted = [...metrics].sort((a, b) => a.run_date.localeCompare(b.run_date)).slice(-14);
  const maxCost = Math.max(...sorted.map((m) => Number(m.total_cost_usd || 0)), 0.01);

  if (loading && sorted.length === 0) {
    return (
      <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
        <h3 className="text-sm font-medium text-slate-300 mb-4">Daily Cost Trend</h3>
        <div className="h-48 flex items-center justify-center text-slate-500 text-sm">
          Loading...
        </div>
      </div>
    );
  }

  if (sorted.length === 0) {
    return (
      <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
        <div className="flex items-center gap-2 mb-4">
          <DollarSign className="w-4 h-4 text-cyan-400" />
          <h3 className="text-sm font-medium text-slate-300">Daily Cost Trend</h3>
        </div>
        <div className="h-48 flex items-center justify-center text-slate-500 text-sm">
          No extraction data yet. Run the pipeline to see cost trends.
        </div>
      </div>
    );
  }

  return (
    <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
      <div className="flex items-center gap-2 mb-4">
        <DollarSign className="w-4 h-4 text-cyan-400" />
        <h3 className="text-sm font-medium text-slate-300">Daily Cost Trend (last 14 days)</h3>
      </div>
      <div className="flex items-end gap-1.5 h-48">
        {sorted.map((m) => {
          const cost = Number(m.total_cost_usd || 0);
          const heightPct = (cost / maxCost) * 100;
          return (
            <div
              key={`${m.run_date}-${m.model_key}`}
              className="flex-1 flex flex-col items-center justify-end group relative"
            >
              <div
                className="w-full rounded-t bg-gradient-to-t from-cyan-600 to-cyan-400 transition-all duration-300 hover:from-cyan-500 hover:to-cyan-300"
                style={{ height: `${Math.max(heightPct, 2)}%` }}
              >
                <div className="opacity-0 group-hover:opacity-100 transition-opacity absolute -top-8 left-1/2 -translate-x-1/2 bg-slate-800 text-xs text-slate-200 px-2 py-1 rounded whitespace-nowrap pointer-events-none">
                  ${cost.toFixed(4)} · {m.model_key}
                </div>
              </div>
              <span className="text-[10px] text-slate-500 mt-1 -rotate-45 origin-left whitespace-nowrap">
                {m.run_date.slice(5)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
