"""Generate large, messy test datasets for icm-engine stress testing.

Produces:
  - payees.csv (50+ payees, inconsistent formatting)
  - transactions.csv (300+ deals, messy dates/amounts)
  - saas_plan.yaml (tiered with accelerators, thresholds, SPIFs)

Usage: uv run python examples/gen_large_dataset.py
Output: examples/large_dataset/
"""
from __future__ import annotations

import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

random.seed(42)

OUT = Path(__file__).parent / "large_dataset"
OUT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Payees — 55 reps + managers, deliberately messy
# ---------------------------------------------------------------------------
FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael",
    "Linda", "David", "Elizabeth", "William", "Barbara", "Richard", "Susan",
    "Joseph", "Jessica", "Thomas", "Sarah", "Christopher", "Karen",
    "Charles", "Lisa", "Daniel", "Nancy", "Matthew", "Betty", "Anthony",
    "Margaret", "Mark", "Sandra", "Donald", "Ashley", "Steven", "Kimberly",
    "Paul", "Emily", "Andrew", "Donna", "Joshua", "Michelle",
    "Kenneth", "Carol", "Kevin", "Amanda", "Brian", "Dorothy", "George",
    "Melissa", "Timothy", "Deborah", "Ronald", "Stephanie", "Jason", "Rebecca",
    "Edward", "Sharon",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark",
    "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King",
    "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green",
    "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell",
    "Carter", "Roberts",
]
ROLES = ["AE"] * 30 + ["SDR"] * 10 + ["Manager"] * 8 + ["SE"] * 5 + ["CSM"] * 2

def make_payees() -> list[dict[str, str]]:
    payees = []
    used_names: set[str] = set()
    for i in range(55):
        fn = random.choice(FIRST_NAMES)
        ln = random.choice(LAST_NAMES)
        name = f"{fn} {ln}"
        # Force some duplicates / near-duplicates for messiness
        if i % 7 == 0 and i > 0:
            name = f"{fn} {ln} Jr."
        if name in used_names:
            name = f"{fn} {ln} {random.choice(['II', 'III', 'Sr.'])}"
        used_names.add(name)

        pid = f"P{str(i + 1).zfill(3)}"
        role = ROLES[i % len(ROLES)]

        # Quotas vary by role, with some outliers
        if role == "AE":
            quota = random.choice([800000, 1000000, 1200000, 1500000, 0])  # 0 = not set yet
        elif role == "SDR":
            quota = random.choice([400000, 500000, 600000, 750000])
        elif role == "Manager":
            quota = random.choice([2000000, 2500000, 3000000])
        elif role == "SE":
            quota = random.choice([600000, 800000, 1000000, 0])
        else:
            quota = random.choice([300000, 400000, 500000])

        plan_id = random.choice(["saas_ae_plan", "saas_sdr_plan", "mgmt_plan", ""])

        # Messy effective dates — some missing, some in wrong format
        if random.random() < 0.1:
            eff_from = ""
        elif random.random() < 0.15:
            eff_from = "2026/01/01"  # wrong separator
        else:
            eff_from = "2026-01-01"

        eff_to = "2027-12-31" if random.random() > 0.2 else ""

        # Messy emails
        if random.random() < 0.05:
            email = ""
        elif random.random() < 0.1:
            email = f"{fn.lower()}.{ln.lower()}@company"  # missing TLD
        else:
            email = f"{fn.lower()}.{ln.lower()}@company.com"

        # Manager hierarchy — managers manage groups of 3-6 reps
        manager_id = ""
        if role in ("AE", "SDR", "SE", "CSM"):
            mgr_idx = ((i // 5) % 8) + 31  # managers are indices 31-38
            manager_id = f"P{str(mgr_idx).zfill(3)}"

        # Ramp for ~20% of new hires
        ramp_months = ""
        ramp_schedule = ""
        if random.random() < 0.2:
            ramp_months = str(random.choice([3, 4, 6]))
            ramp_schedule = "0.25 0.50 0.75 1.0"

        # Category quotas (some AE/SE have product-specific quotas)
        cat_quotas = "{}"
        if role in ("AE", "SE") and random.random() < 0.3:
            enterprise_q = random.choice([300000, 500000, 750000])
            cat_quotas = f'{{"Enterprise": {enterprise_q}}}'

        # Manager override (some managers get a cut of team deals)
        mgr_override = ""
        if role == "Manager" and random.random() > 0.3:
            mgr_override = f"{random.choice([0.02, 0.03, 0.05, 0.07])}"

        team_id = ""
        if role == "Manager":
            team_id = f"Team-{chr(65 + (i - 31) % 8)}"

        payees.append({
            "id": pid,
            "name": name,
            "quota": str(quota),
            "plan_id": plan_id,
            "effective_from": eff_from,
            "effective_to": eff_to,
            "email": email,
            "ramp_months": ramp_months,
            "ramp_schedule": ramp_schedule,
            "category_quotas": cat_quotas,
            "manager_id": manager_id,
            "manager_override": mgr_override,
            "team_id": team_id,
        })
    return payees


# ---------------------------------------------------------------------------
# Transactions — 350 deals over 6 months, deliberately messy
# ---------------------------------------------------------------------------
PRODUCTS = ["Enterprise", "Pro", "Starter", "Enterprise", "Pro", "Add-On", ""]
DEAL_SIZES = {
    "Enterprise": (50000, 300000),
    "Pro": (10000, 80000),
    "Starter": (2000, 15000),
    "Add-On": (500, 5000),
}
PERIODS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]

def make_transactions(payees: list[dict[str, str]]) -> list[dict[str, str]]:
    txns = []
    payee_pool = [p for p in payees if p["plan_id"] != ""]  # only assigned payees
    for i in range(350):
        txn_id = f"TXN-{str(i + 1).zfill(5)}"
        payee = random.choice(payee_pool)
        deal_id = f"DEAL-{str(random.randint(1000, 9999)).zfill(4)}"

        period = random.choice(PERIODS)
        product = random.choice(PRODUCTS)
        lo, hi = DEAL_SIZES.get(product, (1000, 10000))
        amount = round(random.uniform(lo, hi), 2)

        # Messiness: ~5% negative amounts (clawbacks/refunds)
        if random.random() < 0.05:
            amount = -round(random.uniform(500, 20000), 2)

        # Messiness: ~8% deals with missing/inconsistent dates
        if random.random() < 0.08:
            close_date = ""
        elif random.random() < 0.1:
            close_date = f"2026-{random.randint(1,6):02d}-{random.randint(1,28):02d}"  # won't match period month
        else:
            # Date matches period month
            close_date = f"{period}-{str(random.randint(1, 28)).zfill(2)}"

        # Some deals have metadata JSON
        metadata = "{}"
        if random.random() < 0.15:
            metadata = f'{{"contract_length": {random.choice([12, 24, 36])}, "renewal": {random.choice(["true", "false"])}}}'

        # ~3% duplicate deal IDs (edge case)
        if random.random() < 0.03 and i > 50:
            deal_id = txns[random.randint(0, i - 1)]["deal_id"]

        txns.append({
            "id": txn_id,
            "payee_id": payee["id"],
            "deal_id": deal_id,
            "period": period,
            "amount": str(amount),
            "product": product,
            "close_date": close_date,
            "metadata": metadata,
        })
    return txns


# ---------------------------------------------------------------------------
# Write CSVs
# ---------------------------------------------------------------------------
def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"  Wrote {len(rows)} rows -> {path.name}")


