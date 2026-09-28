"""Pure data logic for the Robbialac rebates app.

No Streamlit imports here: parsing, normalization and aggregation stay
unit-testable and reusable outside the UI.
"""
from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import IO, Union

import pandas as pd

Source = Union[str, IO[bytes]]

NAME_COL = "nome"
PRICE_COL = "preco"
NET_COL = "preco_s_iva"
VAT_COL = "iva_unit"
VAT_RATE_COL = "taxa_iva"
DEFAULT_VAT_RATE = 23.0
CATALOG_COLUMNS = [NAME_COL, NET_COL, VAT_RATE_COL, VAT_COL, PRICE_COL]


def round_money(value) -> float:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise ValueError("Introduz um valor monetário finito.")
        return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise ValueError("Introduz um valor monetário válido.") from None


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


def _parse_amount(value) -> float:
    try:
        amount = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError("Introduz um valor numérico válido.") from None
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("O valor deve ser finito e não negativo.")
    return round_money(amount)


def _parse_vat_rate(value) -> float:
    rate = _parse_amount(str(value).strip().removesuffix("%"))
    if rate > 100:
        raise ValueError("A taxa de IVA deve estar entre 0 e 100%.")
    return rate


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
            (c for k, c in labels.items() if re.fullmatch(r"pre[cç]o(?: total.*| c/ iva.*)?", k)),
            None,
        )
        if price_c is None:
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


