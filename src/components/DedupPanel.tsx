import { Database, GitMerge } from "lucide-react";
import type { DuplicateGroup } from "@/lib/types";

interface DedupPanelProps {
  groups: DuplicateGroup[];
  loading: boolean;
}

const groupTypeColors: Record<string, string> = {
  exact: "bg-red-500/10 text-red-400 border-red-500/20",
  semantic: "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
  boilerplate: "bg-amber-500/10 text-amber-400 border-amber-500/20",
};

export function DedupPanel({ groups, loading }: DedupPanelProps) {
  if (loading && groups.length === 0) {
    return (
      <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
        <div className="flex items-center gap-2 mb-4">
          <Database className="w-4 h-4 text-cyan-400" />
          <h2 className="text-lg font-semibold">Semantic Dedup Groups</h2>
        </div>
        <div className="text-slate-500 text-sm">Loading...</div>
      </div>
    );
  }

  if (groups.length === 0) {
    return (
      <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
        <div className="flex items-center gap-2 mb-4">
          <Database className="w-4 h-4 text-cyan-400" />
          <h2 className="text-lg font-semibold">Semantic Dedup Groups</h2>
        </div>
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <GitMerge className="w-12 h-12 text-slate-700 mb-3" />
          <p className="text-slate-500 text-sm">
            No duplicate groups yet. Run the semantic dedup pipeline to group similar reviews.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
        <div className="flex items-center gap-2 mb-4">
          <Database className="w-4 h-4 text-cyan-400" />
          <h2 className="text-lg font-semibold">Semantic Dedup Groups</h2>
          <span className="ml-auto text-xs text-slate-500">{groups.length} groups</span>
        </div>
        <div className="space-y-3">
          {groups.map((group) => {
            const memberCount =
              (group as unknown as { duplicate_group_members?: { count: number }[] })
                .duplicate_group_members?.[0]?.count ?? 1;
            return (
              <div
                key={group.id}
                className="flex items-center justify-between bg-slate-950 rounded-lg border border-slate-800 p-4 hover:border-slate-700 transition-colors"
              >
                <div className="flex items-center gap-4">
                  <span
                    className={`inline-block px-2 py-0.5 rounded text-xs border ${
                      groupTypeColors[group.group_type] || groupTypeColors.semantic
                    }`}
                  >
                    {group.group_type}
                  </span>
                  <div>
                    <p className="text-sm text-slate-300 font-mono">Group #{group.id}</p>
                    <p className="text-xs text-slate-500">
                      Canonical review:{" "}
                      {group.canonical_review_id ? `#${group.canonical_review_id}` : "—"}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-6">
                  <div className="text-right">
                    <p className="text-xs text-slate-500">Members</p>
                    <p className="text-sm font-mono font-semibold text-slate-300">{memberCount}</p>
                  </div>
                  <div className="text-right">
                    <p className="text-xs text-slate-500">Confidence</p>
                    <p
                      className={`text-sm font-mono font-semibold ${
                        group.confidence >= 0.98
                          ? "text-red-400"
                          : group.confidence >= 0.93
                            ? "text-amber-400"
                            : "text-cyan-400"
                      }`}
                    >
                      {(group.confidence * 100).toFixed(1)}%
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-xs text-slate-500">Created</p>
                    <p className="text-xs text-slate-400">
                      {new Date(group.created_at).toLocaleDateString()}
                    </p>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
        <h3 className="text-sm font-medium text-slate-300 mb-3">Dedup Pipeline Stages</h3>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          {[
            { stage: "1. Exact URL", desc: "Same source + external ID", color: "border-red-500/20" },
            { stage: "2. Text Hash", desc: "SHA-256 of normalized text", color: "border-amber-500/20" },
            { stage: "3. Thread-aware", desc: "Quote chains & mirrors", color: "border-cyan-500/20" },
            { stage: "4. Semantic", desc: "pgvector cosine similarity", color: "border-emerald-500/20" },
          ].map((s) => (
            <div key={s.stage} className={`bg-slate-950 rounded-lg border ${s.color} p-3`}>
              <p className="text-sm font-medium text-slate-300">{s.stage}</p>
              <p className="text-xs text-slate-500 mt-1">{s.desc}</p>
            </div>
          ))}
        </div>
        <div className="mt-4 flex gap-4 text-xs">
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-red-400" /> ≥98% exact/templated
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-amber-400" /> 93-98% near-duplicate
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-cyan-400" /> 88-93% analyst review
          </span>
        </div>
      </div>
    </div>
  );
}
