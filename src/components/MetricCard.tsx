import type { LucideIcon } from "lucide-react";

interface MetricCardProps {
  label: string;
  value: string;
  subtitle?: string;
  icon: LucideIcon;
  accent: "cyan" | "blue" | "emerald" | "amber" | "red" | "slate";
}

const accentMap: Record<MetricCardProps["accent"], { bg: string; text: string; border: string }> = {
  cyan: { bg: "bg-cyan-500/10", text: "text-cyan-400", border: "border-cyan-500/20" },
  blue: { bg: "bg-blue-500/10", text: "text-blue-400", border: "border-blue-500/20" },
  emerald: { bg: "bg-emerald-500/10", text: "text-emerald-400", border: "border-emerald-500/20" },
  amber: { bg: "bg-amber-500/10", text: "text-amber-400", border: "border-amber-500/20" },
  red: { bg: "bg-red-500/10", text: "text-red-400", border: "border-red-500/20" },
  slate: { bg: "bg-slate-500/10", text: "text-slate-400", border: "border-slate-500/20" },
};

export function MetricCard({ label, value, subtitle, icon: Icon, accent }: MetricCardProps) {
  const colors = accentMap[accent];
  return (
    <div className={`rounded-xl border ${colors.border} bg-slate-900 p-5 transition-transform hover:scale-[1.02] duration-200`}>
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm text-slate-400 mb-1">{label}</p>
          <p className="text-2xl font-bold font-mono tracking-tight">{value}</p>
          {subtitle && <p className="text-xs text-slate-500 mt-1">{subtitle}</p>}
        </div>
        <div className={`w-10 h-10 rounded-lg ${colors.bg} ${colors.text} flex items-center justify-center`}>
          <Icon className="w-5 h-5" />
        </div>
      </div>
    </div>
  );
}
