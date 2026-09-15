import { Gauge } from "lucide-react";
import type { ExtractionMetric } from "@/lib/types";

interface MetricsTableProps {
  metrics: ExtractionMetric[];
  loading: boolean;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function MetricsTable({ metrics, loading }: MetricsTableProps) {
  if (loading && metrics.length === 0) {
    return (
      <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
        <div className="flex items-center gap-2 mb-4">
          <Gauge className="w-4 h-4 text-cyan-400" />
          <h2 className="text-lg font-semibold">Raw Extraction Metrics</h2>
        </div>
        <div className="text-slate-500 text-sm">Loading...</div>
      </div>
    );
  }

  if (metrics.length === 0) {
    return (
      <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
        <div className="flex items-center gap-2 mb-4">
          <Gauge className="w-4 h-4 text-cyan-400" />
          <h2 className="text-lg font-semibold">Raw Extraction Metrics</h2>
        </div>
        <div className="text-slate-500 text-sm py-8 text-center">
          No metrics recorded yet. Run the extraction worker to populate this table.
        </div>
      </div>
    );
  }

  return (
    <div className="bg-slate-900 rounded-xl border border-slate-800 overflow-hidden">
      <div className="flex items-center gap-2 p-5 border-b border-slate-800">
        <Gauge className="w-4 h-4 text-cyan-400" />
        <h2 className="text-lg font-semibold">Raw Extraction Metrics</h2>
        <span className="ml-auto text-xs text-slate-500">{metrics.length} records</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 text-xs uppercase tracking-wider">
              <th className="text-left px-5 py-3 font-medium">Date</th>
              <th className="text-left px-3 py-3 font-medium">Model</th>
              <th className="text-right px-3 py-3 font-medium">Requests</th>
              <th className="text-right px-3 py-3 font-medium">Success</th>
              <th className="text-right px-3 py-3 font-medium">Failed</th>
              <th className="text-right px-3 py-3 font-medium">Valid Fails</th>
              <th className="text-right px-3 py-3 font-medium">Tokens In</th>
              <th className="text-right px-3 py-3 font-medium">Tokens Out</th>
              <th className="text-right px-3 py-3 font-medium">Cost</th>
              <th className="text-right px-3 py-3 font-medium">Rate Limits</th>
              <th className="text-right px-3 py-3 font-medium">Latency</th>
              <th className="text-right px-5 py-3 font-medium">Retries</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((m, i) => (
              <tr
                key={`${m.run_date}-${m.model_key}-${i}`}
                className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors"
              >
                <td className="px-5 py-2.5 text-slate-300 font-mono text-xs">{m.run_date}</td>
                <td className="px-3 py-2.5 text-slate-300 font-mono text-xs">{m.model_key}</td>
                <td className="px-3 py-2.5 text-right text-slate-400 font-mono">{m.total_requests}</td>
                <td className="px-3 py-2.5 text-right text-emerald-400 font-mono">{m.successful_extractions}</td>
                <td className="px-3 py-2.5 text-right text-red-400 font-mono">{m.failed_extractions}</td>
                <td className="px-3 py-2.5 text-right text-amber-400 font-mono">{m.validation_failures}</td>
                <td className="px-3 py-2.5 text-right text-slate-400 font-mono">{formatTokens(m.total_tokens_input)}</td>
                <td className="px-3 py-2.5 text-right text-slate-400 font-mono">{formatTokens(m.total_tokens_output)}</td>
                <td className="px-3 py-2.5 text-right text-cyan-400 font-mono">${Number(m.total_cost_usd).toFixed(4)}</td>
                <td className="px-3 py-2.5 text-right font-mono">
                  <span className={m.rate_limit_hits > 0 ? "text-amber-400" : "text-slate-600"}>
                    {m.rate_limit_hits}
                  </span>
                </td>
                <td className="px-3 py-2.5 text-right text-slate-400 font-mono">
                  {m.avg_latency_ms ? `${m.avg_latency_ms}ms` : "—"}
                </td>
                <td className="px-5 py-2.5 text-right text-slate-400 font-mono">{m.retry_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
