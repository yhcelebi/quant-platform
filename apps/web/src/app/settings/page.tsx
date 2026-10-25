"use client";

import { useState } from "react";
import { useTheme } from "next-themes";
import { toast } from "sonner";
import { PageHeader } from "@/components/app-shell";
import { NativeSelect } from "@/components/fields";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { TOKEN_KEY, type Meta } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { store, useStored } from "@/lib/use-stored";

export default function SettingsPage() {
  const { theme, setTheme } = useTheme();
  const saved = useStored(TOKEN_KEY, "");
  const [edited, setLocal] = useState<string | null>(null);
  const token = edited ?? saved;
  const meta = useApi<Meta>("/api/meta");
  return (
    <>
      <PageHeader title="Settings" />
      <div className="grid max-w-2xl gap-4">
        <Card>
          <CardHeader>
            <CardTitle>Appearance</CardTitle>
          </CardHeader>
          <CardContent>
            <NativeSelect value={theme ?? "system"} onChange={setTheme} aria-label="Theme" className="w-48">
              <option value="system">System</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </NativeSelect>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Access token</CardTitle>
            <CardDescription>
              Only needed when the app is opened from another device and QP_API__TOKEN is set.
              Stored in this browser only.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex gap-2">
            <Label htmlFor="token" className="sr-only">
              Token
            </Label>
            <Input id="token" type="password" value={token} onChange={(e) => setLocal(e.target.value)} />
            <Button
              onClick={() => {
                store(TOKEN_KEY, token);
                toast.success(token ? "Token saved" : "Token removed");
              }}
            >
              Save
            </Button>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Statement formats</CardTitle>
            <CardDescription>From data/books/sources.yaml</CardDescription>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            {meta.data?.sources.map((s) => (
              <div key={s.name} className="flex justify-between">
                <span>{s.name}</span>
                <span className="text-muted-foreground">
                  {s.ready ? (s.verified ? "ready" : "set up, not yet verified") : "not set up"}
                </span>
              </div>
            ))}
            {meta.data?.errors.length ? (
              <div className="pt-2 text-status-critical">
                Books errors: {meta.data.errors.join("; ")}
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
