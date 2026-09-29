from io import BytesIO
import unittest

import pandas as pd

from rebates import (
    add_missing, add_price_vat, add_vat_split, clean_catalog, edit_catalog_row,
    load_catalog, summarize,
)


def make_catalog(prices, rates=None, use_vat=None):
    data = {"nome": [f"Produto {i}" for i in range(len(prices))], "preco": prices}
    if rates is not None:
        data["taxa_iva"] = rates
    if use_vat is not None:
        data["usar_iva"] = use_vat
    return add_price_vat(pd.DataFrame(data))


def excel_buffer(df):
    buf = BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf


class CatalogTests(unittest.TestCase):
    def test_prices_are_rounded_to_cents(self):
        catalog = clean_catalog(pd.DataFrame({"nome": ["A", "B"], "preco": [12.345, 2.675]}))
        self.assertEqual(catalog.preco.tolist(), [12.35, 2.68])

    def test_each_product_has_its_own_rate(self):
        catalog = make_catalog([106.0, 113.0, 123.0, 100.0], [6, 13, 23, 0])
        self.assertEqual(catalog.preco_s_iva.tolist(), [100.0] * 4)
        self.assertEqual(catalog.iva_unit.tolist(), [6.0, 13.0, 23.0, 0.0])
        self.assertEqual(catalog.taxa_iva.tolist(), [6.0, 13.0, 23.0, 0.0])

    def test_missing_rates_default_to_23(self):
        self.assertEqual(make_catalog([123.0]).taxa_iva.tolist(), [23.0])
        self.assertEqual(make_catalog([123.0, 106.0], [None, 6]).taxa_iva.tolist(), [23.0, 6.0])

    def test_catalog_always_includes_vat_below_rebate_limit(self):
        catalog = make_catalog([49.2, 50.0, 60.0], [23, 23, 23])
        self.assertEqual(catalog.preco_s_iva.tolist(), [40.0, 40.65, 48.78])
        self.assertEqual(catalog.iva_unit.tolist(), [9.2, 9.35, 11.22])
        self.assertEqual(catalog.preco.tolist(), [49.2, 50.0, 60.0])

    def test_catalog_recalculation_is_idempotent(self):
        catalog = make_catalog([49.2, 50.0, 60.0, 123.0])
        pd.testing.assert_frame_equal(add_price_vat(catalog), catalog)
        pd.testing.assert_frame_equal(add_price_vat(add_price_vat(catalog)), catalog)

    def test_old_derived_values_do_not_override_total(self):
        legacy = pd.DataFrame({"nome": ["A"], "preco": [60.0], "preco_s_iva": [60.0], "iva_unit": [0.0]})
        catalog = add_price_vat(legacy)
        self.assertEqual(catalog.preco.tolist(), [60.0])
        self.assertEqual(catalog.preco_s_iva.tolist(), [48.78])
        self.assertEqual(catalog.iva_unit.tolist(), [11.22])

    def test_export_import_preserves_total_and_product_rates(self):
        export = pd.DataFrame({
            "Nome": ["A", "B"], "Preço s/ IVA": [100.0, 100.0],
            "Taxa IVA (%)": [6.0, 13.0], "IVA (€)": [6.0, 13.0], "Preço total": [106.0, 113.0],
        })
        catalog = load_catalog(excel_buffer(export))
        self.assertEqual(catalog.preco.tolist(), [106.0, 113.0])
        self.assertEqual(catalog.taxa_iva.tolist(), [6.0, 13.0])
        self.assertEqual(add_price_vat(catalog).preco_s_iva.tolist(), [100.0, 100.0])

    def test_legacy_export_uses_total_and_default_rate(self):
        export = pd.DataFrame({"Nome": ["A"], "Preço s/ IVA": [60.0], "IVA": [0.0], "Preço total": [60.0]})
        catalog = add_price_vat(load_catalog(excel_buffer(export)))
        self.assertEqual(catalog.preco.tolist(), [60.0])
        self.assertEqual(catalog.preco_s_iva.tolist(), [48.78])

    def test_rates_can_be_imported_as_percentage_strings(self):
        data = pd.DataFrame({"Nome": ["A", "B"], "Preço c/ IVA": [106.0, 113.0], "IVA (%)": ["6%", "13,00%"]})
        self.assertEqual(load_catalog(excel_buffer(data)).taxa_iva.tolist(), [6.0, 13.0])

    def test_excel_percentage_cells_are_supported(self):
        data = pd.DataFrame({"Nome": ["A"], "Preço total": [106.0], "Taxa IVA (%)": [0.06]})
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            data.to_excel(writer, index=False)
            writer.sheets["Sheet1"]["C2"].number_format = "0%"
        buf.seek(0)
        self.assertEqual(load_catalog(buf).taxa_iva.tolist(), [6.0])

    def test_invalid_imported_rates_are_rejected(self):
        for rate in [-1, 101, "abc", float("inf")]:
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                make_catalog([123.0], [rate])

    def test_add_one_or_all_without_duplicates(self):
        catalog = make_catalog([106.0], [6])
        single = add_missing(catalog, ["B"])
        self.assertEqual(single.nome.tolist(), ["Produto 0", "B"])
        all_items = add_price_vat(add_missing(single, [" produto 0 ", "b", "C", " c "]))
        self.assertEqual(all_items.nome.tolist(), ["Produto 0", "B", "C"])
        self.assertEqual(all_items.preco.tolist(), [106.0, 0.0, 0.0])
        self.assertEqual(all_items.taxa_iva.tolist(), [6.0, 23.0, 23.0])


