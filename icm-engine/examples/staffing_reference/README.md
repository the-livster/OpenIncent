# Worked example — a staffing agency's commission run, reconciled to the cent

This is a complete, reproducible commission run for a fictional UK recruitment agency,
**Northwind Recruitment**. It exists to answer one question with evidence: *can you trust the
number?* Everything below is produced by the open-source engine from the input files in this
folder — you can re-run it and get the same figures.

It exercises the comp patterns that generic tools fumble and agencies patch by hand:

- **Permanent desk** — placement-fee commission, **tiered** so the part of billings above target
  pays a higher rate (boundary-crossing).
- **Contract desk** — commission on **gross profit** (bill rate − pay rate × hours), not revenue.
- **Desk splits** — a placement shared 60/40 between two recruiters; a contract deal split 50/50.
- **New-hire ramp** — a reduced quota in a starter's first months.
- **Manager override** — a desk lead earns 2% on the team's placements.
- **Clawback** — a placement that fell through inside the guarantee period, reversed with a reason.

Then it does the thing a spreadsheet can't: **reconciles the result against what the agency
actually paid**, and finds the money.

---

## Reproduce it

```bash
cd icm-engine

# 1. The plans are self-checking — every plan ships executable invariants.
uv run icm check-plan examples/staffing_reference/perm_plan.yaml
uv run icm check-plan examples/staffing_reference/contract_plan.yaml

# 2. Run the month (three plans, one run).
uv run icm \
  --plans examples/staffing_reference/perm_plan.yaml \
  --plans examples/staffing_reference/contract_plan.yaml \
  --plans examples/staffing_reference/mgmt_plan.yaml \
  --transactions examples/staffing_reference/transactions.csv \
  --payees examples/staffing_reference/payees.csv \
  --adjustments examples/staffing_reference/adjustments.csv \
  --output examples/staffing_reference/output --csv --no-db

# 3. Check the data, then reconcile against what payroll actually paid.
uv run icm validate --plan examples/staffing_reference/perm_plan.yaml \
  --transactions examples/staffing_reference/transactions.csv \
  --payees examples/staffing_reference/payees.csv

uv run icm reconcile \
  --commissions examples/staffing_reference/output/commissions.csv \
  --paid examples/staffing_reference/paid.csv

# 4. The part you hand to a recruiter: one branded statement per person.
uv run icm statements \
  --plans examples/staffing_reference/perm_plan.yaml \
  --plans examples/staffing_reference/contract_plan.yaml \
  --plans examples/staffing_reference/mgmt_plan.yaml \
  --transactions examples/staffing_reference/transactions.csv \
  --payees examples/staffing_reference/payees.csv \
  --adjustments examples/staffing_reference/adjustments.csv \
  --output examples/staffing_reference/output/statements \
  --period 2026-05 --format html \
  --theme examples/staffing_reference/theme.yaml
```

