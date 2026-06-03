# OpenIncent — Project Context & Decisions Log
*Snapshot for an AI collaborator (planning + building context). ~June 2026.*

## What OpenIncent is
Open-source incentive-compensation (sales-commission) engine. Input: a comp-plan definition (YAML) + deal/transaction data + payees. Output: per-payee commission payouts with a full, immutable **audit trail** explaining every number. Headless Python library + CLI (`icm`), plus a local desktop app and an HTTP API. Brand: **OpenIncent** (domain + GitHub org secured). Engine package: `icm-engine`.

## Founder constraints (these shape every decision)
- Solo founder, ~$0 capital. Bootstrapped, no fundraising.
- Needs first income by ~September 2026. Realistic only if selling starts now; a full living by then is NOT realistic — keep separate income.
- Strong technical builder, NOT a comp/vertical domain expert. Don't assume deep ICM/industry expertise.
- Sales-call-averse (firm personal constraint): all GTM is async/written-first, NO live sales calls. Use a "free audit" framing where the founder assesses the prospect (flips the dynamic).
- Division of labor: the build AI implements from specs; founder reviews/merges.

## Strategy & business model
- **Open-core**: free AGPL engine (adoption + credibility) + paid services now + recurring (hosting/AI/accountability) later, revenue-funded only.
- **No hosted multi-tenant SaaS now** — capital, ops, and liability (computing people's pay) are too high at $0. Stop extending the multi-tenant API.
- **Trust is the north star**: Correct → Transparent → Integrity (numbers never change silently). Every feature serves this.
- **Free adoption lane (never paywalled)**: engine, docs, plan templates, plan-from-text AI. Never charge for the *ability to use* the engine. Charge for *labor* (setup/implementation) and *judgment*. Templates are free, not a product.

## Positioning, ICP & competition
- **Wedge**: "own your comp logic, don't rent a black box" — open-source, self-hostable, auditable, your data stays yours, pay-once vs per-seat-forever.
- **ICP (current)**: lead with the *technical* buyer — RevOps/data/finance engineers who want to self-host and own their comp logic (matches founder's credibility). Do NOT pick a vertical yet (no vertical expertise); let the beachhead emerge from who shows up.
- **Not for**: tiny teams on a simple flat % who are happy in a spreadsheet.
- **Competitors**: Xactly/Varicent (enterprise); CaptivateIQ/Everstage (mid); QuotaPath (~$35/seat/mo) & Commissionly (low); **Core Commissions** ($20–35/payee/mo — closest direct competitor). The *open-source* ICM niche is empty; the *closed* SMB niche is NOT. Differentiate on **model** (open/own-data/no-per-seat) + services, NOT on features or "being new."
- **Moat** = brand/canonical project + community + vertical depth (later) + services/relationships + correctness track record + AGPL/CLA. The engine code is NOT the moat. A competitor open-sourcing a rival is low-probability; defend with moats + niche depth, don't fight a feature war.

## Revenue streams (phased)
- **Now (capital-free services):** productized **setup** ($750–2,500), **implementation** ($8–20k). Lead with technical setup ("you bring the plan, I make it run"), not comp-strategy design (founder isn't that expert).
- **Next (recurring, delivered as a service):** care plan ($50–150/mo), fractional comp-ops.
- **Later (revenue-funded):** managed hosting (flat per-org, NOT per-seat), payee portal, AI tier (BYOK free / managed paid), accountability tier (audit/SLA/SOC2).
- **Opportunistic:** commercial/dual license (~100% margin; requires CLA).
- Payee statements are **no-custody**: push to tools the customer already controls (email/Slack/Drive), or customer self-publishes a static portal, or self-hosts. Founder never holds rep data.

## Licensing / IP / legal
- **AGPL-3.0** engine; commercial tiers proprietary.
- **CLA required from the first outside contribution** (irreversible if skipped — preserves dual-licensing). Set up CLA Assistant; a custom Individual CLA with a relicensing grant is drafted.
- **Keep core deps permissive** (no GPL/AGPL in core) to preserve dual-licensing. Currently clean.
- **Brand/trademark**: name/domain/org secured; file trademark with traction. Copyright/patents won't stop a rival engine; trademark protects the name only.
- **Entity (LLC) + 1-page services agreement (liability cap) + NDA**: trigger at first paying client, not before.

## Product & engine — tech & decisions
- Python 3.11+, pydantic v2, typer (CLI `icm`), FastAPI, uv, ruff, mypy --strict, pytest. **Decimal everywhere** (no floats). **Deterministic**. Immutable **audit ledger** (JSONL + SQLite).
- Rule types: flat_rate, tiered (boundary-crossing attainment), accelerator. Safe filter expressions (`== != < > <= >= in`, `and/or`), no `eval`.
- **Quota is per-period**; `period_type` (monthly/quarterly) sets the attainment window; attainment **resets per window** (v1); window key derived from the transaction period.
- **Correctness fixes MERGED**: tiered infinite-loop when attainment exceeds the top tier (top tier now extends to infinity); tiered/accelerator crash on missing close_date; removed the hardcoded `period="2026-01"` default (derive from close_date, error if neither).

## Build status & spec queue
**MVP scope** (founder chose a fuller MVP over a leaner demo-only slice; this pushes selling toward July — mitigate by starting outreach in parallel):
**Build order (dependencies matter):**
1. **Splits/overlays** — `Transaction.credits` (split + overlay), credited amounts, `credit_allocated` ledger event. *(spec written)*
2. **Per-period quotas** — `Payee.quotas` map + `quota_for(window)`; all quota consumers (tiered/accelerator/attainment) use it. *(spec written)*
3. **Clawbacks / true-up** — recompute + diff vs prior; negative adjustments; exceptions report ("review only outliers"); persist commission lines. *(spec written)*
4. **Locked periods + versioning** — version calcs per (plan, period); lock pins the official version; no silent overwrite; diff-against-locked = true-up. *(spec written; after #3)*

**Trust-demo slice (do early — serves acquisition):**
- **Skip-logging** — tiered/accelerator log *why* a deal didn't pay (`rule_skipped` + reason). *(spec written)*
- **Order-trace view** — render the ledger as a readable "how this order became a commission" trail; CLI `icm trace` + `/v1/trace`; ledger-grounded only, never recompute. *(spec written; needs skip-logging)*
- **Attainment visibility** — `AttainmentSummary` (bookings/quota/%) in result + ledger + statement. *(spec written)*
- **Per-rep statement generator** — one isolated file per rep (XLSX/HTML/PDF); per-rep data isolation is a hard, tested requirement. *(spec written)*
- **Distribution** — `Payee.email`; send via customer SMTP / `.eml` / mail-merge; never send from our servers; isolation tested. *(spec written)*

Every spec: fail-before/pass-after tests, full `pytest` + `ruff` + `mypy --strict` green, determinism preserved, ledger property test intact.

## GTM
- **Async/written-first, no calls.** Free-audit framing (founder assesses prospect).
- **Channels**: SEO (slow, 6–12mo, plant now), Reddit/communities (conversations + discovery; helpful, not spammy), and the faster lever = **direct async free-audit outreach** to a built target list. No warm network.
- **Funnel**: free engine + content → conversations → free audit → paid setup → (later) recurring. Each engagement → a reusable free template + a content piece.
- **Distribution is the #1 risk**, not the product. Sell in parallel with building.
- **Validated pain** (r/SalesOperations): "month-end commission true-ups" across NetSuite/Stripe/Salesforce with credits/splits/proration → first content piece + validates clawback/split/true-up features. Key product insight: surface **only the exceptions/outliers** for review.
- **Realistic timeline**: months 1–2 = groundwork/pull (content, communities, a few conversations, 1–2 trying it), not revenue. First revenue plausibly months 3–6; by September only if selling starts now.

## Website
- Domain bought. **Host**: static site on **Cloudflare Pages** (free, fast, SSL, DNS) — or GitHub Pages. NOT a React SPA (bad SEO). Astro later when blogging; single static HTML for now.
- Built: `website/index.html` (landing — trust positioning + free-audit CTA) + `website/services.html` (offer ladder). Flat sibling files, relative links (work locally + deployed). Fill `[GITHUB_URL]` + `[CONTACT_EMAIL]`. Services positioned as technical setup, not consulting.

## Deferred — do NOT build now
Hosted multi-tenant SaaS; no-code plan builder; new-hire ramps; advanced quota categories; managed email sending; SOC 2/compliance; CRM connectors (warehouse/CSV/Excel/Parquet ingestion only).

## Immediate next steps
1. Fill website placeholders + deploy (Cloudflare Pages); delete the redundant `website/services/` folder.
2. Wire CLA (CLA Assistant) + add `CONTRIBUTING.md` + `LICENSING.md` (README links them).
3. Run the build prompts in the order above.
4. **DSL-vs-real-plans stress test** — encode ~10 real comp plans to find the engine's real gaps before selling (still outstanding).
5. Start free-audit outreach + first blog post (true-ups teardown) in parallel — don't wait for all features.
