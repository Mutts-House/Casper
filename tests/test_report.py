import unittest
from datetime import datetime
from casper_report import segment_customers, weekday_lift

def tx(cid, stamp, category="beverages", amount="5", fuel="false"):
    return {"customer_id":cid,"timestamp":stamp,"category":category,"amount":amount,"is_fuel":fuel}

class ReportTests(unittest.TestCase):
    def test_priority_and_overlap_are_reported(self):
        rows = [tx("a", f"2025-01-{d:02d}T12:00", "food service") for d in (1,2,3,4,5,6,7,8)]
        rows += [tx("a", f"2025-01-{d:02d}T08:00", "fuel", fuel="true") for d in (1,2,3,4,5,6,7,8)] * 2
        assignments, overlaps, _ = segment_customers(rows, datetime(2025,1,8).date())
        self.assertEqual(assignments["a"], "P1")
        self.assertEqual(overlaps, 1)

    def test_lift_uses_same_weekday_transaction_count(self):
        rows = []
        for day, count in [(1, 2), (8, 4), (15, 8)]:
            rows.extend(tx("x", f"2025-01-{day:02d}T10:00") for _ in range(count))
        lift = weekday_lift(rows)
        self.assertEqual(lift[datetime(2025,1,15).date()]["baseline"], 3)
        self.assertAlmostEqual(lift[datetime(2025,1,15).date()]["lift"], 8/3-1)

if __name__ == "__main__":
    unittest.main()
