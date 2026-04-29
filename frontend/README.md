# Bilibili Operator Console

The frontend is the operator UI for the ingestion system. It is not a generic
starter dashboard anymore.

Current responsibilities include:

- submitting ingest jobs
- browsing recent jobs and videos
- inspecting assets, comments, comment images, danmaku, subtitles, and
  completeness summaries

## Tooling

- React 19
- TypeScript
- Vite
- TanStack Router + Query
- Tailwind CSS + shadcn/ui

## Commands

Install workspace dependencies from the repository root:

```bash
bun install
```

Run the frontend locally:

```bash
bun run dev
```

Build the production bundle:

```bash
bun run build
```

If Bun is unavailable in the current runtime, the equivalent fallback command
is:

```bash
npm --workspace frontend run build
```

## Generated Client

The generated OpenAPI client lives in `frontend/src/client/`.

Refresh it after backend schema changes:

```bash
bash ./scripts/generate-client.sh
```

If you cannot use Bun in the current environment, generate the OpenAPI document
from `backend/` and then run:

```bash
npm --workspace frontend run generate-client
```

## Tests

Playwright tests live in `frontend/tests/`.

Run them with:

```bash
bunx playwright test
```
