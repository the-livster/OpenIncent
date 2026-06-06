"""Generate messy demo transactions. Run once: python _gen.py"""
import csv, random, sys
from decimal import Decimal
from pathlib import Path

random.seed(42)

products = ["Enterprise", "Pro", "Starter", "New Logo", "Renewal", "Add-on"]
payees = {
    "alice": 28, "bob": 22, "charlie": 5, "dave": 15,
    "eve": 12, "frank": 0,  # zero after mid-March
    "hector": 8, "grace": 0, "isabel": 5, "jordan": 5,
}

months = ["2026-04", "2026-05", "2026-06"]
rows = []
tid = 1

for month in months:
    n_deals = random.randint(25, 35)
    for _ in range(n_deals):
        pid = random.choices(list(payees.keys()), weights=list(payees.values()), k=1)[0]
        if pid == "frank":
            continue
        product = random.choice(products)
        if product in ("Enterprise", "New Logo"):
            amt = Decimal(str(random.randint(5000, 85000)))
        elif product == "Pro":
            amt = Decimal(str(random.randint(1500, 25000)))
        else:
            amt = Decimal(str(random.randint(200, 8000)))
        day = random.randint(1, 28)
        close = f"2026-{month[5:]}-{day:02d}"

        rows.append({
            "id": f"D-{tid:04d}",
            "payee_id": pid,
            "deal_id": f"OPP-{tid:04d}",
            "period": month,
            "amount": str(amt),
            "product": product,
            "close_date": close,
        })
        tid += 1

# Split deals
splits = [
    ("alice", "charlie", "0.7", "0.3", "Enterprise", "85000"),
    ("dave", "grace", "0.6", "0.4", "New Logo", "42000"),
    ("bob", "charlie", "0.8", "0.2", "Pro", "18000"),
    ("eve", "grace", "0.5", "0.5", "Enterprise", "55000"),
    ("isabel", "jordan", "0.6", "0.4", "Enterprise", "35000"),
]

for p1, p2, s1, s2, prod, amt in splits:
    for month in random.sample(months, 2):
        day = random.randint(1, 28)
        rows.append({
            "id": f"D-{tid:04d}",
            "payee_id": p1,
            "deal_id": f"OPP-{tid:04d}",
            "period": month,
            "amount": amt,
            "product": prod,
            "close_date": f"2026-{month[5:]}-{day:02d}",
            "credits": f"{p1}:{s1}:split|{p2}:{s2}:split",
        })
        tid += 1

# Add SE overlays on Enterprise deals
overlay_deals = [r for r in rows if r.get("product") == "Enterprise" and not r.get("credits")]
for r in random.sample(overlay_deals, min(8, len(overlay_deals))):
    existing = r.get("credits", "")
    overlay = "grace:0.15:overlay"
    if existing:
        r["credits"] = existing + "|" + overlay
    else:
        r["credits"] = f'{r["payee_id"]}:1:split|{overlay}'

random.shuffle(rows)

out = Path(__file__).parent / "transactions.csv"
with open(out, "w", newline="") as f:
    fieldnames = ["id", "payee_id", "deal_id", "period", "amount", "product", "close_date", "credits"]
    w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)

print(f"Generated {len(rows)} deals -> {out}")