class CatalogEditingTests(unittest.TestCase):
    def setUp(self):
        self.catalog = make_catalog([123.0, 106.0], [23, 6])

    def edit(self, column, value, row=0):
        self.catalog = edit_catalog_row(self.catalog, row, column, value)
        return self.catalog.iloc[row]

    def test_edit_rate_keeps_gross_and_changes_only_one_product(self):
        row = self.edit("taxa_iva", "6")
        self.assertEqual((row.preco, row.preco_s_iva, row.iva_unit), (123.0, 116.04, 6.96))
        self.assertEqual(self.catalog.iloc[1].preco, 106.0)
        self.assertEqual(self.catalog.iloc[1].taxa_iva, 6.0)

    def test_edit_rate_accepts_zero_and_percent(self):
        self.assertEqual(self.edit("taxa_iva", "13%").taxa_iva, 13.0)
        row = self.edit("taxa_iva", 0)
        self.assertEqual((row.preco_s_iva, row.iva_unit, row.preco), (123.0, 0.0, 123.0))

    def test_edit_net_updates_total_using_product_rate(self):
        row = self.edit("preco_s_iva", "150,125", row=1)
        self.assertEqual((row.preco_s_iva, row.iva_unit, row.preco), (150.13, 9.01, 159.14))

    def test_edit_total_updates_net_using_product_rate(self):
        row = self.edit("preco", 212, row=1)
        self.assertEqual((row.preco_s_iva, row.iva_unit, row.preco), (200.0, 12.0, 212.0))

    def test_vat_amount_is_calculated_not_manually_editable(self):
        with self.assertRaises(ValueError):
            self.edit("iva_unit", 10)

    def test_repeated_edits_preserve_product_rate(self):
        self.edit("nome", "Produto editado")
        self.edit("taxa_iva", 13)
        self.edit("preco_s_iva", 200)
        self.assertEqual(self.catalog.nome.tolist(), ["Produto editado", "Produto 1"])
        self.assertEqual(self.catalog.preco.tolist(), [226.0, 106.0])
        self.assertEqual(self.catalog.taxa_iva.tolist(), [13.0, 6.0])

    def test_invalid_amounts_do_not_change_catalog(self):
        original = self.catalog.copy()
        for value in [-1, "NaN", "inf", "abc", None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.edit("preco", value)
        pd.testing.assert_frame_equal(self.catalog, original)

    def test_invalid_rates_do_not_change_catalog(self):
        original = self.catalog.copy()
        for value in [-1, 101, "NaN", "inf", "abc", None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.edit("taxa_iva", value)
        pd.testing.assert_frame_equal(self.catalog, original)

    def test_invalid_names_do_not_drop_products(self):
        for name in ["", " produto 1 "]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.edit("nome", name)
        self.assertEqual(len(self.catalog), 2)


class RebateChoiceTests(unittest.TestCase):
    def summary(self, catalog, offers=None):
        if offers is None:
            offers = catalog.nome
        return add_vat_split(summarize(pd.Series(offers), catalog))

    def test_checkbox_overrides_any_price_boundary(self):
        catalog = make_catalog([49.2, 123.0], [23, 23], [True, False])
        summary = self.summary(catalog).set_index("oferta")
        self.assertEqual(summary.loc["Produto 0", "preco_aplicado"], 49.2)
        self.assertEqual(summary.loc["Produto 0", "criterio"], "Com IVA")
        self.assertEqual(summary.loc["Produto 1", "preco_aplicado"], 100.0)
        self.assertEqual(summary.loc["Produto 1", "criterio"], "Sem IVA")
        self.assertEqual(summary.iva.sum(), 9.2)

    def test_choice_is_applied_before_multiplying_quantity(self):
        catalog = make_catalog([49.2], [23], [False])
        self.assertEqual(self.summary(catalog, ["Produto 0"] * 3).iloc[0].total, 120.0)
        catalog = edit_catalog_row(catalog, 0, "usar_iva", True)
        self.assertEqual(self.summary(catalog, ["Produto 0"] * 3).iloc[0].total, 147.6)

    def test_mixed_rates_follow_individual_choices(self):
        catalog = make_catalog([106.0, 113.0, 123.0, 100.0], [6, 13, 23, 0], [False, True, False, True])
        summary = self.summary(catalog).set_index("oferta")
        self.assertEqual(summary.loc[catalog.nome, "preco_aplicado"].tolist(), [100.0, 113.0, 100.0, 100.0])
        self.assertEqual(summary.iva.sum(), 13.0)

    def test_toggling_choice_never_changes_catalog_prices_or_rates(self):
        catalog = make_catalog([49.2, 113.0], [23, 13], [False, True])
        original = catalog.copy()
        changed = edit_catalog_row(catalog, 0, "usar_iva", True)
        pd.testing.assert_frame_equal(changed.drop(columns="usar_iva"), original.drop(columns="usar_iva"))
        self.assertEqual(changed.usar_iva.tolist(), [True, True])
        restored = edit_catalog_row(changed, 0, "usar_iva", False)
        pd.testing.assert_frame_equal(restored, original)
        self.summary(catalog)
        pd.testing.assert_frame_equal(catalog, original)

    def test_price_and_rate_edits_do_not_change_choice(self):
        catalog = make_catalog([10.0, 123.0], [23, 23], [False, True])
        catalog = edit_catalog_row(catalog, 0, "preco", 500.0)
        catalog = edit_catalog_row(catalog, 1, "preco", 1.0)
        catalog = edit_catalog_row(catalog, 0, "taxa_iva", 6.0)
        self.assertEqual(catalog.usar_iva.tolist(), [False, True])

    def test_missing_choices_use_legacy_limit_once(self):
        raw = pd.DataFrame({"nome": ["A", "B", "C"], "preco": [49.99, 50.0, 50.01]})
        catalog = add_price_vat(raw)
        self.assertEqual(catalog.usar_iva.tolist(), [False, False, True])
        self.assertEqual(add_price_vat(raw, legacy_threshold=60).usar_iva.tolist(), [False, False, False])
        self.assertEqual(add_price_vat(catalog, legacy_threshold=0).usar_iva.tolist(), [False, False, True])

    def test_excel_round_trip_keeps_true_and_false_choices(self):
        catalog = make_catalog([49.2, 123.0], [23, 23], [True, False])
        export = catalog.rename(columns={
            "nome": "Nome", "preco": "Preço total", "preco_s_iva": "Preço s/ IVA",
            "taxa_iva": "Taxa IVA (%)", "iva_unit": "IVA (€)", "usar_iva": "Usar preço c/ IVA",
        })
        loaded = load_catalog(excel_buffer(export))
        self.assertEqual(loaded.usar_iva.tolist(), [True, False])
        pd.testing.assert_frame_equal(loaded, catalog)

    def test_legacy_import_accepts_previous_custom_limit(self):
        raw = pd.DataFrame({"Nome": ["A", "B"], "Preço total": [60.0, 120.0]})
        loaded = load_catalog(excel_buffer(raw), legacy_threshold=100.0)
        self.assertEqual(loaded.usar_iva.tolist(), [False, True])

    def test_boolean_strings_and_excel_numbers_are_parsed_explicitly(self):
        flags = ["true", "False", "sim", "não", "0", "1", 0, 1, False, True]
        catalog = make_catalog([123.0] * len(flags), use_vat=flags)
        self.assertEqual(catalog.usar_iva.tolist(), [True, False, True, False, False, True, False, True, False, True])

    def test_invalid_checkbox_values_are_rejected(self):
        catalog = make_catalog([123.0])
        for value in ["talvez", 2, -1, None, "", float("nan")]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                edit_catalog_row(catalog, 0, "usar_iva", value)

    def test_added_products_start_unchecked_without_changing_existing_choices(self):
        catalog = add_price_vat(add_missing(make_catalog([123.0], use_vat=[True]), ["Novo"]))
        self.assertEqual(catalog.usar_iva.tolist(), [True, False])
        catalog = edit_catalog_row(catalog, 1, "preco", 123.0)
        self.assertEqual(catalog.usar_iva.tolist(), [True, False])

    def test_totals_reconcile_and_unmatched_offers_cost_zero(self):
        catalog = make_catalog([49.2, 113.0], [23, 13], [False, True])
        summary = self.summary(catalog, [" produto 0 ", "Produto 0", "Produto 1", "Missing"])
        self.assertEqual(summary.total.sum(), 193.0)
        self.assertEqual(summary.total_s_iva.sum(), 180.0)
        self.assertEqual(summary.iva.sum(), 13.0)
        self.assertEqual(summary.loc[~summary.matched, "total"].tolist(), [0.0])
        self.assertTrue(((summary.total_s_iva + summary.iva).round(2) == summary.total).all())

    def test_empty_offers_are_supported(self):
        summary = self.summary(make_catalog([123.0]), [])
        self.assertTrue(summary.empty)
        self.assertEqual(summary.total.sum(), 0)


class CatalogEditorTests(unittest.TestCase):
    def test_search_and_sort_preserve_underlying_rows(self):
        from unittest.mock import patch
        from catalog_editor import catalog_editor

        catalog = make_catalog([123.0, 106.0], [23, 6])
        original = catalog.copy()
        with patch("catalog_editor.AgGrid") as grid, patch("catalog_editor.st.text_input", return_value="  Produto 1  "):
            catalog_editor(catalog, key="test_catalog", on_change=lambda _: None)
        data = grid.call_args.args[0]
        options = grid.call_args.kwargs["gridOptions"]
        self.assertTrue(options["defaultColDef"]["sortable"])
        self.assertEqual(options["quickFilterText"], "Produto 1")
        self.assertEqual(data["_row_id"].tolist(), ["0", "1"])
        self.assertEqual(data.nome.tolist(), catalog.nome.tolist())
        self.assertEqual(options["pinnedBottomRowData"][0]["_row_id"], "new")
        self.assertEqual(options["rowSelection"]["selectAll"], "filtered")
        pd.testing.assert_frame_equal(catalog, original)

    def test_selected_checkboxes_use_contrasting_theme_colors(self):
        from unittest.mock import patch
        from catalog_editor import catalog_editor

        with patch("catalog_editor.AgGrid") as grid, patch("catalog_editor.st.text_input", return_value=""):
            catalog_editor(make_catalog([123.0]), key="test_catalog", on_change=lambda _: None)
        params = grid.call_args.kwargs["theme"]["params"]
        for state in ("Checked", "Indeterminate"):
            with self.subTest(state=state):
                self.assertEqual(params.get(f"checkbox{state}BackgroundColor"), "var(--primary-color)")
                self.assertEqual(params.get(f"checkbox{state}BorderColor"), "var(--primary-color)")
                self.assertEqual(params.get(f"checkbox{state}ShapeColor"), "var(--background-color)")


class AppTests(unittest.TestCase):
    def test_product_choices_change_metrics_without_changing_prices(self):
        from pathlib import Path
        from unittest.mock import patch
        from streamlit.testing.v1 import AppTest

        catalog = make_catalog([49.2, 113.0, 50.0], [23, 13, 23])
        offers = pd.Series(["Produto 0"] * 3 + ["Produto 1", "Produto 2"])
        upload = BytesIO()

        def uploader(label, **kwargs):
            return upload if label == "Ficheiro de rebates" else None

        with patch("streamlit.file_uploader", side_effect=uploader), patch("rebates.load_ofertas", return_value=offers) as offer_source:
            app = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=15).run()
            self.assertFalse(app.exception)
            app.session_state["catalog"] = catalog.copy()
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.number_input), 0)
            self.assertEqual([metric.label for metric in app.metric], [
                "Total rebates s/ IVA", "Total rebates c/ IVA", "Total rebates",
                "IVA incluído no custo", "Ofertas resgatadas",
            ])
            self.assertEqual([metric.value for metric in app.metric], ["160,65 €", "113,00 €", "273,65 €", "13,00 €", "5"])
            app.text_input(key="catalog_search").set_value("Produto 1").run()
            self.assertFalse(app.exception)
            self.assertEqual([metric.value for metric in app.metric], ["160,65 €", "113,00 €", "273,65 €", "13,00 €", "5"])
            pd.testing.assert_frame_equal(app.session_state["catalog"], catalog)
            cases = [
                ([False, False, False], ["260,65 €", "0,00 €", "260,65 €", "0,00 €", "5"]),
                ([True, True, True], ["0,00 €", "310,60 €", "310,60 €", "49,95 €", "5"]),
                ([False, True, True], ["120,00 €", "163,00 €", "283,00 €", "22,35 €", "5"]),
                ([False, True, False], ["160,65 €", "113,00 €", "273,65 €", "13,00 €", "5"]),
            ]
            for choices, expected in cases:
                updated = catalog.copy()
                updated["usar_iva"] = choices
                app.session_state["catalog"] = updated
                app.run()
                self.assertFalse(app.exception)
                self.assertEqual([metric.value for metric in app.metric], expected)
                pd.testing.assert_frame_equal(
                    app.session_state["catalog"].drop(columns="usar_iva"), catalog.drop(columns="usar_iva"),
                )
            offer_source.return_value = pd.Series(dtype=str)
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual([metric.value for metric in app.metric], ["0,00 €"] * 4 + ["0"])