def clean_catalog(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize names, gross prices and product rates, dropping duplicate names.

    Shared by load_catalog (fresh files) and the UI (edited tables), so an
    editor-produced df is held to the same rules as a loaded one.
    """
    out = df.copy()
    out[NAME_COL] = out[NAME_COL].map(
        lambda v: re.sub(r"\s+", " ", str(v).replace("\xa0", " ")).strip()
        if pd.notna(v)
        else None
    )
    out = out[out[NAME_COL].notna() & (out[NAME_COL] != "")]
    out = out.loc[~out[NAME_COL].map(_norm_key).duplicated()].reset_index(drop=True)
    return add_price_vat(out)


def load_catalog(source: Source) -> pd.DataFrame:
    """Read gross catalog prices and optional per-product VAT percentages."""
    with pd.ExcelFile(source, engine="openpyxl") as workbook:
        raw = pd.read_excel(workbook, header=None)
        name_c, price_c, start = _locate_columns(raw)
        df = raw.iloc[start:, [name_c, price_c]].copy()
        df.columns = [NAME_COL, PRICE_COL]
        labels = {_norm_key(v): c for c, v in enumerate(raw.iloc[start - 1])}
        rate_c = next(
            (labels[label] for label in ("taxa iva (%)", "taxa de iva (%)", "iva (%)", "taxa_iva") if label in labels),
            None,
        )
        if rate_c is not None:
            cells = workbook.book.worksheets[0].iter_rows(
                min_row=start + 1, max_row=len(raw), min_col=rate_c + 1, max_col=rate_c + 1,
            )
            df[VAT_RATE_COL] = [
                cell.value * 100
                if isinstance(cell.value, (int, float)) and "%" in cell.number_format
                else cell.value
                for (cell,) in cells
            ]
    return clean_catalog(df)


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
    """Aggregate offers using each product's gross price, net price and VAT rate.

    Matching against the catalog is whitespace/case-insensitive. Ofertas with
    no catalog entry are kept with price 0 and matched=False so the UI can
    flag them instead of silently undercounting.
    """
    prices = add_price_vat(catalog)
    price_map = {_norm_key(row[NAME_COL]): row for row in prices.to_dict("records")}
    rows = []
    for oferta, qty in ofertas.value_counts().items():
        product = price_map.get(_norm_key(oferta))
        price = product[PRICE_COL] if product is not None else 0.0
        if isinstance(price, float) and math.isnan(price):
            price = 0.0  # in catalog but no price: matched at 0 €
        rows.append(
            {
                "oferta": oferta,
                "quantidade": int(qty),
                "preco_unit": price,
                "preco_unit_s_iva": product[NET_COL] if product is not None else 0.0,
                VAT_RATE_COL: product[VAT_RATE_COL] if product is not None else 0.0,
                "total": round_money(price * qty),
                "matched": product is not None,
            }
        )
    return (
        pd.DataFrame(rows, columns=[
            "oferta", "quantidade", "preco_unit", "preco_unit_s_iva", VAT_RATE_COL, "total", "matched",
        ])
        .sort_values("total", ascending=False)
        .reset_index(drop=True)
    )


def add_vat_split(df: pd.DataFrame, threshold: float = 50.0) -> pd.DataFrame:
    """Use gross unit cost above the limit, net unit cost at or below it.

    The threshold affects rebate costs only; catalog prices and rates never
    change. The unit decision is made before multiplying by quantity.
    """
    threshold = _parse_amount(threshold)
    out = df.copy()
    includes_vat = out["preco_unit"] > threshold
    out["preco_aplicado"] = out["preco_unit"].where(includes_vat, out["preco_unit_s_iva"])
    out["criterio"] = includes_vat.map({True: "Com IVA", False: "Sem IVA"})
    out["total_s_iva"] = (out["preco_unit_s_iva"] * out["quantidade"]).map(round_money)
    unit_vat = (out["preco_unit"] - out["preco_unit_s_iva"]).where(includes_vat, 0.0)
    out["iva"] = (unit_vat * out["quantidade"]).map(round_money)
    out["total"] = (out["preco_aplicado"] * out["quantidade"]).map(round_money)
    return out.sort_values("total", ascending=False).reset_index(drop=True)


def add_price_vat(df: pd.DataFrame) -> pd.DataFrame:
    """Derive net price and VAT from the gross price and each product's rate."""
    out = df.copy()
    out[PRICE_COL] = out[PRICE_COL].map(normalize_price).map(_parse_amount)
    if VAT_RATE_COL not in out:
        out[VAT_RATE_COL] = DEFAULT_VAT_RATE
    out[VAT_RATE_COL] = out[VAT_RATE_COL].map(
        lambda rate: DEFAULT_VAT_RATE if pd.isna(rate) or rate == "" else _parse_vat_rate(rate)
    )
    out[NET_COL] = [
        round_money(Decimal(str(gross)) / (1 + Decimal(str(rate)) / 100))
        for gross, rate in zip(out[PRICE_COL], out[VAT_RATE_COL])
    ]
    out[VAT_COL] = (out[PRICE_COL] - out[NET_COL]).map(round_money)
    return out[CATALOG_COLUMNS]


def edit_catalog_row(df: pd.DataFrame, row: int, column: str, value) -> pd.DataFrame:
    out = add_price_vat(df)
    if column not in (NAME_COL, NET_COL, PRICE_COL, VAT_RATE_COL) or row not in out.index:
        raise ValueError("Produto ou coluna não editável.")
    if column == NAME_COL:
        name = re.sub(r"\s+", " ", str(value or "")).strip()
        if not name:
            raise ValueError("O nome do produto não pode ficar vazio.")
        if _norm_key(name) in set(out.drop(index=row)[NAME_COL].map(_norm_key)):
            raise ValueError("Já existe um produto com esse nome.")
        out.at[row, NAME_COL] = name
        return out
    if column == VAT_RATE_COL:
        out.at[row, VAT_RATE_COL] = _parse_vat_rate(value)
    elif column == NET_COL:
        net = Decimal(str(_parse_amount(value)))
        rate = Decimal(str(out.at[row, VAT_RATE_COL]))
        out.at[row, PRICE_COL] = round_money(net * (1 + rate / 100))
    else:
        out.at[row, PRICE_COL] = _parse_amount(value)
    return add_price_vat(out)


def add_missing(catalog: pd.DataFrame, names) -> pd.DataFrame:
    """Return catalog + new rows (price 0.0, default VAT rate) for missing names.

    Comparison uses the same normalized key as rebate matching, so an
    oferta is never added twice under different casing/spacing.
    """
    existing = {_norm_key(n) for n in catalog[NAME_COL]}
    new = []
    for n in names:
        key = _norm_key(n)
        if key not in existing:
            existing.add(key)
            new.append(n)
    if not new:
        return catalog
    extra = pd.DataFrame({NAME_COL: new, PRICE_COL: 0.0, VAT_RATE_COL: DEFAULT_VAT_RATE})
    return pd.concat([catalog, extra], ignore_index=True)
