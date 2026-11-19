"""Synthetic convenience-store data for testing and demos.

Generates customers with known behavior patterns so the segmentation can be
checked against ground truth. Every file is labeled SYNTHETIC. Do not use it
for business decisions.

Also embeds real sales effects on a few trend dates so the lift measurement
has something to find.
"""

from __future__ import annotations

import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

START = date(2026, 6, 1)
END = date(2026, 11, 14)

CATALOG = {
    "fuel": [("Unleaded 87 Gal", "Fuel", 52.0)],
    "beverage": [("Fresh Brew Coffee", "Coffee", 2.49), ("Energy Drink", "Energy Drinks", 3.29), ("Bottled Water", "Water", 1.79)],
    "snack": [("Potato Chips", "Snacks", 2.19), ("Chocolate Bar", "Candy", 1.59), ("Cookie Pack", "Bakery", 2.99)],
    "food_service": [("Hot Pizza Slice", "Hot Food", 3.49), ("Breakfast Sandwich", "Breakfast", 4.29), ("Chicken Taquito", "Hot Food", 2.99)],
    "restricted": [("Cigarettes Pack", "Tobacco", 9.75), ("Lottery Ticket", "Lottery", 5.00)],
}

# Customer archetypes: (name, count, id_type_weights, visit generator)
def _visits(kind: str, rng: random.Random) -> list[datetime]:
    days_total = (END - START).days
    out: list[datetime] = []

    def at(day: date, hour: int) -> datetime:
        return datetime.combine(day, datetime.min.time()) + timedelta(hours=hour, minutes=rng.randint(0, 59))

    if kind == "loyal":
        for _ in range(rng.randint(14, 22)):
            d = END - timedelta(days=rng.randint(0, 60))
            out.append(at(d, rng.choice([7, 8, 12, 13, 17, 18])))
    elif kind == "lapsing":
        # Regular in the 90 days ending 45 days before END, then gone.
        for _ in range(rng.randint(3, 6)):
            d = END - timedelta(days=rng.randint(45, 120))
            out.append(at(d, rng.choice([9, 12, 16, 19])))
    elif kind == "late_night":
        for _ in range(rng.randint(4, 8)):
            d = START + timedelta(days=rng.randint(0, days_total))
            out.append(at(d, rng.choice([22, 23, 0, 1])))
    elif kind == "fuel_commuter":
        for _ in range(rng.randint(6, 10)):
            d = START + timedelta(days=rng.randint(0, days_total))
            while d.weekday() >= 5:
                d += timedelta(days=1)
            out.append(at(min(d, END), rng.choice([6, 7, 8, 17, 18])))
    elif kind == "lunch":
        for _ in range(rng.randint(5, 9)):
            d = START + timedelta(days=rng.randint(0, days_total))
            while d.weekday() >= 5:
                d += timedelta(days=1)
            out.append(at(min(d, END), rng.choice([11, 12, 12, 13])))
    else:  # occasional
        for _ in range(rng.randint(1, 3)):
            d = START + timedelta(days=rng.randint(0, days_total))
            out.append(at(d, rng.randint(7, 20)))
    if kind in ("late_night", "fuel_commuter", "lunch"):
        recent = END - timedelta(days=rng.randint(0, 20))
        out.append(at(recent, 22 if kind == "late_night" else 12))
    return sorted(out)


def _basket(kind: str, rng: random.Random) -> list[tuple[str, str, str, float]]:
    """Return line items: (category, product, department, amount)."""
    items = []
    if kind == "fuel_commuter":
        items.append(("fuel", *CATALOG["fuel"][0][:2], round(rng.uniform(38, 62), 2)))
        if rng.random() < 0.6:
            p, dept, price = rng.choice(CATALOG["beverage"])
            items.append(("beverage", p, dept, price))
    elif kind == "lunch":
        p, dept, price = rng.choice(CATALOG["food_service"])
        items.append(("food_service", p, dept, price))
        if rng.random() < 0.5:
            p, dept, price = rng.choice(CATALOG["beverage"])
            items.append(("beverage", p, dept, price))
    elif kind == "late_night":
        p, dept, price = rng.choice(CATALOG["snack"])
        items.append(("snack", p, dept, price))
        p, dept, price = rng.choice(CATALOG["beverage"])
        items.append(("beverage", p, dept, price))
        if rng.random() < 0.3:
            p, dept, price = rng.choice(CATALOG["food_service"])
            items.append(("food_service", p, dept, price))
    else:
        cat = rng.choice(["beverage", "snack", "food_service", "restricted"])
        p, dept, price = rng.choice(CATALOG[cat])
        items.append((cat, p, dept, price))
    return items


