"use client";

import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/app-shell";
import { CategoryOptions, NativeSelect } from "@/components/fields";
import { ErrorNote, Loading } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, type Meta, type Tx } from "@/lib/api";
import { label, money } from "@/lib/format";
import { useApi } from "@/lib/use-api";
import { store, useStored } from "@/lib/use-stored";
import { cn } from "@/lib/utils";

const QUICK = [
  "Expenses:Food:Groceries",
  "Expenses:Food:Restaurants",
  "Expenses:Food:Coffee",
  "Expenses:Food:Delivery",
  "Expenses:Transport:Public",
  "Expenses:Transport:Taxi",
  "Expenses:Transport:Fuel",
  "Expenses:Health:Pharmacy",
  "Expenses:Shopping:Online",
  "Expenses:PersonalCare",
];
const LAST_ACCOUNT = "qp-last-account";

export default function AddPage() {
  const { data: meta, error, loading } = useApi<Meta>("/api/meta");
  const [amount, setAmount] = useState("");
  const [category, setCategory] = useState("Expenses:Food:Groceries");
  const remembered = useStored(LAST_ACCOUNT, "Assets:Cash:Wallet");
  const [picked, setAccount] = useState<string | null>(null);
  const account = picked ?? remembered;
  const [chosenDate, setDate] = useState("");
  const date = chosenDate || meta?.today || "";
  const [payee, setPayee] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);


  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!amount) return;
    setBusy(true);
    try {
      const tx = await api.post<Tx>("/api/transactions", {
        amount,
        category,
        payee,
        account,
        date,
        note,
      });
      store(LAST_ACCOUNT, account);
      toast.success(`${money(tx.amount.replace("-", ""), tx.currency)} · ${label(tx.category)}`);
      setAmount("");
      setPayee("");
      setNote("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const quick = meta ? QUICK.filter((q) => meta.categories.includes(q)) : [];
  return (
    <>
      <PageHeader title="Add" />
      {error ? <ErrorNote message={error} /> : null}
      {loading ? <Loading rows={2} /> : null}
      {meta ? (
        <form onSubmit={save} className="mx-auto max-w-md space-y-5">
          <div className="space-y-2">
            <Label htmlFor="amount">Amount (TRY)</Label>
            <Input
              id="amount"
              inputMode="decimal"
              autoFocus
              placeholder="0,00"
              value={amount}
              onChange={(e) => setAmount(e.target.value.replace(/[^\d.,]/g, ""))}
              className="h-14 text-center text-3xl font-semibold tabular-nums"
              required
            />
          </div>
          <div className="space-y-2">
            <Label>Category</Label>
            <div className="flex flex-wrap gap-1.5">
              {quick.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => setCategory(q)}
                  className={cn(
                    "rounded-full border px-3 py-1 text-xs transition-colors",
                    category === q ? "border-primary bg-primary text-primary-foreground" : "hover:bg-accent",
                  )}
                >
                  {label(q)}
                </button>
              ))}
            </div>
            <NativeSelect value={category} onChange={setCategory} aria-label="Category">
              <CategoryOptions categories={meta.categories} />
            </NativeSelect>
          </div>
          <Card size="sm">
            <CardContent className="grid grid-cols-2 gap-3">
              <div className="col-span-2 space-y-1.5">
                <Label htmlFor="payee">Where / who</Label>
                <Input id="payee" placeholder="Migros, Pazar..." value={payee} onChange={(e) => setPayee(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label>Paid from</Label>
                <NativeSelect value={account} onChange={setAccount} aria-label="Paid from">
                  {meta.accounts.map((a) => (
                    <option key={a} value={a}>
                      {label(a)}
                    </option>
                  ))}
                </NativeSelect>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="date">Date</Label>
                <Input id="date" type="date" value={date} onChange={(e) => setDate(e.target.value)} className="h-10" />
              </div>
              <div className="col-span-2 space-y-1.5">
                <Label htmlFor="note">Note</Label>
                <Input id="note" value={note} onChange={(e) => setNote(e.target.value)} />
              </div>
            </CardContent>
          </Card>
          <Button type="submit" size="lg" className="h-12 w-full text-base" disabled={busy || !amount}>
            {busy ? "Saving…" : "Save"}
          </Button>
          <p className="text-center text-xs text-muted-foreground">
            Income too: pick a category under Income (salary, bonus...).
          </p>
        </form>
      ) : null}
    </>
  );
}
