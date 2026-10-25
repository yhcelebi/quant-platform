"use client";

import { Fragment, useState } from "react";
import { PageHeader } from "@/components/app-shell";
import { ErrorNote, Loading, Notes } from "@/components/states";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { label, money, pct } from "@/lib/format";
import type { Meta } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

type Amounts = { account: string; amount: string }[];
type Income = {
  start: string;
  end: string;
  income: Amounts;
  expenses: Amounts;
  total_income: string;
  total_expenses: string;
  net: string;
  savings_rate: string | null;
  missing_rates: string[];
};
type BLine = { account: string; native: Record<string, string>; value: string | null };
type Balance = {
  date: string;
  assets: BLine[];
  liabilities: BLine[];
  total_assets: string;
  total_liabilities: string;
  net_worth: string;
  opening_equity: string;
  retained: string;
  revaluation: string;
  missing_rates: string[];
};
type CashFlow = {
  start: string;
  end: string;
  opening_cash: string;
  sections: { section: string; total: string; lines: { group: string; amount: string }[] }[];
  net_flow: string;
  revaluation: string;
  closing_cash: string;
  missing_rates: string[];
};

const SECTION: Record<string, string> = {
  opening: "Opening balances",
  operating: "Operating (income and spending)",
  investing: "Investing",
  financing: "Financing (loans)",
  exchange: "Currency exchange",
  other: "Other",
};

function Row({
  name,
  value,
  strong,
  indent,
  hint,
}: {
  name: string;
  value: string;
  strong?: boolean;
  indent?: boolean;
  hint?: string;
}) {
  return (
    <TableRow className={cn(strong && "font-semibold")}>
      <TableCell className={cn(indent && "pl-6 text-muted-foreground")}>
        {name}
        {hint ? <span className="ml-2 text-xs text-muted-foreground">{hint}</span> : null}
      </TableCell>
      <TableCell className="text-right tabular-nums">{value}</TableCell>
    </TableRow>
  );
}

function IncomeTab({ month }: { month: string }) {
  const { data, error, loading } = useApi<Income>("/api/statements/income", { day: `${month}-15` });
  if (error) return <ErrorNote message={error} />;
  if (loading || !data) return <Loading />;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Income statement</CardTitle>
        <CardDescription>
          {data.start} – {data.end} · in TRY at TCMB rates of each day
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableBody>
            <Row name="Income" value={money(data.total_income)} strong />
            {data.income.map((x) => (
              <Row key={x.account} name={label(x.account, true)} value={money(x.amount)} indent />
            ))}
            <Row name="Expenses" value={money(data.total_expenses)} strong />
            {[...data.expenses]
              .sort((a, b) => Number(b.amount) - Number(a.amount))
              .map((x) => (
                <Row key={x.account} name={label(x.account, true)} value={money(x.amount)} indent />
              ))}
            <Row
              name="Net savings"
              value={money(data.net)}
              strong
              hint={data.savings_rate ? `${pct(data.savings_rate)} of income` : undefined}
            />
          </TableBody>
        </Table>
        <Notes notes={data.missing_rates} />
      </CardContent>
    </Card>
  );
}

function BalanceTab({ month }: { month: string }) {
  const [y, m] = month.split("-").map(Number);
  const last = new Date(y, m, 0).getDate();
  const { data, error, loading } = useApi<Balance>("/api/statements/balance", {
    day: `${month}-${String(last).padStart(2, "0")}`,
  });
  if (error) return <ErrorNote message={error} />;
  if (loading || !data) return <Loading />;
  const native = (x: BLine) =>
    Object.entries(x.native)
      .filter(([c]) => c !== "TRY")
      .map(([c, v]) => money(v, c))
      .join(", ");
  return (
    <Card>
      <CardHeader>
        <CardTitle>Balance sheet</CardTitle>
        <CardDescription>On {data.date} · foreign currency at that day&apos;s TCMB rate</CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableBody>
            <Row name="Assets" value={money(data.total_assets)} strong />
            {data.assets.map((x) => (
              <Row key={x.account} name={label(x.account, true)} value={money(x.value)} indent hint={native(x)} />
            ))}
            <Row name="Liabilities" value={money(data.total_liabilities)} strong />
            {data.liabilities.map((x) => (
              <Row key={x.account} name={label(x.account, true)} value={money(x.value)} indent />
            ))}
            <Row name="Net worth" value={money(data.net_worth)} strong />
            <Row name="Opening balances" value={money(data.opening_equity)} indent />
            <Row name="Savings since" value={money(data.retained)} indent />
            <Row name="Currency revaluation" value={money(data.revaluation)} indent />
          </TableBody>
        </Table>
        <p className="mt-3 text-xs text-muted-foreground">
          Midas and IBKR show the net amount transferred until their ledgers are linked.
        </p>
        <Notes notes={data.missing_rates} />
      </CardContent>
    </Card>
  );
}

function CashFlowTab({ month }: { month: string }) {
  const { data, error, loading } = useApi<CashFlow>("/api/statements/cashflow", { day: `${month}-15` });
  if (error) return <ErrorNote message={error} />;
  if (loading || !data) return <Loading />;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Cash flow</CardTitle>
        <CardDescription>
          {data.start} – {data.end} · banks, wallets and cash; transfers between them net to zero
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableBody>
            <Row name="Opening cash" value={money(data.opening_cash)} strong />
            {data.sections.map((s) => (
              <Fragment key={s.section}>
                <Row name={SECTION[s.section] ?? s.section} value={money(s.total)} strong />
                {[...s.lines]
                  .sort((a, b) => Number(a.amount) - Number(b.amount))
                  .map((l) => (
                    <Row key={`${s.section}-${l.group}`} name={label(l.group, true)} value={money(l.amount)} indent />
                  ))}
              </Fragment>
            ))}
            <Row name="Currency revaluation of cash" value={money(data.revaluation)} />
            <Row name="Closing cash" value={money(data.closing_cash)} strong />
          </TableBody>
        </Table>
        <Notes notes={data.missing_rates} />
      </CardContent>
    </Card>
  );
}

export default function StatementsPage() {
  // the current month comes from the API (Istanbul date), not the build machine's clock
  const meta = useApi<Meta>("/api/meta");
  const [chosen, setMonth] = useState("");
  const month = chosen || meta.data?.today.slice(0, 7) || "";
  if (meta.error) return <ErrorNote message={meta.error} />;
  if (!month) return <Loading />;
  return (
    <>
      <PageHeader title="Statements">
        <Input type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} className="h-9 w-40" />
      </PageHeader>
      <Tabs defaultValue="income">
        <TabsList className="mb-4">
          <TabsTrigger value="income">Income</TabsTrigger>
          <TabsTrigger value="balance">Balance sheet</TabsTrigger>
          <TabsTrigger value="cash">Cash flow</TabsTrigger>
        </TabsList>
        <TabsContent value="income">
          <IncomeTab month={month} />
        </TabsContent>
        <TabsContent value="balance">
          <BalanceTab month={month} />
        </TabsContent>
        <TabsContent value="cash">
          <CashFlowTab month={month} />
        </TabsContent>
      </Tabs>
    </>
  );
}
