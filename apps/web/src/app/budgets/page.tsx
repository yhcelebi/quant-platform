"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/app-shell";
import { BudgetBar } from "@/components/budget-bar";
import { NativeSelect } from "@/components/fields";
import { PeriodTabs } from "@/components/period";
import { ErrorNote, Loading, Notes } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, type BudgetLine, type Meta } from "@/lib/api";
import { day, label } from "@/lib/format";
import { useApi } from "@/lib/use-api";

const BUDGET_PERIODS: Record<string, string> = {
  daily: "per day",
  weekly: "per week",
  monthly: "per month",
  quarterly: "per quarter",
  yearly: "per year",
};

export default function BudgetsPage() {
  const [period, setPeriod] = useState("month");
  const meta = useApi<Meta>("/api/meta");
  const report = useApi<{ start: string; end: string; items: BudgetLine[]; notes: string[] }>(
    "/api/budgets",
    { period },
  );
  const [account, setAccount] = useState("Expenses:Food");
  const [every, setEvery] = useState("monthly");
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);

  // groups (Expenses:Food) first, then single categories
  const targets = useMemo(() => {
    const expenses = (meta.data?.categories ?? []).filter((c) => c.startsWith("Expenses:"));
    const groups = [...new Set(expenses.map((c) => c.split(":").slice(0, 2).join(":")))];
    return { groups, leaves: expenses.filter((c) => c.split(":").length > 2) };
  }, [meta.data]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/api/budgets", { account, period: every, amount });
      toast.success(`Budget set: ${label(account, true)}, ${amount} TRY ${every}`);
      setAmount("");
      void report.reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Budgets">
        <PeriodTabs value={period} onChange={setPeriod} />
      </PageHeader>
      <div className="grid gap-4 md:grid-cols-[1fr_20rem]">
        <Card>
          <CardHeader>
            <CardTitle>Spent vs budget</CardTitle>
            <CardDescription>
              {report.data ? `${day(report.data.start)} – ${day(report.data.end)}` : ""} · budgets
              are converted to this period by days
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {report.error ? <ErrorNote message={report.error} /> : null}
            {report.loading && !report.data ? <Loading /> : null}
            {report.data && report.data.items.length === 0 ? (
              <p className="text-sm text-muted-foreground">No budgets yet: add one on the right.</p>
            ) : null}
            {report.data?.items.map((b) => <BudgetBar key={b.account} line={b} />)}
            {report.data ? <Notes notes={report.data.notes} /> : null}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Set a budget</CardTitle>
            <CardDescription>A new amount replaces the old one from today.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={save} className="space-y-3">
              <div className="space-y-1.5">
                <Label>Category or group</Label>
                <NativeSelect value={account} onChange={setAccount} aria-label="Category or group">
                  <optgroup label="Groups (all subcategories)">
                    {targets.groups.map((g) => (
                      <option key={g} value={g}>
                        {label(g)}
                      </option>
                    ))}
                  </optgroup>
                  <optgroup label="Single categories">
                    {targets.leaves.map((c) => (
                      <option key={c} value={c}>
                        {label(c, true)}
                      </option>
                    ))}
                  </optgroup>
                </NativeSelect>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1.5">
                  <Label htmlFor="b-amount">Amount (TRY)</Label>
                  <Input
                    id="b-amount"
                    inputMode="decimal"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value.replace(/[^\d.,]/g, ""))}
                    className="h-10"
                    required
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>Every</Label>
                  <NativeSelect value={every} onChange={setEvery} aria-label="Budget period">
                    {Object.entries(BUDGET_PERIODS).map(([p, text]) => (
                      <option key={p} value={p}>
                        {text}
                      </option>
                    ))}
                  </NativeSelect>
                </div>
              </div>
              <Button type="submit" className="w-full" disabled={busy || !amount}>
                Save budget
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
