import type { NextConfig } from "next";

// Static export (ADR 0017, 8): `pnpm build` writes out/, which `make app` serves next to
// the JSON API on one port. No Next.js server runs in production.
const nextConfig: NextConfig = {
  output: "export", // Cache Components / Partial Prefetching need a server: not used
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
