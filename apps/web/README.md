# Household books web app

Next.js + shadcn/ui (Base UI) front end for the books API (`apps/api`, ADR 0017). Static
export: `pnpm build` writes `out/`, which `make app` serves next to the API on one port.

```bash
make web-install   # once
make web-build     # after changing the app
make app           # http://127.0.0.1:8765
make web-dev       # hot reload on :3000; run `make app` too, with NEXT_PUBLIC_API=http://127.0.0.1:8765
make web-check     # eslint + TypeScript
```

All amounts come from the API as exact decimal strings and are only formatted here.
