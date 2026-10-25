import type { Tx } from "@/lib/api";
import { day, label, money } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export function TxRow({ tx, onClick }: { tx: Tx; onClick?: () => void }) {
  const inflow = Number(tx.amount) > 0;
  const title = tx.payee || tx.description || label(tx.category);
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!onClick}
      className="flex w-full items-center gap-3 rounded-lg px-2 py-2.5 text-left transition-colors enabled:hover:bg-accent"
    >
      <div className="w-12 shrink-0 text-xs text-muted-foreground tabular-nums">{day(tx.date)}</div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{title}</div>
        <div className="flex items-center gap-1.5 truncate text-xs text-muted-foreground">
          {tx.review ? (
            <Badge variant="outline" className="h-4 border-status-warning px-1 text-[10px]">
              review
            </Badge>
          ) : null}
          <span className="truncate">
            {label(tx.category, true)} · {label(tx.account)}
          </span>
        </div>
      </div>
      <div
        className={cn(
          "shrink-0 text-sm font-medium tabular-nums",
          inflow ? "text-money-in" : "text-money-out",
        )}
      >
        {inflow ? "+" : ""}
        {money(tx.amount, tx.currency)}
      </div>
    </button>
  );
}
