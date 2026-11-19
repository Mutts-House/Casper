# Buyer Personas: prototype

Turns your sales exports into named customer personas, measures whether local
and mainstream trends actually moved sales for each persona, and writes a
persona report with marketing actions for each one.

Built first for a franchised convenience store. Designed so the same engine can
serve other small retailers later.

## Quick start

```bash
pip install -r requirements.txt
python -m personas demo                   # runs on SYNTHETIC data, no setup needed
open output/report.html                   # macOS; use start/xdg-open elsewhere
```

## Run on your own data

```bash
python -m personas run \
  --input pos_export.csv \
  --input second_register.xlsx \
  --survey survey.csv \
  --trends trends.csv \
  --store "Main Store #1234" \
  --out output
```

Other sources:

| Source | Flag |
|---|---|
| SQLite database | `--sqlite pos.db --sql "SELECT * FROM sales"` |
| Postgres/MySQL | `--db-url postgresql://user:pass@host/db --sql "..."` (needs sqlalchemy) |
| PDF sales reports | `--pdf daily_summary.pdf` (needs pdfplumber; reads tables only) |

If your POS uses column names the tool doesn't recognize, the error lists the
columns it found. Map them with a JSON file:

```json
{"timestamp": ["Sale Date"], "amount": ["Net Price"], "customer_id": ["Member #"]}
```

Then pass `--mapping mapping.json`.

## What goes in and what comes out

**Inputs**
- Transactions (required): date/time, amount, and product or department. A loyalty ID, email, or phone is optional but needed to see repeat customers.
- Survey (optional): one row per respondent, keyed by loyalty ID, email, or phone. Every other column becomes a *stated* attribute, such as age band or household.
- Trends (optional): CSV with `date, scope, topic, tags, source, url`. Tags are semicolon-separated and match the playbook (e.g. `commute;gas prices`).

**Outputs** (in the output folder)
- `report.html`: one card per persona, plus trend-lift table. Opens in any browser.
- `personas.json`: the same data, machine-readable, for other tools.

## Provenance: what you can trust

Every persona attribute is tagged:

| Tag | Meaning | Confidence |
|---|---|---|
| observed | Measured from transactions | High |
| stated | Self-reported in a survey; shown only when 5+ people answered | Medium |
| inferred | Playbook assumption about why a segment behaves that way | Test it |

## Segments

Rules are plain Python in `personas/config.py` and `personas/segment.py`, in
priority order: Lapsing Regulars, Loyal Core, Late-Night Regulars,
Fuel-and-Go Commuters, Lunch Grab-and-Go, Occasional Visitors.

Rules were chosen over clustering so you can read why a customer landed where
they did. Thresholds live in `RULES` in `config.py`. Tune them with your data.

## Value: revenue vs. gross profit

Every persona card shows both revenue share and gross-profit share. Gross profit
is the value measure: a segment that buys mostly fuel can dominate revenue while
earning less per dollar than a segment buying food service.

The margin rates in `config.py` (`MARGIN_RATES`) are **placeholders**, not your
store's numbers. Replace them with your actual category margins:

```bash
python -m personas run --input pos.csv --margins margins.json
```

where `margins.json` looks like `{"fuel": 0.08, "food_service": 0.58}`.
Until you do, treat the gross-profit shares as directional only.

## Trend lift: how to read it

For each trend date, the tool compares that day's revenue to the usual revenue
for that weekday. It reports:

- **avg_lift_pct**: how far above or below normal the day was.
- **avg_z_score**: how unusual the day was, measured against normal day-to-day swings.
- **evidence**: `usable`-style flags:
  - `too few events`: fewer than 3 matching dates. Anecdote, not evidence.
  - `within normal variation`: the swing looks like ordinary noise.
  - `outside normal range`: the swing is at least 2 standard deviations. Still correlation, not proof of cause.

## AI plugins

The persona stories are written by a pluggable AI provider. The provider sees
only aggregate facts (segment sizes, averages, playbook text). Customer names,
IDs, and emails never leave the machine.

| Option | Use when |
|---|---|
| `--ai template` (default) | You want no AI and fully deterministic text |
| `--ai ollama:llama3.1` | You run a local model with Ollama |
| `--ai openai-compatible:model` | LM Studio, vLLM, or any OpenAI-style server (set `PERSONA_BASE_URL`) |
| `--ai anthropic:claude-haiku-5-5` | Cloud API (set `ANTHROPIC_API_KEY`) |
| `--ai file:ai_plugins/your_plugin.py` | Your own model or logic |

If a provider fails, the tool falls back to the template and records the reason
in the report. The pipeline never stops.

To write your own plugin, copy `ai_plugins/example_plugin.py` and define `PROVIDER`.

## Privacy and compliance

- Emails and phone numbers are hashed (SHA-256, truncated) on load. The raw values are not kept.
- Age-restricted categories (tobacco, lottery, alcohol) are tracked as their own category but are never used in offers or sent to an AI provider.
- Persona narratives are built from groups, not from identifiable individuals.
- Store the output folder like any other customer data. Before you send marketing, confirm that each contact opted in, and honor opt-outs. Check the rules that apply to you (e.g. CAN-SPAM for email, TCPA for SMS, and state privacy laws).

## Tests

```bash
python -m pytest -q tests
```

The accuracy test measures segmentation against synthetic ground truth. It is a
regression guard, not validation: the rules and the generator were designed
together. Real validation needs your store's data, plus a manual spot-check of
20 or so customers per segment.

## Known limits (prototype)

- Rules are hand-set. They need tuning against a real store's data.
- Trend feeds are CSV-based. Live connectors (events APIs, Google Trends) plug in through `trends.TrendSource`; none are built yet.
- Demographics come only from your survey or the ZIP-level data you add. Sales data cannot reveal age.
- One store per run. Multi-store and multi-tenant hosting are the next step if you sell this.
- Trend matching uses a 90-day window by default (`--trend-window`).
- Gross-profit shares rest on placeholder margins until you replace them.
- A single day of trend data is weak evidence. Trust lift results only after several events per topic.

## Layout

```
personas/
  config.py     canonical columns, categories, dayparts, RULES, PLAYBOOK
  ingest.py     CSV, Excel, SQLite, SQLAlchemy, PDF, survey loaders; hashing
  segment.py    customer features and rule-based segments
  trends.py     trend feeds, matching to segments, lift and z-score
  profile.py    persona cards with provenance tags; AI narrative step
  plugins.py    AI provider interface and built-in providers
  pipeline.py   end-to-end run
  report.py     self-contained HTML report
  demo.py       synthetic data generator
  cli.py        command line
tests/          pytest suite
ai_plugins/     example custom AI plugin
```

## B2B company scorecards

Scores companies you buy from or sell to on four separate measures. There is no
blended total, so every score can be audited against its inputs.

```bash
python -m personas b2b --input sample_data/b2b_invoices_demo.csv --out output_b2b
```

Invoice file columns: `company`, `invoice_date`, `amount` (required), and
optionally `due_date`, `paid_date`, `cost`, `disputed` (0/1).

| Score | Measures | Needs |
|---|---|---|
| Value | 12-month gross profit | `cost` (default margin used when missing, tagged inferred) |
| Reliability | Share of due invoices paid on time | `due_date`, `paid_date` (at least 3 due invoices) |
| Growth | 12-month spend vs prior 12 months | invoices from both periods |
| Concentration | Share of your 12-month spend (lower scores higher) | invoices |

Each score carries its raw inputs, the threshold used, a provenance tag
(observed or inferred), and a one-line reason. Output: `b2b_scorecards.json`
(full audit) and `b2b_scores.csv` (flat table).
