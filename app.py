from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

from catalog_editor import catalog_editor
from rebates import (
    DEFAULT_VAT_RATE,
    NAME_COL,
    USE_VAT_COL,
    VAT_RATE_COL,
    add_missing,
    add_price_vat,
    add_vat_split,
    clean_catalog,
    edit_catalog_row,
    load_catalog,
    load_ofertas,
    summarize,
)

CATALOG_DIR = Path(__file__).parent / "static"

EUR_SEPARATORS = str.maketrans(",.", ".,")
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def format_eur(value: float) -> str:
    return f"{value:,.2f} €".translate(EUR_SEPARATORS)


def default_catalog() -> Path | None:
    """Bundled catalog: prefer catalogo.xlsx, else any .xlsx in static/."""
    preferred = CATALOG_DIR / "catalogo.xlsx"
    if preferred.exists():
        return preferred
    candidates = sorted(CATALOG_DIR.glob("*.xlsx"))
    return candidates[0] if candidates else None


def to_xlsx_bytes(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()


st.set_page_config(page_title="Robbialac Rebates", page_icon="🎁", layout="wide")

LOGO = CATALOG_DIR / "logo.svg"
st.image(str(LOGO))

st.title("Custo rebates +Pinta")

if st.session_state.pop("catalog_imported", False):
    st.toast("Ofertas adicionadas ao catálogo — define os preços no separador Catálogo", icon="➕")


@st.cache_data
def get_catalog(path: str) -> pd.DataFrame:
    return load_catalog(path)


def _source_key(uploaded_file) -> tuple | None:
    if uploaded_file is not None:
        return ("upload", uploaded_file.name, getattr(uploaded_file, "file_id", uploaded_file.size))
    default_path = default_catalog()
    return ("default", str(default_path)) if default_path else None


# The catalog lives in session state: edits and auto-imported rows persist
# across reruns until a different source file is chosen. The uploader itself
# is rendered in the Catálogo tab but its value is readable here via its key.
st.session_state.setdefault("catalog_version", 0)
upload = st.session_state.get("catalog_upload")
src_key = _source_key(upload)
catalog_load_error = None
if src_key is not None and st.session_state.get("catalog_src") != src_key:
    try:
        loaded_catalog = load_catalog(upload) if upload is not None else get_catalog(src_key[1])
    except ValueError as error:
        catalog_load_error = str(error)
    else:
        st.session_state.catalog = loaded_catalog
        st.session_state.catalog_src = src_key
        st.session_state.catalog_version += 1
        st.session_state.catalog_selected = []
catalog = st.session_state.get("catalog")

# Each product's checkbox picks the rebate cost basis; it never changes prices.
# Sessions/catalogs saved before the checkbox existed initialize it once from
# the previous limit rule (gross > limit used the gross cost).
legacy_threshold = st.session_state.pop(
    "rebate_threshold", st.session_state.pop("iva_threshold", 50.0)
)
if catalog is not None:
    if VAT_RATE_COL not in catalog or USE_VAT_COL not in catalog:
        st.session_state.catalog_version += 1
    catalog = add_price_vat(catalog, legacy_threshold=legacy_threshold)
    st.session_state.catalog = catalog


def _apply_catalog_edits(response):
    """Apply a single grid event before rendering the updated rebates summary."""
    if response.get("selected") is not None:
        st.session_state.catalog_selected = response["selected"]
        return
    st.session_state.pop("catalog_error", None)
    try:
        if response["row"] == "new":
            if response["column"] != NAME_COL:
                return
            name = str(response["value"] or "").strip()
            if not name:
                raise ValueError("Introduz o nome do novo produto.")
            df = clean_catalog(add_missing(st.session_state.catalog, [name]))
            if len(df) == len(st.session_state.catalog):
                raise ValueError("Já existe um produto com esse nome.")
            df = add_price_vat(df)
        else:
            df = edit_catalog_row(
                st.session_state.catalog, int(response["row"]), response["column"], response["value"],
            )
    except ValueError as error:
        st.session_state.catalog_error = str(error)
        return
    # session catalog keeps gross prices and product rates; net and VAT are derived
    st.session_state.catalog = df


def _import_offers(names):
    st.session_state.catalog = add_missing(st.session_state.catalog, names)
    st.session_state.catalog_imported = True


def _delete_selected():
    selected = [int(row) for row in st.session_state.get("catalog_selected", [])]
    st.session_state.catalog = st.session_state.catalog.drop(index=selected).reset_index(drop=True)
    st.session_state.catalog_selected = []
    st.session_state.catalog_version += 1


tab_rebates, tab_catalog = st.tabs(["🎁 Rebates", "📋 Catálogo"])

with tab_rebates:
    with st.container(border=True):
        st.subheader("Upload Excel de rebates")
        rebates_file = st.file_uploader(
            "Ficheiro de rebates", type=["xlsx", "xls"], label_visibility="collapsed"
        )
        st.caption(
            "Cada produto usa o preço c/ IVA ou s/ IVA conforme a checkbox "
            "«Usar preço c/ IVA» no separador Catálogo."
        )

    if rebates_file is None:
        st.info("Carrega um ficheiro de rebates para ver o resumo.")
    elif catalog is None:
        st.error("Sem catálogo de prémios: carrega um ficheiro .xlsx no separador Catálogo.")
    else:
        try:
            ofertas = load_ofertas(rebates_file)
            summary = add_vat_split(summarize(ofertas, catalog))
        except ValueError as e:
            st.error(str(e))
            st.stop()

        totals_by_criterion = summary.groupby("criterio")["total"].sum()
        with st.container(horizontal=True):
            st.metric(
                "Total rebates s/ IVA",
                format_eur(totals_by_criterion.get("Sem IVA", 0.0)),
                help="Custo dos produtos com a checkbox «Usar preço c/ IVA» desmarcada, "
                "já multiplicado pelas quantidades resgatadas.",
            )
            st.metric(
                "Total rebates c/ IVA",
                format_eur(totals_by_criterion.get("Com IVA", 0.0)),
                help="Custo dos produtos com a checkbox «Usar preço c/ IVA» marcada, "
                "já multiplicado pelas quantidades resgatadas.",
            )
            st.metric("Total rebates", format_eur(summary["total"].sum()))
            st.metric("IVA incluído no custo", format_eur(summary["iva"].sum()))
            st.metric("Ofertas resgatadas", int(summary["quantidade"].sum()))

        summary_columns = [
            "oferta", "quantidade", VAT_RATE_COL, "preco_unit", "preco_aplicado",
            "criterio", "total_s_iva", "iva", "total",
        ]
        summary_labels = {
            "oferta": "Oferta",
            "quantidade": "Quantidade",
            VAT_RATE_COL: "Taxa IVA (%)",
            "preco_unit": "Preço catálogo (c/ IVA)",
            "preco_unit_s_iva": "Preço catálogo (s/ IVA)",
            "preco_aplicado": "Custo unitário aplicado",
            "criterio": "Critério aplicado",
            "total_s_iva": "Base s/ IVA",
            "iva": "IVA incluído no custo",
            "total": "Custo dos rebates",
        }
        st.dataframe(
            summary[summary_columns].style.format(
                {column: format_eur for column in ["preco_unit", "preco_aplicado", "total_s_iva", "iva", "total"]}
            ),
            column_config={
                **summary_labels,
                "quantidade": st.column_config.NumberColumn("Qtd."),
                VAT_RATE_COL: st.column_config.NumberColumn("Taxa IVA (%)", format="%.2f"),
            },
            hide_index=True,
            width="stretch",
        )

        unmatched = summary[~summary["matched"]]
        if not unmatched.empty:
            st.warning(
                f"{len(unmatched)} oferta(s) sem correspondência no catálogo "
                "(preço assumido 0 €)"
            )
            with st.expander("Ver ofertas"):
                for oferta in unmatched["oferta"]:
                    name_col, action_col = st.columns([3, 1])
                    name_col.text(oferta)
                    action_col.button(
                        "Adicionar ao catálogo",
                        key=f"import_offer_{oferta}",
                        on_click=_import_offers,
                        args=([oferta],),
                    )
            st.button(
                "Adicionar todas ao catálogo",
                help="Cria linhas no catálogo com preço 0 € para editares "
                "no separador Catálogo.",
                on_click=_import_offers,
                args=(unmatched["oferta"].tolist(),),
            )

        zero_priced = summary[summary["matched"] & (summary["preco_unit"] == 0)]
        if not zero_priced.empty:
            st.caption(
                f"{len(zero_priced)} oferta(s) com preço 0 € no catálogo — "
                "edita os preços no separador Catálogo."
            )

        export = summary.reindex(columns=[
            "oferta", "quantidade", VAT_RATE_COL, "preco_unit", "preco_unit_s_iva",
            "preco_aplicado", "criterio", "total_s_iva", "iva", "total",
        ]).rename(columns=summary_labels)
        d1, d2 = st.columns(2)
        d1.download_button(
            "Download resumo (CSV)",
            export.to_csv(index=False, float_format="%.2f").encode("utf-8-sig"),
            "resumo_rebates.csv",
            "text/csv",
        )
        d2.download_button(
            "Download resumo (Excel)",
            to_xlsx_bytes(export),
            "resumo_rebates.xlsx",
            XLSX_MIME,
        )

with tab_catalog:
    with st.container(border=True):
        st.subheader("Catálogo")
        st.file_uploader(
            "Substituir catálogo (opcional)", type=["xlsx"], key="catalog_upload"
        )
        default = default_catalog()
        if catalog_load_error:
            st.error(f"Catálogo não importado: {catalog_load_error} O catálogo anterior foi mantido.")
        else:
            st.caption(
                f"A usar: {upload.name if upload is not None else (default.name if default else 'nenhum')}"
            )
        st.caption(
            f"Cada produto tem a sua taxa de IVA. Produtos sem taxa definida começam com "
            f"{DEFAULT_VAT_RATE:.0f}% — revê e ajusta as taxas na tabela. "
            "A checkbox «Usar preço c/ IVA» escolhe o custo usado nos rebates, "
            "sem alterar os preços do catálogo."
        )

    if catalog is None:
        st.error("Sem catálogo de prémios: carrega um ficheiro .xlsx acima.")
    else:
        st.caption(
            f"{len(catalog)} prémios — preços em euros; clica numa célula para editar, "
            "ou escreve o nome na última linha para adicionar um produto. "
            "Alterar a taxa mantém o preço total e recalcula o preço s/ IVA. "
            "Alterar o preço s/ IVA atualiza o total. O IVA em euros é calculado automaticamente. "
            "Clica num cabeçalho para ordenar; a linha para adicionar produtos fica fixa no fundo."
        )
        catalog_editor(
            catalog,
            key=f"catalog_editor_{st.session_state.catalog_version}",
            on_change=_apply_catalog_edits,
        )
        if st.session_state.get("catalog_error"):
            st.error(st.session_state.catalog_error)
        st.button(
            "Remover produtos selecionados",
            disabled=not st.session_state.get("catalog_selected"),
            on_click=_delete_selected,
        )
        catalog_export = catalog.rename(
            columns={
                "nome": "Nome",
                VAT_RATE_COL: "Taxa IVA (%)",
                "iva_unit": "IVA (€)",
                "preco_s_iva": "Preço s/ IVA",
                "preco": "Preço total",
                USE_VAT_COL: "Usar preço c/ IVA",
            }
        )
        st.download_button(
            "Download catálogo (Excel)",
            to_xlsx_bytes(catalog_export),
            "catalogo.xlsx",
            XLSX_MIME,
        )
