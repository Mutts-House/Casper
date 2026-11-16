#!/usr/bin/env python3
"""Create a realistic, deterministic six-month demo data set."""
import csv, random
from datetime import datetime, timedelta
from pathlib import Path

def generate(out="sample_data", seed=7, n=5000):
    random.seed(seed); out = Path(out); out.mkdir(exist_ok=True)
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=180)
    customers = [f"C{i:04d}" for i in range(1, 401)]
    cats = [("fuel", .34), ("food service", .25), ("beverages", .22), ("snacks", .14), ("tobacco", .05)]
    with (out/"transactions.csv").open("w", newline="") as fh:
        w=csv.writer(fh); w.writerow(["transaction_id","customer_id","timestamp","category","amount","is_fuel"])
        for i in range(n):
            dt=start+timedelta(days=random.randrange(181),hours=random.randrange(24),minutes=random.randrange(60))
            customer=random.choice(customers) if random.random() > .12 else ""
            category=random.choices([x[0] for x in cats], weights=[x[1] for x in cats])[0]
            amount=round(random.uniform(8,65) if category=="fuel" else random.uniform(2,22),2)
            w.writerow([f"T{i:05d}",customer,dt.isoformat(timespec="minutes"),category,amount,category=="fuel"])
    with (out/"survey.csv").open("w", newline="") as fh:
        w=csv.writer(fh); w.writerow(["customer_id","age_band","household","weekly_routine"])
        for c in customers[:180]: w.writerow([c,random.choice(["18-24","25-34","35-54","55+"]),random.choice(["single","family","shared"]),random.choice(["commute","lunch","evening","weekend"])])
    with (out/"trends.csv").open("w", newline="") as fh:
        w=csv.writer(fh); w.writerow(["date","name","scope"])
        for i in range(6): w.writerow([(start+timedelta(days=30+i*20)).date(),"Local festival" if i%2 else "Game day","local"])
    with (out/"margins.csv").open("w", newline="") as fh:
        w=csv.writer(fh); w.writerow(["category","margin"])
        for c,_ in cats: w.writerow([c,.30])

if __name__ == "__main__":
    import argparse
    a=argparse.ArgumentParser(); a.add_argument("--output",default="sample_data"); args=a.parse_args(); generate(args.output)
