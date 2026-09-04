# OpenIncent

A method for getting commission right — and free, open software that runs it.

Commission rarely breaks because the math is hard. It breaks because **nobody can
prove the number.** OpenIncent is a repeatable way of taking commission from a
messy spreadsheet to **paid, reconciled, and explainable** — and proving it at
every step. The method is the product; the engine is free software that runs it.

- **The [method](METHOD.md)** — eight steps from messy spreadsheet to a run that
  reconciles to reality and explains every line, with a four-level conformance
  ladder (L0–L3) as your proof it worked.
- **The engine** — a free, open implementation that runs the method: it validates
  your data, checks your plan, calculates payouts, and reconciles the result to
  what you actually paid.

You can follow the method by hand, run the engine yourself, or
[have someone run it for you](https://openincent.com/services). The method and the
engine are both free.

**Read the method:** [The OpenIncent Method](METHOD.md).

**Desktop app for Windows.** The engine is also a Python library (`icm-engine`) with CLI and HTTP API.

[![License](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)

---

## What it does

1. **You write your comp plan** in YAML, or describe it in plain English and let the AI builder draft it.
2. **You add your deals** — CSV, Excel, or Parquet. Messy column names get fuzzy-matched automatically.
3. **You get explained payouts.** Per-rep commission statements (XLSX, HTML, PDF), plus a full audit ledger that traces every dollar to the exact rule and deal behind it.

## Who it is for

Teams whose comp has outgrown a spreadsheet but does not justify a full RevOps hire or a per-seat SaaS subscription. Splits, tiers, accelerators, draws, clawbacks, manager overrides, team quotas — all handled.

**Not for** tiny teams perfectly happy in a spreadsheet.

## Quick start

### Desktop app (Windows)

[Download the latest release](https://github.com/the-livster/OpenIncent/releases/latest) — one `.exe`, no install, no cloud, no account.

### CLI

```bash
pip install icm-engine
icm --plan plan.yaml --transactions deals.csv --payees reps.csv --output out/
```

### From source

```bash
git clone https://github.com/the-livster/OpenIncent.git
cd OpenIncent/icm-engine
uv sync
uv run icm --plan plan.yaml --transactions deals.csv --payees reps.csv --output out/
```

## Features

- **3 rule types:** flat rate, tiered (boundary-crossing), accelerator
- **Crediting:** splits (must sum to 100%) and overlays (additive)
- **Manager hierarchy:** auto-generated upline override credits
- **Team/shared quotas:** pooled bookings and attainment %
- **Multi-plan runs:** different payees on different plans in one calculation
- **Draws/guarantees:** non-recoverable (simple floor) and recoverable (balance threading)
- **Period locking + delta true-ups:** lock a period, recalculate later, get only the deltas
- **Partial-period pro-rating:** full, daily, or zero
- **Multi-currency:** display-only conversion at output boundary
- **Finance payout register:** auto-generated XLSX on period lock
- **MBOs/bonuses:** non-commission payouts
- **Manual adjustments:** post-cap/draw overrides with required reason
- **Ramp schedules:** quota relief for new hires
- **Time-varying quotas:** per-period quota overrides
- **Rule composition:** `on_rule` — a rule whose base is what an earlier rule paid, so a kicker layered on base commission has one rate to maintain, not two
- **Year-to-date attainment:** `attainment_basis: cumulative` — tiers measured against fiscal-year-cumulative bookings and quota, so tier position carries across periods
- **Per-rep statements:** XLSX, HTML, PDF — one file per payee
- **Distribution:** SMTP / .eml / mail-merge; safe by default (no sending without `--send`)
- **Audit ledger:** every decision point recorded — dispute resolution without recomputation
- **Reconciliation:** diff a run against what was actually paid — surfaces underpaid, overpaid, and missed lines
- **AI plan builder:** describe your comp plan in plain English, get validated YAML

## Architecture

- **Engine:** Python 3.11+, pydantic v2, Decimal everywhere (no floats), deterministic
- **CLI:** Typer + Rich
- **API:** FastAPI (local only by default)
- **Desktop app:** React 19 + TypeScript + Vite + Tailwind, packaged via PyInstaller + pywebview
- **Database:** SQLite (local), schema v10
- **Auto-updater:** Ed25519-signed manifest from GitHub Releases

## License

AGPL-3.0. You can run it, modify it, and self-host it freely. If you offer it as a network service, you must release your modifications. [Commercial licensing](LICENSING.md) available.

## Services

Setup and managed comp runs available at [openincent.com/services](https://openincent.com/services). One-time engagement — we configure your plan, reconcile it against your numbers, and hand it over. No subscription, no lock-in.

---

[Made in Canada](https://openincent.com) · [hello@openincent.com](mailto:hello@openincent.com)
