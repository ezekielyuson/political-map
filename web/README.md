# PGE — web frontend

Minimal Next.js 15 app that calls the PGE API. Lives in this directory so a
single GitHub repo serves both deploy targets (Fly for the Python API,
Vercel for this).

## What it does

- **`/`** — search box + DB stats banner. Search hits `/nodes?q=`.
- **`/nodes/[id]`** — node profile + 1-hop neighborhood, edges grouped by
  kind, every edge tagged with evidence type and source.
- **`/paths`** — find paths between two ids (BFS up to 5 hops).

Server components fetch directly from the API for first-paint speed; the
search box and path finder are client components.

## Local dev

```bash
cd web
npm install              # or pnpm / bun
cp .env.example .env.local
# default points at https://political-map.fly.dev; change to
# http://localhost:8000 if you're running the API on your laptop.
npm run dev
```

Open <http://localhost:3000>.

## Deploy on Vercel

1. <https://vercel.com/new> → import `ezekielyuson/political-map`.
2. **Root Directory** → `web` (this is the key step; Vercel won't see Python
   code outside `web/` and won't try to build it).
3. **Framework preset** → Next.js (auto-detected).
4. **Environment Variables** → add:
   - `NEXT_PUBLIC_PGE_API_URL` = `https://political-map.fly.dev`
5. Deploy.

Subsequent `git push` to `main` auto-redeploys both Vercel (this app) and
Fly (the API).

## Talking to a different API

`NEXT_PUBLIC_PGE_API_URL` controls where the frontend looks. Local dev
uses whatever's in `.env.local`; on Vercel it's the project's environment
variables. The fallback is `http://localhost:8000` so plain `npm run dev`
works as long as `pge serve` is running locally.

## Why hand-typed API types?

`lib/types.ts` mirrors the Pydantic response models in
`src/pge/graph/queries.py` by hand. The surface is small enough that hand
typing beats running an OpenAPI codegen step on every API change. When the
API schema grows, switch to a generated client.

## Conventions

- Tailwind for styling. No UI library — every component is in
  `components/` and rendered with utility classes. Easy to swap to shadcn
  later if it grows.
- Server components by default; `"use client"` only where state /
  effects are needed (`SearchBox`, `PathsPage`).
- All fetches go through `lib/api.ts`. Don't call `fetch` directly from
  components.
