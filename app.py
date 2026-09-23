from pathlib import Path

import pandas as pd
import streamlit as st

from rebates import load_catalog, load_ofertas, summarize

CATALOG_DIR = Path(__file__).parent / "static"


def default_catalog() -> Path | None:
    """Bundled catalog: prefer premios_2026.xlsx, else any .xlsx in static/."""
    preferred = CATALOG_DIR / "premios_2026.xlsx"
    if preferred.exists():
        return preferred
    candidates = sorted(CATALOG_DIR.glob("*.xlsx"))
    return candidates[0] if candidates else None

st.set_page_config(page_title="Robbialac Rebates", page_icon="🎁", layout="wide")
st.title("Robbialac Rebates")


@st.cache_data
def get_catalog(path: str) -> pd.DataFrame:
    return load_catalog(path)


col1, col2 = st.columns([1, 2])

with col1:
    with st.container(border=True):
        st.subheader("Dados")
        with st.expander("Catálogo de prémios"):
            catalog_file = st.file_uploader(
                "Substituir catálogo (opcional)", type=["xlsx"]
            )
            default = default_catalog()
            st.caption(
                f"A usar: {catalog_file.name if catalog_file else (default.name if default else 'nenhum')}"
            )
        rebates_file = st.file_uploader(
            "Upload excel de rebates", type=["xlsx", "xls"]
        )


if catalog_file is not None:
    catalog = load_catalog(catalog_file)
elif default is not None:
    catalog = get_catalog(str(default))
else:
    st.error("Sem catálogo de prémios: carrega um ficheiro .xlsx acima.")
    st.stop()

with col1:
    with st.expander(f"Ver catálogo ({len(catalog)} prémios)"):
        st.dataframe(catalog, hide_index=True, width="stretch")

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
            m1.metric("Total gasto", f"{summary['total'].sum():.2f} €")
            m2.metric("Ofertas resgatadas", int(summary["quantidade"].sum()))
            m3.metric("Ofertas distintas", len(summary))

            st.dataframe(
                summary[["oferta", "quantidade", "preco_unit", "total"]],
                column_config={
                    "oferta": "Oferta",
                    "quantidade": st.column_config.NumberColumn("Qtd."),
                    "preco_unit": st.column_config.NumberColumn(
                        "Preço unit.", format="%.2f €"
                    ),
                    "total": st.column_config.NumberColumn(
                        "Total", format="%.2f €"
                    ),
                },
                hide_index=True,
                width="stretch",
            )

            unmatched = summary[~summary["matched"]]
            if not unmatched.empty:
                st.warning(
                    f"{len(unmatched)} oferta(s) sem correspondência no catálogo "
                    "(preço assumido 0 €): "
                    + ", ".join(unmatched["oferta"])
                )

            st.download_button(
                "Download resumo (CSV)",
                summary.drop(columns="matched")
                .to_csv(index=False)
                .encode("utf-8-sig"),
                "resumo_rebates.csv",
                "text/csv",
            )