Outputs land in [`output/`](output/): `commissions.csv` (every line, with the rule behind it),
`summary.csv` (per-rep totals), `ledger.jsonl` (the full audit trail), and `reconciliation.csv` —
plus [`output/statements/`](output/statements/), one **branded HTML statement per person**
(Northwind colours and footer via [`theme.yaml`](theme.yaml); each payee is routed to their own
plan, and each statement shows the tier-slice math in plain English). Open
[Priya's](output/statements/statement_P-101_2026-05.html) to see the boundary-crossing split and
the clawback exactly as a recruiter would. Add `show_powered_by: false` to the theme to
white-label it.

---

## The plans are self-checking

Before a single deal is loaded, each plan proves itself. `icm check-plan` runs the invariants
declared on the plan through the real engine:

```
PASS 100% of quota pays the £2,000 OTE: payout 2000.00 == 2000
PASS a single deal that crosses 100% splits 10%/15%: payout 2600.00 == 2600
```

A mistranscribed rate that silently underpaid at quota would fail here — at edit time, not at
payroll time. The annual plan rebuild goes from *pray* to *compile*.

---

## The month

Six people, May 2026. Per-rep totals (`summary.csv`):

| Rep | Desk | Quota | Commission |
|-----|------|------:|-----------:|
| Priya Shah | Perm | £20,000 | **£2,480.00** |
| Tom Reilly | Perm | £20,000 | £1,780.00 |
| Jess Owens (new hire) | Perm | £20,000 | £950.00 |
| Aisha Khan | Contract | £8,000 GP | £1,897.50 |
| Marco Diaz | Contract | £8,000 GP | £990.00 |
| Dana Lowe (desk manager) | Override | — | £1,050.00 |

Every figure traces to the cent. Three worth walking through:

### Priya — boundary-crossing, a split, and a clawback

Priya billed £18,000 solo (`NW-1042`) plus her 60% of a £12,000 split placement (`NW-1087` →
£7,200), carrying her to £25,200 against a £20,000 quota. The engine pays each band at its own rate:

```
NW-1042  18,000 between 0% and 90% of quota   -> 10%   = 1,800.00
NW-1087   2,000 between 90% and 100% of quota  -> 10%   =   200.00
NW-1087   5,200 between 100% and 126% of quota -> 15%   =   780.00
adj      clawback (PL-0998 fell through, guarantee) =    -300.00
                                                          --------
                                                          2,480.00
```

The £5,200 of billings *above target* pays 15%, not 10%. That uplift — **£260** — is exactly what a
single-rate spreadsheet misses (see the reconciliation below). The clawback is a separate, audited
line with a reason, not a silent edit.

### Aisha — commission on gross profit, not revenue

Aisha's contract deal `CT-3310` was £60/hr charged, £40/hr paid, 480 hours → **£9,600 gross
profit**. Her plan pays on GP, tiered:

```
CT-3310  8,000 GP between 0% and 100% of quota   -> 15%    = 1,200.00
CT-3310  1,600 GP between 100% and 120% of quota -> 22.5%  =   360.00
CT-3457  1,500 GP (50% of a split) above quota   -> 22.5%  =   337.50
                                                              --------
                                                              1,897.50
```

### Dana — desk-manager override

Dana earns 2% on her team's placements. The engine generates an override credit per deal and the
ledger shows each one — £360 on Priya's £18,000, £260 on Tom's £13,000, and so on — totalling
**£1,050**. (Notice the clawback didn't reduce it: overrides ride on placements, not adjustments.)

Jess, two months into the job, is on a **ramp**: her quota is relieved to £10,000 for May, so her
£9,500 placement lands at 95% attainment and pays in the first tier.

---

## The reconciliation — where the money is

The agency paid these recruiters from a spreadsheet. `icm reconcile` diffs the plan against what
was actually paid (`paid.csv`):

```
| Payee | Period  | Computed |     Paid |     Delta | Status    |
|-------|---------|----------|----------|-----------|-----------|
| P-101 | 2026-05 | 2,480.00 | 2,220.00 |   +260.00 | underpaid |
| P-201 | 2026-05 | 1,050.00 |     0.00 | +1,050.00 | missing   |

2 discrepancy(ies): 1 missing, 1 underpaid
Owed to payees: 1,310.00  |  Paid above plan: 0.00  |  Net: +1,310.00
```

Two errors an afternoon's reconciliation surfaced, both of the kind manual processes make every month:

1. **Priya was underpaid £260.** The spreadsheet paid her whole £25,200 at the standard 10% and
   missed the higher rate on the portion above target. This is *the* classic incumbent failure — pay
   the whole deal at one rate and leave a human to notice.
2. **Dana's £1,050 override was never paid.** The override simply got forgotten in the manual run.

That's **£1,310 owed to two people** — found, explained line by line, and traceable to the exact
rule and deal. If a run reconciles cleanly, you get that in writing too: *"your numbers match,
here's the proof."*

---

## The audit trail is the point

Every number above is backed by a readable ledger entry. The full trail for this run is in
[`output/ledger.jsonl`](output/ledger.jsonl) — one line per decision, each with a plain-English
explanation:

```json
{"transaction_id": "CT-3310", "payee_id": "P-110", "event_type": "attainment_computed",
 "human_readable": "P-110 booked 11100.0 against 8000 quota = 138.8%"}
```

Filter it to a single deal — every decision behind Priya's split placement:

```bash
grep '"NW-1087"' examples/staffing_reference/output/ledger.jsonl
```

(Run with the database instead of `--no-db`, and `icm trace --txn NW-1087 --payee P-101` renders the
same per-deal walk as a table.)

No black box. Run it yourself, read every line — or have it run for you, every month, reconciled and
explained: [openincent.com/services](https://openincent.com/services).
