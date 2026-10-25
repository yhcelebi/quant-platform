"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowRight, Inbox } from "lucide-react";
import { PageHeader } from "@/components/app-shell";
import { BudgetBar } from "@/components/budget-bar";
import { PeriodTabs } from "@/components/period";
import { ErrorNote, Loading, Notes } from "@/components/states";
import { TxRow } from "@/components/tx-row";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { Dashboard } from "@/lib/api";
import { day, label, money, pct } from "@/lib/format";
import { useApi } from "@/lib/use-api";

function Kpi({
  title,
  value,
  hint,
  className,
}: {
  title: string;
  value: string;
  hint?: string;
  className?: string;
}) {
  return (
    <Card size="sm" className={className}>
      <CardHeader>
        <CardDescription>{title}</CardDescription>
        <CardTitle className="text-xl tabular-nums md:text-2xl">{value}</CardTitle>
        {hint ? <div className="text-xs text-muted-foreground">{hint}</div> : null}
      </CardHeader>
    </Card>
  );
}

/** Spending by group: one hue (magnitude), values labeled, largest first. */
function SpendingBars({ rows }: { rows: Dashboard["spending"] }) {
  if (!rows.length) return <p className="text-sm text-muted-foreground">No spending in this period.</p>;
  const max = Math.max(...rows.map((r) => Number(r.amount)));
  return (
    <div className="space-y-2.5">
      {rows.map((r) => (
        <div key={r.group} className="grid grid-cols-[6.5rem_1fr_6.5rem] items-center gap-3 text-sm" title={`${label(r.group)}: ${money(r.amount)}`}>
          <span className="truncate text-muted-foreground">{label(r.group)}</span>
          <div className="h-2.5 rounded-full bg-muted">
            <div className="h-full rounded-full bg-series-1" style={{ width: `${(Number(r.amount) / max) * 100}%` }} />
          </div>
          <span className="text-right tabular-nums">{money(r.amount)}</span>
        </div>
      ))}
    </div>
  );
}

export default function Overview() {
  const [period, setPeriod] = useState("month");
  const { data, error, loading } = useApi<Dashboard>("/api/dashboard", { period });
  return (
    <>
      <PageHeader title="Overview">
        <PeriodTabs value={period} onChange={setPeriod} />
      </PageHeader>
      {error ? <ErrorNote message={error} /> : null}
      {loading && !data ? <Loading rows={4} /> : null}
      {data ? (
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            {day(data.start)} – {day(data.end)}
          </p>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            <Kpi title="Spent" value={money(data.expenses)} />
            <Kpi title="Income" value={money(data.income)} />
            <Kpi
              className="col-span-2 md:col-span-1"
              title="Saved"
              value={money(data.net)}
              hint={data.savings_rate ? `${pct(data.savings_rate)} of income` : undefined}
            />
          </div>
          {data.review > 0 ? (
            <Link
              href="/review"
              className="flex items-center justify-between rounded-xl border border-status-warning/50 bg-status-warning/10 px-4 py-3 text-sm"
            >
              <span className="flex items-center gap-2">
                <Inbox className="size-4" aria-hidden />
                {data.review} transaction{data.review > 1 ? "s" : ""} to categorize
              </span>
              <ArrowRight className="size-4" />
            </Link>
          ) : null}
          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Budgets</CardTitle>
                <CardDescription>Spent against budget for this period</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {data.budgets.length ? (
                  [...data.budgets]
                    .sort((a, b) => Number(b.used ?? 0) - Number(a.used ?? 0))
                    .slice(0, 6)
                    .map((b) => <BudgetBar key={b.account} line={b} />)
                ) : (
                  <p className="text-sm text-muted-foreground">
                    No budgets yet. <Link href="/budgets" className="underline">Set one</Link>.
                  </p>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Where it went</CardTitle>
                <CardDescription>Spending by category group</CardDescription>
              </CardHeader>
              <CardContent>
                <SpendingBars rows={data.spending} />
              </CardContent>
            </Card>
          </div>
          <Card>
            <CardHeader>
              <CardTitle>Recent</CardTitle>
            </CardHeader>
            <CardContent className="-mx-2">
              {data.recent.length ? (
                data.recent.map((t, i) => <TxRow key={t.id ?? i} tx={t} />)
              ) : (
                <p className="px-2 text-sm text-muted-foreground">
                  Nothing yet. <Link href="/add" className="underline">Add an expense</Link> or{" "}
                  <Link href="/import" className="underline">import a statement</Link>.
                </p>
              )}
            </CardContent>
          </Card>
          <Notes notes={data.notes} />
        </div>
      ) : null}
    </>
  );
}
