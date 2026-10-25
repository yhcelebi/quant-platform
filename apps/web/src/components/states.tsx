import { CircleAlert } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-16 w-full" />
      ))}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  const unauthorized = /token/i.test(message);
  return (
    <div className="flex items-start gap-2 rounded-lg border border-status-critical/40 bg-status-critical/5 p-3 text-sm">
      <CircleAlert className="mt-0.5 size-4 shrink-0 text-status-critical" aria-hidden />
      <div>
        <div className="font-medium">Could not load</div>
        <div className="text-muted-foreground">
          {message}
          {unauthorized ? " — enter the access token in Settings." : ""}
          {/fetch|network/i.test(message) ? " — is `make app` running?" : ""}
        </div>
      </div>
    </div>
  );
}

export function Notes({ notes }: { notes: string[] }) {
  if (!notes.length) return null;
  return (
    <ul className="space-y-1 text-xs text-muted-foreground">
      {notes.map((n) => (
        <li key={n}>Note: {n.startsWith("USD") || n.startsWith("EUR") ? `no TCMB rate for ${n} (left out)` : n}</li>
      ))}
    </ul>
  );
}
