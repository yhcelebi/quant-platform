"use client";

import { useState } from "react";
import { Search } from "lucide-react";
import { PageHeader } from "@/components/app-shell";
import { CategorizeForm } from "@/components/categorize";
import { PeriodTabs } from "@/components/period";
import { ErrorNote, Loading } from "@/components/states";
import { TxRow } from "@/components/tx-row";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { Meta, Tx } from "@/lib/api";
import { useApi } from "@/lib/use-api";

export default function TransactionsPage() {
  const [period, setPeriod] = useState("month");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Tx | null>(null);
  const meta = useApi<Meta>("/api/meta");
  const list = useApi<{ start: string; end: string; items: Tx[] }>("/api/transactions", {
    period,
    q,
  });
  const items = list.data?.items ?? [];
  return (
    <>
      <PageHeader title="Transactions">
        <PeriodTabs value={period} onChange={setPeriod} />
      </PageHeader>
      <div className="relative mb-4">
        <Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          placeholder="Search payee or description"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="h-10 pl-9"
        />
      </div>
      {list.error ? <ErrorNote message={list.error} /> : null}
      {list.loading && !list.data ? <Loading rows={6} /> : null}
      {list.data ? (
        <Card size="sm">
          <CardContent className="-mx-1">
            {items.length ? (
              items.map((t, i) => (
                <TxRow
                  key={t.id ?? i}
                  tx={t}
                  onClick={t.id && /^(Expenses|Income):/.test(t.category) ? () => setOpen(t) : undefined}
                />
              ))
            ) : (
              <p className="p-2 text-sm text-muted-foreground">No transactions in this period.</p>
            )}
          </CardContent>
        </Card>
      ) : null}
      <Sheet open={open !== null} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent side="bottom" className="mx-auto max-w-md rounded-t-2xl">
          <SheetHeader>
            <SheetTitle>Change category</SheetTitle>
          </SheetHeader>
          <div className="px-4 pb-6">
            {open && meta.data ? (
              <CategorizeForm
                tx={open}
                categories={meta.data.categories}
                onDone={() => {
                  setOpen(null);
                  void list.reload();
                }}
              />
            ) : null}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
