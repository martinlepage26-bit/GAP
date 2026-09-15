import { TrendingUp, Target } from "lucide-react";
import type { GapCluster, GapScore } from "@/lib/types";

interface GapTableProps {
  gaps: GapCluster[];
  loading: boolean;
}

const categoryColors: Record<string, string> = {
  feature_gap: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  price_gap: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  segment_gap: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  geography_language_gap: "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
  quality_gap: "bg-red-500/10 text-red-400 border-red-500/20",
  other: "bg-slate-500/10 text-slate-400 border-slate-500/20",
};

function scoreColor(score: number): string {
  if (score >= 0.8) return "text-emerald-400";
  if (score >= 0.6) return "text-cyan-400";
  if (score >= 0.4) return "text-amber-400";
  return "text-red-400";
}

function scoreBar(score: number): string {
  if (score >= 0.8) return "bg-emerald-500";
  if (score >= 0.6) return "bg-cyan-500";
  if (score >= 0.4) return "bg-amber-500";
  return "bg-red-500";
}

export function GapTable({ gaps, loading }: GapTableProps) {
  if (loading && gaps.length === 0) {
    return (
      <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
        <div className="flex items-center gap-2 mb-4">
          <TrendingUp className="w-4 h-4 text-cyan-400" />
          <h2 className="text-lg font-semibold">Gap Clusters</h2>
        </div>
        <div className="text-slate-500 text-sm">Loading...</div>
      </div>
    );
  }

  if (gaps.length === 0) {
    return (
      <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
        <div className="flex items-center gap-2 mb-4">
          <TrendingUp className="w-4 h-4 text-cyan-400" />
          <h2 className="text-lg font-semibold">Gap Clusters</h2>
        </div>
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <Target className="w-12 h-12 text-slate-700 mb-3" />
          <p className="text-slate-500 text-sm">
            No gap clusters yet. Run the clustering worker to group extracted complaints into ranked gaps.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-slate-900 rounded-xl border border-slate-800 overflow-hidden">
      <div className="flex items-center gap-2 p-5 border-b border-slate-800">
        <TrendingUp className="w-4 h-4 text-cyan-400" />
        <h2 className="text-lg font-semibold">Gap Clusters</h2>
        <span className="ml-auto text-xs text-slate-500">{gaps.length} active</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 text-xs uppercase tracking-wider">
              <th className="text-left px-5 py-3 font-medium">Market</th>
              <th className="text-left px-3 py-3 font-medium">Category</th>
              <th className="text-left px-3 py-3 font-medium">Label</th>
              <th className="text-right px-3 py-3 font-medium">Score</th>
              <th className="text-left px-3 py-3 font-medium">Score Breakdown</th>
              <th className="text-left px-5 py-3 font-medium">Created</th>
            </tr>
          </thead>
          <tbody>
            {gaps.map((gap) => {
              const score = gap.gap_scores;
              return (
                <tr
                  key={gap.id}
                  className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors"
                >
                  <td className="px-5 py-3 text-slate-300 font-mono text-xs">{gap.market_key}</td>
                  <td className="px-3 py-3">
                    <span
                      className={`inline-block px-2 py-0.5 rounded text-xs border ${
                        categoryColors[gap.category] || categoryColors.other
                      }`}
                    >
                      {gap.category.replace(/_/g, " ")}
                    </span>
                  </td>
                  <td className="px-3 py-3 text-slate-300 max-w-xs truncate">
                    {gap.cluster_label || gap.summary || "—"}
                  </td>
                  <td className="px-3 py-3 text-right">
                    {score ? (
                      <span className={`font-mono font-bold ${scoreColor(score.total_score)}`}>
                        {(score.total_score * 100).toFixed(1)}
                      </span>
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    {score ? <ScoreBreakdown score={score} /> : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="px-5 py-3 text-slate-500 text-xs">
                    {new Date(gap.created_at).toLocaleDateString()}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ScoreBreakdown({ score }: { score: GapScore }) {
  const components = [
    { label: "F", value: score.frequency_score, title: "Frequency" },
    { label: "I", value: score.intensity_score, title: "Intensity" },
    { label: "B", value: score.breadth_score, title: "Breadth" },
    { label: "W", value: score.wtp_score, title: "Willingness to Pay" },
    { label: "P", value: score.workaround_score, title: "Workaround" },
    { label: "S", value: score.segment_score, title: "Segment" },
    { label: "Fe", value: score.feasibility_score, title: "Feasibility" },
    { label: "D", value: score.defensibility_score, title: "Defensibility" },
  ];

  return (
    <div className="flex gap-1">
      {components.map((c) => (
        <div
          key={c.label}
          className="group relative w-6 h-6 rounded bg-slate-800 overflow-hidden"
          title={`${c.title}: ${c.value.toFixed(3)}`}
        >
          <div
            className={`absolute bottom-0 left-0 right-0 ${scoreBar(c.value)}`}
            style={{ height: `${c.value * 100}%` }}
          />
          <span className="absolute inset-0 flex items-center justify-center text-[8px] font-bold text-slate-300">
            {c.label}
          </span>
        </div>
      ))}
    </div>
  );
}
