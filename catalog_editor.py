import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, JsCode, StAggridTheme

from rebates import (
    DEFAULT_VAT_RATE,
    NAME_COL,
    NET_COL,
    PRICE_COL,
    USE_VAT_COL,
    VAT_COL,
    VAT_RATE_COL,
)


def catalog_editor(catalog: pd.DataFrame, key: str, on_change):
    search = st.text_input(
        "Pesquisar produto",
        key="catalog_search",
        placeholder="Nome ou parte do nome do produto",
        help="Pesquisa sem distinguir maiúsculas ou acentos. Prime Enter para aplicar. "
        "A pesquisa só filtra a tabela: os rebates e as exportações mantêm o catálogo completo.",
    )
    theme = StAggridTheme("balham").withParams(
        accentColor="var(--primary-color)",
        backgroundColor="var(--background-color)",
        foregroundColor="var(--text-color)",
        borderColor="color-mix(in srgb, var(--text-color) 50%, var(--background-color))",
        headerBackgroundColor="var(--secondary-background-color)",
        headerTextColor="var(--text-color)",
        oddRowBackgroundColor="var(--secondary-background-color)",
        rowHoverColor="color-mix(in srgb, var(--primary-color) 7%, transparent)",
        selectedRowBackgroundColor="color-mix(in srgb, var(--primary-color) 16%, transparent)",
        checkboxCheckedBackgroundColor="var(--primary-color)",
        checkboxCheckedBorderColor="var(--primary-color)",
        checkboxCheckedShapeColor="var(--background-color)",
        checkboxIndeterminateBackgroundColor="var(--primary-color)",
        checkboxIndeterminateBorderColor="var(--primary-color)",
        checkboxIndeterminateShapeColor="var(--background-color)",
    )
    data = catalog.copy()
    data["_row_id"] = data.index.map(str)
    money_format = JsCode("""
        function(params) {
            return Number(params.value || 0).toLocaleString('pt-PT', {
                minimumFractionDigits: 2, maximumFractionDigits: 2
            });
        }
    """)
    editable = JsCode("function(p) { return p.data._row_id !== 'new'; }")
    money_column = {
        "minWidth": 140,
        "flex": 1,
        "cellDataType": False,
        "cellEditor": "agTextCellEditor",
        "editable": editable,
        "valueFormatter": money_format,
        "cellStyle": {"textAlign": "right"},
        "getQuickFilterText": JsCode("function() { return ''; }"),
    }
    return AgGrid(
        data,
        gridOptions={
            "columnDefs": [
                {
                    "field": NAME_COL, "headerName": "Nome", "flex": 3, "minWidth": 250, "editable": True,
                    "comparator": JsCode("""
                        function(a, b) {
                            return String(a || '').localeCompare(String(b || ''), 'pt', {sensitivity: 'base', numeric: true});
                        }
                    """),
                    "getQuickFilterText": JsCode(r"""
                        function(p) {
                            return String(p.value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toUpperCase();
                        }
                    """),
                },
                {**money_column, "field": NET_COL, "headerName": "Preço s/ IVA"},
                {
                    **money_column, "field": VAT_RATE_COL, "headerName": "Taxa IVA (%)",
                    "headerTooltip": "Taxa deste produto. Alterá-la mantém o preço total e recalcula o preço s/ IVA.",
                },
                {
                    **money_column, "field": VAT_COL, "headerName": "IVA (€)", "editable": False,
                    "headerTooltip": "Calculado a partir do preço total e da taxa de IVA do produto.",
                },
                {**money_column, "field": PRICE_COL, "headerName": "Preço total (c/ IVA)"},
                {
                    "field": USE_VAT_COL,
                    "headerName": "Usar preço c/ IVA",
                    "minWidth": 150,
                    "flex": 1,
                    "cellDataType": False,
                    "cellRenderer": "agCheckboxCellRenderer",
                    "cellEditor": "agCheckboxCellEditor",
                    "editable": editable,
                    "headerTooltip": "Marcado: o rebate usa o preço total (c/ IVA). "
                    "Desmarcado: usa o preço s/ IVA.",
                    "getQuickFilterText": JsCode("function() { return ''; }"),
                },
            ],
            "defaultColDef": {"resizable": True, "sortable": True, "suppressMovable": True},
            "quickFilterText": search.strip(),
            "quickFilterParser": JsCode(r"""
                function(text) {
                    return text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toUpperCase().split(/\s+/).filter(Boolean);
                }
            """),
            "onFilterChanged": JsCode("function(p) { p.api.deselectAll(); }"),
            "onModelUpdated": JsCode("""
                function(p) {
                    if (p.api.getDisplayedRowCount() === 0) {
                        p.api.showNoRowsOverlay();
                    } else {
                        p.api.hideOverlay();
                    }
                }
            """),
            "overlayNoRowsTemplate": "<span role='status'>Nenhum produto encontrado.</span>",
            "pinnedBottomRowData": [{
                NAME_COL: "", NET_COL: 0.0, VAT_RATE_COL: DEFAULT_VAT_RATE,
                VAT_COL: 0.0, PRICE_COL: 0.0, USE_VAT_COL: False, "_row_id": "new",
            }],
            "getRowId": JsCode("function(p) { return p.data._row_id; }"),
            "onGridReady": JsCode("""
                function(p) {
                    const configuredTheme = p.api.getGridOption('theme');
                    p.api.addEventListener('rowDataUpdated', function restoreConfiguredTheme() {
                        if (!p.api.getGridOption('theme')) {
                            p.api.setGridOption('theme', configuredTheme);
                        }
                    });
                }
            """),
            "ensureDomOrder": True,
            "readOnlyEdit": True,
            "singleClickEdit": True,
            "stopEditingWhenCellsLoseFocus": True,
            "suppressScrollOnNewData": True,
            "rowSelection": {
                "mode": "multiRow",
                "selectAll": "filtered",
                "isRowSelectable": JsCode("function(p) { return p.data._row_id !== 'new'; }"),
            },
        },
        height=420,
        theme=theme,
        key=key,
        callback=on_change,
        update_on=["cellEditRequest", "selectionChanged"],
        data_return_mode="CUSTOM",
        custom_jscode_for_grid_return=JsCode("""
            function({streamlitRerunEventTriggerName, eventData}) {
                if (streamlitRerunEventTriggerName === 'selectionChanged') {
                    return {selected: eventData.api.getSelectedRows().map(row => row._row_id)};
                }
                return {
                    row: eventData.data._row_id,
                    column: eventData.colDef.field,
                    value: eventData.newValue
                };
            }
        """),
        server_sync_strategy="server_wins",
        allow_unsafe_jscode=True,
        enable_enterprise_modules=False,
    )
