"use client";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

export const PERIODS = [
  { value: "day", label: "Today" },
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
  { value: "quarter", label: "Quarter" },
  { value: "year", label: "Year" },
] as const;

export function PeriodTabs({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <Tabs
      value={value}
      onValueChange={(v) => onChange(String(v))}
      className="max-w-full overflow-x-auto [scrollbar-width:none]"
    >
      <TabsList>
        {PERIODS.map((p) => (
          <TabsTrigger key={p.value} value={p.value}>
            {p.label}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}