class PersistenceTests(unittest.TestCase):
    def _responses(self, get_status=200, get_sha="abc123", put_status=201):
        from unittest.mock import MagicMock

        get_resp = MagicMock(status_code=get_status)
        get_resp.json.return_value = {"sha": get_sha}
        put_resp = MagicMock(status_code=put_status)
        put_resp.json.return_value = {"commit": {"sha": "deadbeefcafe"}}
        return get_resp, put_resp

    def test_commit_updates_existing_file_with_sha(self):
        import base64
        from unittest.mock import patch
        from persistence import commit_catalog

        get_resp, put_resp = self._responses()
        with patch("persistence.requests.get", return_value=get_resp) as get, \
             patch("persistence.requests.put", return_value=put_resp) as put:
            sha = commit_catalog(b"XLSX-BYTES", "owner/repo", "tok")
        self.assertEqual(sha, "deadbeefcafe")
        self.assertEqual(get.call_args.args[0], "https://api.github.com/repos/owner/repo/contents/static/catalogo.xlsx")
        body = put.call_args.kwargs["json"]
        self.assertEqual(body["sha"], "abc123")
        self.assertEqual(body["branch"], "main")
        self.assertEqual(base64.b64decode(body["content"]), b"XLSX-BYTES")
        self.assertEqual(put.call_args.kwargs["headers"]["Authorization"], "Bearer tok")

    def test_missing_file_commits_without_sha(self):
        from unittest.mock import patch
        from persistence import commit_catalog

        get_resp, put_resp = self._responses(get_status=404)
        with patch("persistence.requests.get", return_value=get_resp), \
             patch("persistence.requests.put", return_value=put_resp) as put:
            commit_catalog(b"XLSX", "owner/repo", "tok", branch="dev")
        body = put.call_args.kwargs["json"]
        self.assertNotIn("sha", body)
        self.assertEqual(body["branch"], "dev")

    def test_github_errors_become_valueerror(self):
        from unittest.mock import patch
        from persistence import commit_catalog

        for status in (401, 403, 404, 409, 500):
            get_resp, _ = self._responses(get_status=status)
            with self.subTest(status=status), \
                 patch("persistence.requests.get", return_value=get_resp), \
                 self.assertRaises(ValueError):
                commit_catalog(b"XLSX", "owner/repo", "tok")


if __name__ == "__main__":
    unittest.main()
