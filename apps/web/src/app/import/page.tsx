"use client";

import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/app-shell";
import { NativeSelect } from "@/components/fields";
import { ErrorNote } from "@/components/states";
import { TxRow } from "@/components/tx-row";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, type Meta, type Tx } from "@/lib/api";
import { useApi } from "@/lib/use-api";

type Result = {
  written: boolean;
  new: Tx[];
  categorized: number;
  uncategorized: number;
  duplicates: number;
  transfers: string[];
  skipped: string[];
};

export default function ImportPage() {
  const meta = useApi<Meta>("/api/meta");
  const [source, setSource] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const sources = meta.data?.sources ?? [];
  const chosen = source || sources.find((s) => s.ready)?.name || "";

  async function run(dryRun: boolean) {
    if (!file || !chosen) return;
    setBusy(true);
    const form = new FormData();
    form.set("file", file);
    form.set("source", chosen);
    form.set("dry_run", String(dryRun));
    try {
      const r = await api.upload<Result>("/api/import", form);
      setResult(r);
      if (!dryRun) toast.success(`${r.new.length} transactions added`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Import a statement" />
      {meta.error ? <ErrorNote message={meta.error} /> : null}
      <div className="grid gap-4 md:grid-cols-[20rem_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>File</CardTitle>
            <CardDescription>CSV or Excel export from your bank. Importing the same lines again adds nothing.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-1.5">
              <Label>Bank / wallet</Label>
              <NativeSelect value={chosen} onChange={setSource} aria-label="Source">
                {sources.map((s) => (
                  <option key={s.name} value={s.name} disabled={!s.ready}>
                    {s.name}
                    {s.ready ? "" : " (format not set up yet)"}
                  </option>
                ))}
              </NativeSelect>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="file">Statement</Label>
              <Input
                id="file"
                type="file"
                accept=".csv,.txt,.xlsx,.xls"
                onChange={(e) => {
                  setFile(e.target.files?.[0] ?? null);
                  setResult(null);
                }}
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Button variant="outline" onClick={() => run(true)} disabled={busy || !file || !chosen}>
                Preview
              </Button>
              <Button
                onClick={() => run(false)}
                disabled={busy || !file || !chosen || !result || result.written}
              >
                Import
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Banks whose format is not set up yet: run{" "}
              <code className="rounded bg-muted px-1">qp books inspect FILE</code> once and fill{" "}
              <code className="rounded bg-muted px-1">data/books/sources.yaml</code>.
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>{result ? (result.written ? "Imported" : "Preview") : "Result"}</CardTitle>
            {result ? (
              <div className="flex flex-wrap gap-1.5 pt-1">
                <Badge variant="secondary">{result.new.length} new</Badge>
                <Badge variant="secondary">{result.categorized} categorized</Badge>
                <Badge variant="outline">{result.uncategorized} to review</Badge>
                <Badge variant="outline">{result.duplicates} already imported</Badge>
                <Badge variant="outline">{result.transfers.length} transfers already recorded</Badge>
              </div>
            ) : null}
          </CardHeader>
          <CardContent className="-mx-2">
            {result?.new.map((t, i) => <TxRow key={t.id ?? i} tx={t} />)}
            {result && result.skipped.length ? (
              <details className="px-2 pt-3 text-xs text-muted-foreground">
                <summary>{result.skipped.length} lines skipped</summary>
                <ul className="mt-1 space-y-0.5">
                  {result.skipped.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
              </details>
            ) : null}
            {!result ? <p className="px-2 text-sm text-muted-foreground">Choose a file and press Preview.</p> : null}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
