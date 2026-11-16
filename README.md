# Casper Buyer Personas

Generate a self-contained `report.html` from convenience-store transaction data.

## Run

The project uses only the Python standard library (Python 3.9+).

```bash
python generate_data.py --output sample_data
python casper_report.py --input-dir sample_data --output report.html
python -m unittest discover -s tests -v
```

Inputs are `transactions.csv`, optional `survey.csv`, `trends.csv`, and
`margins.csv`. The generator creates about 5,000 transactions across six
months. Segmentation priority, overlap counts, same-weekday transaction-count
lift, unidentified transactions, survey sample sizes, weak signals, and the
placeholder-margin warning are included in the report.