def main() -> None:
    print("Generating large messy datasets...\n")

    payees = make_payees()
    txns = make_transactions(payees)

    # Payees
    write_csv(OUT / "payees.csv", payees, [
        "id", "name", "quota", "plan_id", "effective_from", "effective_to",
        "email", "ramp_months", "ramp_schedule", "category_quotas",
        "manager_id", "manager_override", "team_id",
    ])

    # Transactions
    write_csv(OUT / "transactions.csv", txns, [
        "id", "payee_id", "deal_id", "period", "amount", "product",
        "close_date", "metadata",
    ])

    # Plan YAML
    plan_yaml = """# SaaS AE Commission Plan — tiered with accelerators
plan_id: saas_ae_plan
name: "SaaS AE Plan"
period_type: monthly
currency: USD

# Tiered rates: higher attainment = higher commission rate
# Also includes product-specific SPIFs
rules:
  - id: R-001
    type: tiered_rate
    description: "Core commission — tiers based on quota attainment"
    tiers:
      - threshold_pct: 0.0
        rate: 0.05        # 5% on first 50% of quota
      - threshold_pct: 0.5
        rate: 0.08        # 8% on 50-100% of quota
      - threshold_pct: 1.0
        rate: 0.12        # 12% above 100% quota (accelerator)

  - id: R-002
    type: flat_rate
    description: "Enterprise deal bonus"
    filter: "product == 'Enterprise'"
    rate: 0.02            # Extra 2% on Enterprise deals

  - id: R-003
    type: flat_rate
    description: "Multi-year contract bonus"
    filter: "deal_id in multi_year_deals"
    rate: 0.01            # Extra 1% for multi-year deals
"""
    (OUT / "saas_ae_plan.yaml").write_text(plan_yaml)
    print(f"  Wrote plan -> saas_ae_plan.yaml")

    # SDR plan
    sdr_yaml = """# SaaS SDR Commission Plan — flat rate + meeting bonus
plan_id: saas_sdr_plan
name: "SaaS SDR Plan"
period_type: monthly
currency: USD

rules:
  - id: R-101
    type: flat_rate
    description: "Flat commission on all qualified meetings"
    rate: 0.03

  - id: R-102
    type: threshold_gate
    description: "Bonus for exceeding 20 meetings/month"
    threshold_pct: 0.0
    multiplier: 1.5
    rate: 0.03
"""
    (OUT / "sdr_plan.yaml").write_text(sdr_yaml)
    print(f"  Wrote plan -> sdr_plan.yaml")

    # Manager plan
    mgr_yaml = """# Manager Override Plan
plan_id: mgmt_plan
name: "Management Plan"
period_type: monthly
currency: USD

rules:
  - id: R-201
    type: flat_rate
    description: "Team override — paid as manager_override on payee record"
    rate: 0.0       # rate comes from manager_override field
"""
    (OUT / "mgmt_plan.yaml").write_text(mgr_yaml)
    print(f"  Wrote plan -> mgmt_plan.yaml")

    # Summary
    assigned = sum(1 for p in payees if p["plan_id"])
    quotas_zero = sum(1 for p in payees if p["quota"] in ("0", ""))
    neg_txns = sum(1 for t in txns if float(t["amount"]) < 0)
    print(f"\nDone. Output: {OUT}/")
    print(f"  Payees: {len(payees)} total, {assigned} with plan, {quotas_zero} with zero/missing quota")
    print(f"  Transactions: {len(txns)} total, {neg_txns} negative (clawbacks)")
    print(f"  Plans: saas_ae_plan.yaml, sdr_plan.yaml, mgmt_plan.yaml")


if __name__ == "__main__":
    main()
