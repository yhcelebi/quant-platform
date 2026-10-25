"use client";

import { CircleCheck } from "lucide-react";
import { PageHeader } from "@/components/app-shell";
import { CategorizeForm } from "@/components/categorize";
import { ErrorNote, Loading } from "@/components/states";
import { Card, CardContent } from "@/components/ui/card";
import type { Meta, Tx } from "@/lib/api";
import { useApi } from "@/lib/use-api";

export default function ReviewPage() {
  const meta = useApi<Meta>("/api/meta");
  const queue = useApi<{ items: Tx[] }>("/api/review");
  const items = queue.data?.items ?? [];
  return (
    <>
      <PageHeader title="Review" />
      <p className="mb-4 text-sm text-muted-foreground">
        Imported lines no rule recognised. Pick a category once and tick “remember”; similar
        lines are then categorized automatically on the next import.
      </p>
      {queue.error ? <ErrorNote message={queue.error} /> : null}
      {queue.loading && !queue.data ? <Loading /> : null}
      {queue.data && items.length === 0 ? (
        <div className="flex items-center gap-2 rounded-xl border p-4 text-sm">
          <CircleCheck className="size-4 text-status-good" aria-hidden /> All caught up.
        </div>
      ) : null}
      <div className="grid gap-3 md:grid-cols-2">
        {meta.data
          ? items.map((t) => (
              <Card key={t.id} size="sm">
                <CardContent>
                  <CategorizeForm tx={t} categories={meta.data!.categories} onDone={queue.reload} />
                </CardContent>
              </Card>
            ))
          : null}
      </div>
    </>
  );
}
