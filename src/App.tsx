import { useEffect, useState, useCallback } from "react";
import {
  Activity,
  DollarSign,
  Zap,
  AlertTriangle,
  TrendingUp,
  Database,
  Layers,
  RefreshCw,
  Gauge,
  Radar,
} from "lucide-react";
import { supabase } from "@/lib/supabase";
import type {
  ExtractionMetric,
  QueueStatus,
  GapCluster,
  DuplicateGroup,
} from "@/lib/types";
import { MetricCard } from "@/components/MetricCard";
import { CostChart } from "@/components/CostChart";
import { QueuePanel } from "@/components/QueuePanel";
import { GapTable } from "@/components/GapTable";
import { DedupPanel } from "@/components/DedupPanel";
import { MetricsTable } from "@/components/MetricsTable";

type Tab = "overview" | "queues" | "gaps" | "dedup" | "metrics";

export default function App() {
  const [metrics, setMetrics] = useState<ExtractionMetric[]>([]);
  const [queueStatus, setQueueStatus] = useState<QueueStatus[]>([]);
  const [gaps, setGaps] = useState<GapCluster[]>([]);
  const [dedupGroups, setDedupGroups] = useState<DuplicateGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<Tab>("overview");
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [metricsRes, queueRes, gapsRes, dedupRes] = await Promise.all([
        supabase
          .from("extraction_metrics")
          .select("*")
          .order("run_date", { ascending: false })
          .limit(30),
        supabase
          .from("extraction_jobs")
          .select("job_type, status")
          .order("job_type"),
        supabase
          .from("gap_clusters")
          .select("*, gap_scores(*)")
          .eq("status", "active")
          .order("created_at", { ascending: false })
          .limit(50),
        supabase
          .from("duplicate_groups")
          .select("*, duplicate_group_members(count)")
          .order("created_at", { ascending: false })
          .limit(20),
      ]);

      if (metricsRes.data) setMetrics(metricsRes.data);

      if (queueRes.data) {
        const counts: Record<string, QueueStatus> = {};
        for (const row of queueRes.data as { job_type: string; status: string }[]) {
          const key = `${row.job_type}:${row.status}`;
          if (counts[key]) {
            counts[key].count += 1;
          } else {
            counts[key] = { job_type: row.job_type, status: row.status, count: 1 };
          }
        }
        setQueueStatus(Object.values(counts));
      }

      if (gapsRes.data) setGaps(gapsRes.data as GapCluster[]);
      if (dedupRes.data) setDedupGroups(dedupRes.data as DuplicateGroup[]);
    } catch {
      // Tables may be empty on first load — that's fine
    } finally {
      setLoading(false);
      setLastRefresh(new Date());
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 15000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const totals = aggregateMetrics(metrics);

  const tabs: { id: Tab; label: string; icon: typeof Activity }[] = [
    { id: "overview", label: "Overview", icon: Activity },
    { id: "queues", label: "Queues", icon: Layers },
    { id: "gaps", label: "Gap Clusters", icon: TrendingUp },
    { id: "dedup", label: "Dedup Groups", icon: Database },
    { id: "metrics", label: "Raw Metrics", icon: Gauge },
  ];

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      {/* Header */}
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center shadow-lg shadow-cyan-500/20">
                <Radar className="w-5 h-5 text-white" />
              </div>
              <div>
                <h1 className="text-lg font-semibold tracking-tight">Gap Discovery Engine</h1>
                <p className="text-xs text-slate-400">Market gap analysis pipeline monitor</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-xs text-slate-500 hidden sm:inline">
                Updated {lastRefresh.toLocaleTimeString()}
              </span>
              <button
                onClick={() => fetchData()}
                className="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 transition-colors text-slate-300"
                title="Refresh"
              >
                <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Tabs */}
      <nav className="border-b border-slate-800 bg-slate-900/40">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex gap-1 overflow-x-auto">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                    activeTab === tab.id
                      ? "border-cyan-500 text-cyan-400"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Icon className="w-4 h-4" />
                  {tab.label}
                </button>
              );
            })}
          </div>
        </div>
      </nav>

      {/* Content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {activeTab === "overview" && (
          <OverviewTab
            totals={totals}
            metrics={metrics}
            queueStatus={queueStatus}
            gaps={gaps}
            loading={loading}
          />
        )}
        {activeTab === "queues" && <QueuePanel queueStatus={queueStatus} loading={loading} />}
        {activeTab === "gaps" && <GapTable gaps={gaps} loading={loading} />}
        {activeTab === "dedup" && <DedupPanel groups={dedupGroups} loading={loading} />}
        {activeTab === "metrics" && <MetricsTable metrics={metrics} loading={loading} />}
      </main>

      <footer className="border-t border-slate-800 py-6">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <p className="text-xs text-slate-500 text-center">
            Gap Discovery Engine — Postgres + pgvector + structured LLM extraction
          </p>
        </div>
      </footer>
    </div>
  );
}

function aggregateMetrics(metrics: ExtractionMetric[]) {
  const totalCost = metrics.reduce((sum, m) => sum + Number(m.total_cost_usd || 0), 0);
  const totalTokensIn = metrics.reduce((sum, m) => sum + (m.total_tokens_input || 0), 0);
  const totalTokensOut = metrics.reduce((sum, m) => sum + (m.total_tokens_output || 0), 0);
  const totalRequests = metrics.reduce((sum, m) => sum + (m.total_requests || 0), 0);
  const totalSuccessful = metrics.reduce((sum, m) => sum + (m.successful_extractions || 0), 0);
  const totalFailed = metrics.reduce((sum, m) => sum + (m.failed_extractions || 0), 0);
  const totalRateLimits = metrics.reduce((sum, m) => sum + (m.rate_limit_hits || 0), 0);
  const totalRetries = metrics.reduce((sum, m) => sum + (m.retry_count || 0), 0);
  const totalValidationFails = metrics.reduce((sum, m) => sum + (m.validation_failures || 0), 0);
  const costPerExtraction = totalSuccessful > 0 ? totalCost / totalSuccessful : 0;
  const validationRate = totalRequests > 0 ? totalValidationFails / totalRequests : 0;

  return {
    totalCost,
    totalTokensIn,
    totalTokensOut,
    totalRequests,
    totalSuccessful,
    totalFailed,
    totalRateLimits,
    totalRetries,
    costPerExtraction,
    validationRate,
  };
}


function OverviewTab({
  totals,
  metrics,
  queueStatus,
  gaps,
  loading,
}: {
  totals: ReturnType<typeof aggregateMetrics>;
  metrics: ExtractionMetric[];
  queueStatus: QueueStatus[];
  gaps: GapCluster[];
  loading: boolean;
}) {
  const queuedJobs = queueStatus.filter((q) => q.status === "queued").reduce((s, q) => s + q.count, 0);
  const runningJobs = queueStatus.filter((q) => q.status === "running").reduce((s, q) => s + q.count, 0);
  const failedJobs = queueStatus.filter((q) => q.status === "failed").reduce((s, q) => s + q.count, 0);

  return (
    <div className="space-y-6">
      {/* Top metric cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          label="Total Cost (30d)"
          value={`$${totals.totalCost.toFixed(4)}`}
          subtitle={`${totals.costPerExtraction.toFixed(6)}/extraction`}
          icon={DollarSign}
          accent="cyan"
        />
        <MetricCard
          label="Tokens Used (30d)"
          value={`${formatNumber(totals.totalTokensIn + totals.totalTokensOut)}`}
          subtitle={`${formatNumber(totals.totalTokensIn)} in / ${formatNumber(totals.totalTokensOut)} out`}
          icon={Zap}
          accent="blue"
        />
        <MetricCard
          label="Extractions (30d)"
          value={formatNumber(totals.totalSuccessful)}
          subtitle={`${totals.totalFailed} failed`}
          icon={Activity}
          accent="emerald"
        />
        <MetricCard
          label="Rate Limit Hits (30d)"
          value={formatNumber(totals.totalRateLimits)}
          subtitle={`${totals.totalRetries} retries`}
          icon={AlertTriangle}
          accent={totals.totalRateLimits > 10 ? "red" : "amber"}
        />
      </div>

      {/* Queue summary + validation */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="bg-slate-900 rounded-xl border border-slate-800 p-5">
          <h3 className="text-sm font-medium text-slate-300 mb-4">Queue Status</h3>
          <div className="space-y-3">
            <QueueStatRow label="Queued" count={queuedJobs} color="text-amber-400" />
            <QueueStatRow label="Running" count={runningJobs} color="text-cyan-400" />
            <QueueStatRow label="Failed" count={failedJobs} color="text-red-400" />
          </div>
        </div>

        <div className="bg-slate-900 rounded-xl border border-slate-800 p-5">
          <h3 className="text-sm font-medium text-slate-300 mb-4">Validation Health</h3>
          <div className="space-y-3">
            <div className="flex justify-between items-center">
              <span className="text-sm text-slate-400">Validation failure rate</span>
              <span
                className={`text-sm font-mono font-semibold ${
                  totals.validationRate > 0.05 ? "text-red-400" : "text-emerald-400"
                }`}
              >
                {(totals.validationRate * 100).toFixed(2)}%
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-sm text-slate-400">Success rate</span>
              <span className="text-sm font-mono font-semibold text-emerald-400">
                {totals.totalRequests > 0
                  ? ((totals.totalSuccessful / totals.totalRequests) * 100).toFixed(1)
                  : "0"}
                %
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-sm text-slate-400">Active gap clusters</span>
              <span className="text-sm font-mono font-semibold text-cyan-400">{gaps.length}</span>
            </div>
          </div>
        </div>

        <div className="bg-slate-900 rounded-xl border border-slate-800 p-5">
          <h3 className="text-sm font-medium text-slate-300 mb-4">Pipeline Health</h3>
          <div className="space-y-3">
            <div className="flex justify-between items-center">
              <span className="text-sm text-slate-400">Avg latency</span>
              <span className="text-sm font-mono font-semibold text-slate-200">
                {metrics.length > 0 && metrics[0].avg_latency_ms
                  ? `${metrics[0].avg_latency_ms}ms`
                  : "—"}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-sm text-slate-400">Models tracked</span>
              <span className="text-sm font-mono font-semibold text-slate-200">
                {new Set(metrics.map((m) => m.model_key)).size}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-sm text-slate-400">Days of data</span>
              <span className="text-sm font-mono font-semibold text-slate-200">{metrics.length}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Cost chart */}
      <CostChart metrics={metrics} loading={loading} />

      {/* Recent gaps preview */}
      <div>
        <h2 className="text-lg font-semibold mb-4">Recent Gap Clusters</h2>
        <GapTable gaps={gaps.slice(0, 5)} loading={loading} />
      </div>
    </div>
  );
}

function QueueStatRow({ label, count, color }: { label: string; count: number; color: string }) {
  return (
    <div className="flex justify-between items-center">
      <span className="text-sm text-slate-400">{label}</span>
      <span className={`text-sm font-mono font-semibold ${color}`}>{count}</span>
    </div>
  );
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}
