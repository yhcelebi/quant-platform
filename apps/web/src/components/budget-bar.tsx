import { CircleAlert, CircleCheck, OctagonAlert } from "lucide-react";
import type { BudgetLine } from "@/lib/api";
import { label, money, pct } from "@/lib/format";
import { cn } from "@/lib/utils";

const STATUS = {
  ok: { text: "On track", icon: CircleCheck, color: "bg-status-good", ink: "text-status-good" },
  warning: { text: "Close", icon: CircleAlert, color: "bg-status-warning", ink: "text-status-warning" },
  over: { text: "Over", icon: OctagonAlert, color: "bg-status-critical", ink: "text-status-critical" },
  "no budget": { text: "No budget", icon: CircleCheck, color: "bg-muted", ink: "text-muted-foreground" },
} as const;

/** Budget used, as a bar with the status shown by icon + label (never color alone). */
export function BudgetBar({ line }: { line: BudgetLine }) {
  const s = STATUS[line.status];
  const used = Math.min(Number(line.used ?? 0), 1);
  const Icon = s.icon;
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span className="font-medium">{label(line.account, true)}</span>
        <span className="text-muted-foreground tabular-nums">
          {money(line.actual)} <span className="text-xs">of {money(line.budget)}</span>
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-muted" role="presentation">
        <div className={cn("h-full rounded-full transition-all", s.color)} style={{ width: `${used * 100}%` }} />
      </div>
      <div className="flex items-center justify-between text-xs">
        <span className="flex items-center gap-1 text-muted-foreground">
          <Icon className={cn("size-3.5", s.ink)} aria-hidden />
          {s.text} · {pct(line.used)} used
        </span>
        <span className="text-muted-foreground tabular-nums">
          {Number(line.remaining) >= 0 ? `${money(line.remaining)} left` : `${money(line.remaining.replace("-", ""))} over`}
        </span>
      </div>
    </div>
  );
}
