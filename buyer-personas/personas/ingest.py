"""Load sales data from CSV, Excel, SQL, and PDF tables into one canonical frame.

Output columns (canonical): transaction_id, timestamp, customer_key, product,
category (normalized), quantity, amount, source. Raw email and phone values
are never kept: they are hashed into customer_key on load.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pandas as pd

from .config import CATEGORY_RULES, COLUMN_ALIASES, DEFAULT_CATEGORY

CANONICAL_COLUMNS = [
    "transaction_id", "timestamp", "customer_key", "product",
    "category", "quantity", "amount", "source",
]


def _norm(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(name).strip().lower())


def load_mapping(path: str | None) -> dict[str, list[str]]:
    """Return column aliases, merged with a user JSON mapping if given.

    JSON format: {"amount": ["Net Price"], "timestamp": ["Sale Date"]}
    """
    mapping = {k: list(v) for k, v in COLUMN_ALIASES.items()}
    if path:
        custom = json.loads(Path(path).read_text())
        for canon, aliases in custom.items():
            mapping[canon] = list(aliases) + mapping.get(canon, [])
    return mapping


def hash_identifier(value: str) -> str:
    """One-way hash so the stored key cannot be read back to an email or phone."""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()[:16]


def normalize_category(raw: object) -> str:
    text = str(raw or "").lower()
    if not text or text == "nan":
        return DEFAULT_CATEGORY
    for label, keywords in CATEGORY_RULES:
        if any(k in text for k in keywords):
            return label
    return DEFAULT_CATEGORY


def _map_columns(df: pd.DataFrame, mapping: dict[str, list[str]]) -> pd.DataFrame:
    lookup = {_norm(c): c for c in df.columns}
    out = pd.DataFrame(index=df.index)
    for canon, aliases in mapping.items():
        for alias in aliases:
            src = lookup.get(_norm(alias))
            if src is not None:
                out[canon] = df[src]
                break
    if "timestamp" not in out or "amount" not in out:
        missing = [c for c in ("timestamp", "amount") if c not in out]
        raise ValueError(
            f"Required column(s) not found: {missing}. "
            f"Columns in file: {list(df.columns)}. Add aliases with --mapping."
        )
    return out


def _standardize(df: pd.DataFrame, source: str) -> pd.DataFrame:
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["amount"] = pd.to_numeric(
        df["amount"].astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce"
    )
    df = df.dropna(subset=["timestamp", "amount"])
    df = df[df["amount"] > 0]  # drop voids/refunds from the behavioral view

    for col in ("customer_id", "email", "phone"):
        if col not in df:
            df[col] = None

    def key_for(row) -> str | None:
        for col in ("customer_id", "email", "phone"):
            val = row[col]
            if pd.notna(val) and str(val).strip():
                return hash_identifier(str(val))
        return None

    df["customer_key"] = df.apply(key_for, axis=1)
    # Category comes from the category column; when it is missing or blank, use the product name.
    raw_cat = df["category"] if "category" in df else pd.Series(None, index=df.index, dtype=object)
    raw_prod = df["product"] if "product" in df else pd.Series(None, index=df.index, dtype=object)
    combined = raw_cat.where(raw_cat.notna() & (raw_cat.astype(str).str.strip() != ""), raw_prod)
    df["category"] = combined.map(normalize_category)
    if "transaction_id" not in df or df["transaction_id"].isna().all():
        df["transaction_id"] = [f"{source}-{i}" for i in range(len(df))]
    if "product" not in df:
        df["product"] = ""
    if "quantity" not in df:
        df["quantity"] = 1
    df["source"] = source
    return df[CANONICAL_COLUMNS]


def load_file(path: str, mapping: dict[str, list[str]]) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        raw = pd.read_excel(p, dtype=object)
    elif p.suffix.lower() == ".csv":
        try:
            raw = pd.read_csv(p, dtype=object)
        except UnicodeDecodeError:
            raw = pd.read_csv(p, dtype=object, encoding="latin-1")
    else:
        raise ValueError(f"Unsupported file type: {p.suffix}")
    return _standardize(_map_columns(raw, mapping), source=p.name)


def load_sql(db_path: str, query: str, mapping: dict[str, list[str]]) -> pd.DataFrame:
    """Read from a SQLite file. For Postgres/MySQL, export to CSV or pass a
    SQLAlchemy engine via load_sql_engine()."""
    with sqlite3.connect(db_path) as conn:
        raw = pd.read_sql_query(query, conn, dtype=object)
    return _standardize(_map_columns(raw, mapping), source=f"sql:{Path(db_path).name}")


def load_sql_engine(url: str, query: str, mapping: dict[str, list[str]]) -> pd.DataFrame:
    try:
        from sqlalchemy import create_engine
    except ImportError as exc:
        raise RuntimeError("Install sqlalchemy to use --db-url") from exc
    engine = create_engine(url)
    raw = pd.read_sql_query(query, engine, dtype=object)
    return _standardize(_map_columns(raw, mapping), source="sql:engine")


def load_pdf(path: str, mapping: dict[str, list[str]]) -> pd.DataFrame:
    """Extract tables from a PDF (receipt reports, POS summaries).

    Requires pdfplumber. The first row of each table is treated as headers.
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("Install pdfplumber to read PDF tables") from exc
    frames = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if len(table) < 2:
                    continue
                header, *rows = table
                frames.append(pd.DataFrame(rows, columns=header))
    if not frames:
        raise ValueError(f"No tables found in {path}")
    raw = pd.concat(frames, ignore_index=True)
    return _standardize(_map_columns(raw, mapping), source=Path(path).name)


def load_survey(path: str) -> pd.DataFrame:
    """Optional stated-data survey. Must include one of customer_id / email / phone.

    Every other column is treated as a self-reported (stated) attribute,
    for example age_band, household, or weekly_routine.
    """
    p = Path(path)
    df = pd.read_excel(p, dtype=object) if p.suffix.lower().startswith(".xls") else pd.read_csv(p, dtype=object)
    lookup = {_norm(c): c for c in df.columns}

    # Same identifier priority as the POS loader (customer_id, then email, then phone),
    # so a survey row links to the same hashed key that the transactions use.
    id_cols = []
    for canon in ("customer_id", "email", "phone"):
        for alias in COLUMN_ALIASES[canon]:
            if _norm(alias) in lookup:
                id_cols.append(lookup[_norm(alias)])
                break
    if not id_cols:
        raise ValueError("Survey needs a customer_id, email, or phone column to link responses.")

    def key_for(row) -> str | None:
        for col in id_cols:
            if pd.notna(row[col]) and str(row[col]).strip():
                return hash_identifier(str(row[col]))
        return None

    out = pd.DataFrame(index=df.index)
    out["customer_key"] = df.apply(key_for, axis=1)
    for col in df.columns:
        if col not in id_cols:
            out[col] = df[col]
    return out.dropna(subset=["customer_key"])
