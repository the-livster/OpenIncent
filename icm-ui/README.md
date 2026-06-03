# icm-ui

Web interface for the [icm-engine](../icm-engine) incentive compensation
calculation engine. Upload a YAML plan, transactions CSV, and payees CSV to
calculate commissions and explore the audit trail.

## Quick Start

```bash
# 1. Start the icm-engine API (from the icm-engine directory)
cd ../icm-engine
uv run icm serve

# 2. Start the UI dev server (from this directory)
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). The UI connects to the
engine API at `http://localhost:8000` by default.

## Configuration

Copy `.env.example` to `.env` and adjust as needed:

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_BASE` | `http://localhost:8000` | icm-engine API URL |

## Tech Stack

- React 19, TypeScript, Vite 8
- Tailwind CSS 4
- No external state management — local React state

## Development

```bash
npm run dev       # Start dev server with HMR
npm run build     # Type-check and build for production
npm run lint      # Run ESLint
npm run preview   # Preview production build
```