def generate(out_dir: str, seed: int = 7) -> dict[str, str]:
    rng = random.Random(seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    plan = [
        ("loyal", 40), ("lapsing", 60), ("late_night", 50),
        ("fuel_commuter", 90), ("lunch", 60), ("occasional", 200),
    ]
    rows = []
    survey_rows = []
    truth_rows = []
    tx_counter = 1
    for kind, count in plan:
        for n in range(count):
            cid = f"{kind[:3].upper()}{n:04d}"
            id_type = rng.choices(["loyalty", "email", "phone"], weights=[0.6, 0.3, 0.1])[0]
            truth_rows.append({
                "archetype": kind,
                "loyalty_id": cid if id_type == "loyalty" else "",
                "email": f"{cid.lower()}@example.com" if id_type == "email" else "",
                "phone": f"206-555-{n:04d}" if id_type == "phone" else "",
            })
            if kind in ("loyal", "fuel_commuter") and n < 40:
                survey_rows.append({
                    "loyalty_id": cid if id_type == "loyalty" else "",
                    "email": f"{cid.lower()}@example.com" if id_type == "email" else "",
                    "age_band": rng.choice(["18-24", "25-34", "35-44", "45-54", "55+"]),
                    "household": rng.choice(["single", "couple", "family"]),
                    "weekly_routine": rng.choice(["commute", "shift work", "stay home", "student"]),
                })
            for ts in _visits(kind, rng):
                for cat, product, dept, amount in _basket(kind, rng):
                    rows.append({
                        "Receipt": f"R{tx_counter:07d}",
                        "Date Time": ts.strftime("%Y-%m-%d %H:%M"),
                        "Loyalty ID": cid if id_type == "loyalty" else "",
                        "Email": f"{cid.lower()}@example.com" if id_type == "email" else "",
                        "Phone": f"206-555-{n:04d}" if id_type == "phone" else "",
                        "Item": product,
                        "Department": dept,
                        "Qty": 1,
                        "Net Sales": f"{amount:.2f}",
                    })
                tx_counter += 1

    # Anonymous walk-in shoppers with no identifier at all.
    for _ in range(1500):
        ts = datetime.combine(START + timedelta(days=rng.randint(0, (END - START).days)), datetime.min.time()) \
            + timedelta(hours=rng.randint(6, 22), minutes=rng.randint(0, 59))
        cat = rng.choice(["beverage", "snack", "food_service", "fuel", "restricted"])
        if cat == "fuel":
            product, dept, price = CATALOG["fuel"][0]
            price = round(rng.uniform(30, 60), 2)
        else:
            product, dept, price = rng.choice(CATALOG[cat])
        rows.append({
            "Receipt": f"R{tx_counter:07d}", "Date Time": ts.strftime("%Y-%m-%d %H:%M"),
            "Loyalty ID": "", "Email": "", "Phone": "", "Item": product, "Department": dept,
            "Qty": 1, "Net Sales": f"{price:.2f}",
        })
        tx_counter += 1

    # Trend days with real sales effects baked in (what the lift test should find).
    trend_effects = {
        date(2026, 8, 16): [("snack", 40), ("beverage", 40)],     # local sports home game, late night
        date(2026, 9, 14): [("fuel", 35)],                       # gas price spike
        date(2026, 10, 31): [("snack", 35)],                     # Halloween
    }
    for d, extras in trend_effects.items():
        for cat, count in extras:
            for _ in range(count):
                hour = rng.choice([22, 23, 0]) if cat != "fuel" else rng.choice([7, 8, 17])
                ts = datetime.combine(d, datetime.min.time()) + timedelta(hours=hour % 24, minutes=rng.randint(0, 59))
                if cat == "fuel":
                    product, dept, price = CATALOG["fuel"][0]
                    price = round(rng.uniform(38, 62), 2)
                else:
                    product, dept, price = rng.choice(CATALOG[cat])
                rows.append({
                    "Receipt": f"R{tx_counter:07d}", "Date Time": ts.strftime("%Y-%m-%d %H:%M"),
                    "Loyalty ID": "", "Email": "", "Phone": "", "Item": product, "Department": dept,
                    "Qty": 1, "Net Sales": f"{price:.2f}",
                })
                tx_counter += 1

    truth_path = out / "ground_truth_demo.csv"
    with truth_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["archetype", "loyalty_id", "email", "phone"])
        w.writeheader()
        w.writerows(truth_rows)

    pos_path = out / "pos_export_demo.csv"
    with pos_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    survey_path = out / "survey_demo.csv"
    with survey_path.open("w", newline="") as f:
        cols = ["loyalty_id", "email", "age_band", "household", "weekly_routine"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in survey_rows:
            if r["loyalty_id"] or r["email"]:
                w.writerow(r)

    trends_path = out / "trends_demo.csv"
    with trends_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "scope", "topic", "tags", "source", "url"])
        w.writeheader()
        w.writerows([
            {"date": "2026-08-16", "scope": "local", "topic": "Local sports home game", "tags": "event;late night;nightlife", "source": "demo", "url": ""},
            {"date": "2026-09-14", "scope": "mainstream", "topic": "Gas price spike", "tags": "gas prices;commute", "source": "demo", "url": ""},
            {"date": "2026-10-31", "scope": "mainstream", "topic": "Halloween", "tags": "event;deal", "source": "demo", "url": ""},
            {"date": "2026-10-10", "scope": "local", "topic": "Week of heavy rain", "tags": "weather;commute", "source": "demo", "url": ""},
            {"date": "2026-07-04", "scope": "mainstream", "topic": "Fourth of July", "tags": "event;deal", "source": "demo", "url": ""},
        ])

    readme = out / "README.txt"
    readme.write_text(
        "SYNTHETIC DEMO DATA. Generated for testing the buyer persona software.\n"
        "Not real customers, not real sales. Do not use for business decisions.\n"
    )
    return {"pos": str(pos_path), "survey": str(survey_path), "trends": str(trends_path), "truth": str(truth_path)}
