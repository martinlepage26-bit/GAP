import { Layers, Play, Pause, AlertCircle } from "lucide-react";
import type { QueueStatus } from "@/lib/types";

interface QueuePanelProps {
  queueStatus: QueueStatus[];
  loading: boolean;
}

const statusConfig: Record<string, { color: string; icon: typeof Play }> = {
  queued: { color: "text-amber-400", icon: Pause },
  running: { color: "text-cyan-400", icon: Play },
  retry: { color: "text-orange-400", icon: AlertCircle },
  failed: { color: "text-red-400", icon: AlertCircle },
  done: { color: "text-emerald-400", icon: Play },
};

export function QueuePanel({ queueStatus, loading }: QueuePanelProps) {
  if (loading && queueStatus.length === 0) {
    return (
      <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
        <div className="flex items-center gap-2 mb-4">
          <Layers className="w-4 h-4 text-cyan-400" />
          <h2 className="text-lg font-semibold">Extraction Job Queue</h2>
        </div>
        <div className="text-slate-500 text-sm">Loading queue status...</div>
      </div>
    );
  }

  const grouped: Record<string, QueueStatus[]> = {};
  for (const item of queueStatus) {
    if (!grouped[item.job_type]) grouped[item.job_type] = [];
    grouped[item.job_type].push(item);
  }

  if (queueStatus.length === 0) {
    return (
      <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
        <div className="flex items-center gap-2 mb-4">
          <Layers className="w-4 h-4 text-cyan-400" />
          <h2 className="text-lg font-semibold">Extraction Job Queue</h2>
        </div>
        <div className="text-slate-500 text-sm">
          No jobs in the queue. Enqueue extraction jobs from the Python worker to see them here.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
        <div className="flex items-center gap-2 mb-4">
          <Layers className="w-4 h-4 text-cyan-400" />
          <h2 className="text-lg font-semibold">Extraction Job Queue</h2>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Object.entries(grouped).map(([jobType, statuses]) => (
            <div
              key={jobType}
              className="bg-slate-950 rounded-lg border border-slate-800 p-4"
            >
              <h3 className="text-sm font-medium text-slate-300 mb-3 capitalize">
                {jobType.replace(/_/g, " ")}
              </h3>
              <div className="space-y-2">
                {statuses.map((s) => {
                  const cfg = statusConfig[s.status] || statusConfig.queued;
                  const Icon = cfg.icon;
                  return (
                    <div
                      key={s.status}
                      className="flex items-center justify-between"
                    >
                      <div className="flex items-center gap-2">
                        <Icon className={`w-3.5 h-3.5 ${cfg.color}`} />
                        <span className="text-sm text-slate-400 capitalize">{s.status}</span>
                      </div>
                      <span className={`text-sm font-mono font-semibold ${cfg.color}`}>
                        {s.count}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
        <h3 className="text-sm font-medium text-slate-300 mb-3">Queue Worker Pattern</h3>
        <p className="text-sm text-slate-400 leading-relaxed">
          Workers claim jobs atomically using Postgres <code className="text-cyan-400 bg-slate-800 px-1.5 py-0.5 rounded text-xs">SKIP LOCKED</code>.
          This allows multiple concurrent workers to pull from the same queue without conflicts.
          Jobs use idempotency keys to prevent duplicate processing on retry, and exponential
          backoff is applied via the <code className="text-cyan-400 bg-slate-800 px-1.5 py-0.5 rounded text-xs">available_at</code> column.
        </p>
      </div>
    </div>
  );
}
