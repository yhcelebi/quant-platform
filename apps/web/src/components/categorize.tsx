"use client";

import { useState } from "react";
import { toast } from "sonner";
import { CategoryOptions, NativeSelect } from "@/components/fields";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, type Tx } from "@/lib/api";
import { label, money } from "@/lib/format";

/** Change a transaction's category; "remember" adds a rule for similar lines. */
export function CategorizeForm({
  tx,
  categories,
  onDone,
}: {
  tx: Tx;
  categories: string[];
  onDone: () => void;
}) {
  const unc = tx.category.endsWith("Uncategorized");
  const [category, setCategory] = useState(unc ? "" : tx.category);
  const [payee, setPayee] = useState(tx.payee ?? "");
  const [learn, setLearn] = useState(true);
  const [busy, setBusy] = useState(false);

  async function save() {
    if (!tx.id) return;
    setBusy(true);
    try {
      const r = await api.post<Tx & { learned: string | null }>(
        `/api/transactions/${encodeURIComponent(tx.id)}/category`,
        { category, payee: payee || null, learn },
      );
      toast.success(
        `${label(r.category)}${r.learned ? ` · will remember “${r.learned}”` : ""}`,
      );
      onDone();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="text-sm">
        <div className="font-medium">{tx.description}</div>
        <div className="text-muted-foreground">
          {tx.date} · {money(tx.amount, tx.currency)} · {label(tx.account)}
        </div>
      </div>
      <div className="space-y-1.5">
        <Label>Category</Label>
        <NativeSelect value={category} onChange={setCategory} aria-label="Category">
          <option value="" disabled>
            Choose a category…
          </option>
          <CategoryOptions categories={categories} />
        </NativeSelect>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={`payee-${tx.id}`}>Payee (optional)</Label>
        <Input id={`payee-${tx.id}`} value={payee} onChange={(e) => setPayee(e.target.value)} />
      </div>
      <label className="flex items-center gap-2 text-sm">
        <Checkbox checked={learn} onCheckedChange={(v) => setLearn(Boolean(v))} />
        Remember for similar transactions
      </label>
      <Button onClick={save} disabled={busy || !category} className="w-full">
        {busy ? "Saving…" : "Save category"}
      </Button>
    </div>
  );
}
