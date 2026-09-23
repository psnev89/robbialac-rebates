from pathlib import Path

import pandas as pd
import streamlit as st

from rebates import load_catalog, load_ofertas, summarize

CATALOG_DIR = Path(__file__).parent / "static"

EUR_SEPARATORS = str.maketrans(",.", ".,")


def format_eur(value: float) -> str:
    return f"{value:,.2f} €".translate(EUR_SEPARATORS)


def default_catalog() -> Path | None:
    """Bundled catalog: prefer premios_2026.xlsx, else any .xlsx in static/."""
    preferred = CATALOG_DIR / "premios_2026.xlsx"
    if preferred.exists():
        return preferred
    candidates = sorted(CATALOG_DIR.glob("*.xlsx"))
    return candidates[0] if candidates else None

st.set_page_config(page_title="Robbialac Rebates", page_icon="🎁", layout="wide")

LOGO = CATALOG_DIR / "logo.svg"
st.image(str(LOGO))

st.title("Custo rebates +Pinta")


@st.cache_data
def get_catalog(path: str) -> pd.DataFrame:
    return load_catalog(path)

col1, col2 = st.columns([1, 2])

with col1:
    with st.container(border=True):
        st.subheader("Upload Excel de rebates")
        rebates_file = st.file_uploader(
            None, type=["xlsx", "xls"], label_visibility="collapsed"
        )

with col1:
    with st.container(border=True):
        st.subheader("Catálogo atual")

        catalog_file = st.file_uploader(
            "Substituir catálogo (opcional)", type=["xlsx"]
        )
        default = default_catalog()
        st.caption(
            f"A usar: {catalog_file.name if catalog_file else (default.name if default else 'nenhum')}"
        )
        if catalog_file is not None:
            catalog = load_catalog(catalog_file)
        elif default is not None:
            catalog = get_catalog(str(default))
        else:
            st.error("Sem catálogo de prémios: carrega um ficheiro .xlsx acima.")
            st.stop()
        with st.expander(f"Ver catálogo ({len(catalog)} prémios)"):
            st.dataframe(
                catalog.style.format({"preco": format_eur}),
                column_config={"nome": "Nome", "preco": "Preço"},
                hide_index=True,
                width="stretch",
            )



with col2:
    with st.container(border=True):
        st.subheader("Resultado")
        if rebates_file is None:
            st.info("Carrega um ficheiro de rebates para ver o resumo.")
        else:
            try:
                ofertas = load_ofertas(rebates_file)
            except ValueError as e:
                st.error(str(e))
                st.stop()
            summary = summarize(ofertas, catalog)

            m1, m2, m3 = st.columns(3)
            m1.metric("Total gasto", format_eur(summary["total"].sum()))
            m2.metric("Ofertas resgatadas", int(summary["quantidade"].sum()))
            m3.metric("Ofertas distintas", len(summary))

            st.dataframe(
                summary[["oferta", "quantidade", "preco_unit", "total"]].style.format(
                    {"preco_unit": format_eur, "total": format_eur}
                ),
                column_config={
                    "oferta": "Oferta",
                    "quantidade": st.column_config.NumberColumn("Qtd."),
                    "preco_unit": "Preço unit.",
                    "total": "Total",
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
                    st.markdown(
                        "\n".join(f"- :orange[{o}]" for o in unmatched["oferta"])
                    )

            st.download_button(
                "Download resumo (CSV)",
                summary.drop(columns="matched")
                .to_csv(index=False)
                .encode("utf-8-sig"),
                "resumo_rebates.csv",
                "text/csv",
            )
