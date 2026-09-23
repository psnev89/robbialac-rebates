"""Pure data logic for the Robbialac rebates app.

No Streamlit imports here: parsing, normalization and aggregation stay
unit-testable and reusable outside the UI.
"""
from __future__ import annotations

import math
import re
from typing import IO, Union

import pandas as pd

Source = Union[str, IO[bytes]]

NAME_COL = "nome"
PRICE_COL = "preco"


def normalize_price(value) -> float:
    """Turn a price cell into a float. Missing/empty values become 0.0.

    Handles numbers as-is and strings such as '58.19 €', '58,19€' or
    '1.234,56 €' (pt-PT thousands separator).
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return 0.0 if (isinstance(value, float) and math.isnan(value)) else float(value)
    s = re.sub(r"(?i)euros?|eur|[€$]", "", str(value)).replace("\xa0", " ")
    s = re.sub(r"\s+", "", s)
    if not s:
        return 0.0
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _norm_key(text) -> str:
    """Canonical key for matching product names across files."""
    return re.sub(r"\s+", " ", str(text).replace("\xa0", " ")).strip().casefold()


def _find_cell(raw: pd.DataFrame, pattern: str, max_rows: int = 10):
    """First cell (row, col) matching `pattern` near the top of the sheet."""
    for r in range(min(max_rows, len(raw))):
        for c in range(raw.shape[1]):
            v = raw.iat[r, c]
            if isinstance(v, str) and re.search(pattern, v, re.IGNORECASE):
                return r, c
    return None


def _locate_columns(raw: pd.DataFrame):
    """Return (name_col, price_col, first_data_row) for a catalog sheet.

    Supports two layouts:
    1. A proper header row containing NOME and PRECO cells.
    2. The 'Premios' layout: 'Preço ...' marks the price column and
       'DESCRIÇÃO' sits one column right of the product name.
    """
    for r in range(min(10, len(raw))):
        labels = {
            re.sub(r"\s+", " ", str(v)).strip().lower(): c
            for c, v in enumerate(raw.iloc[r])
            if isinstance(v, str)
        }
        name_c = next((c for k, c in labels.items() if k == "nome"), None)
        price_c = next(
            (c for k, c in labels.items() if re.fullmatch(r"pre[cç]o.*", k)), None
        )
        if name_c is not None and price_c is not None:
            return name_c, price_c, r + 1

    price = _find_cell(raw, r"\bpre[cç]o\b")
    desc = _find_cell(raw, r"\bdescri")
    if price and desc and desc[1] > 0:
        return desc[1] - 1, price[1], max(price[0], desc[0]) + 1
    raise ValueError(
        "Não foi possível localizar as colunas de nome e preço no catálogo."
    )


def load_catalog(source: Source) -> pd.DataFrame:
    """Read the prizes catalog into a DataFrame with [nome, preco]."""
    raw = pd.read_excel(source, header=None)
    name_c, price_c, start = _locate_columns(raw)
    df = raw.iloc[start:, [name_c, price_c]].copy()
    df.columns = [NAME_COL, PRICE_COL]
    df[NAME_COL] = df[NAME_COL].map(
        lambda v: re.sub(r"\s+", " ", str(v).replace("\xa0", " ")).strip()
        if pd.notna(v)
        else None
    )
    df = df[df[NAME_COL].notna() & (df[NAME_COL] != "")]
    df[PRICE_COL] = df[PRICE_COL].map(normalize_price)
    return df.drop_duplicates(subset=NAME_COL, keep="first").reset_index(drop=True)


def load_ofertas(source: Source) -> pd.Series:
    """Extract the cleaned 'Oferta' column from a rebates export."""
    df = pd.read_excel(source)
    col = next((c for c in df.columns if _norm_key(c) == "oferta"), None)
    if col is None:
        raise ValueError("Coluna 'Oferta' não encontrada no ficheiro de rebates.")
    return df[col].dropna().map(
        lambda v: re.sub(r"\s+", " ", str(v).replace("\xa0", " ")).strip()
    )


def summarize(ofertas: pd.Series, catalog: pd.DataFrame) -> pd.DataFrame:
    """Aggregate ofertas into [oferta, quantidade, preco_unit, total, matched].

    Matching against the catalog is whitespace/case-insensitive. Ofertas with
    no catalog entry are kept with price 0 and matched=False so the UI can
    flag them instead of silently undercounting.
    """
    price_map = {
        _norm_key(n): p for n, p in zip(catalog[NAME_COL], catalog[PRICE_COL])
    }
    rows = []
    for oferta, qty in ofertas.value_counts().items():
        price = price_map.get(_norm_key(oferta))
        rows.append(
            {
                "oferta": oferta,
                "quantidade": int(qty),
                "preco_unit": round(price, 2) if price is not None else 0.0,
                "total": round((price or 0.0) * qty, 2),
                "matched": price is not None,
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values("total", ascending=False)
        .reset_index(drop=True)
    )
