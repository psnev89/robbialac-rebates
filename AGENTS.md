# Project verification

- Install dependencies: `python3 -m pip install -r requirements.txt`.
- Run the app: `python3 -m streamlit run app.py`.
- Run regression tests: `python3 -m unittest -v`.
- Check syntax: `python3 -m compileall -q app.py rebates.py catalog_editor.py`.
- Keep financial rules in `rebates.py`; `catalog_editor.py` adapts the community AG Grid component to Streamlit.
- Browser checks should cover repeated edits after scrolling, sorting/searching then editing by stable row ID, filtered selections, pinned new-product rows, per-product rate edits, rebate threshold boundaries, single/bulk offer imports, and catalogue exports. Unit tests do not verify grid JavaScript.
- Catalog search and sorting are view-only: rebates and exports always use the full catalog. Search ignores case and accents; changing it clears selections and select-all targets filtered rows only.
- If Rerun does not pick up an imported module change, restart Streamlit. Export session catalog edits first and reimport afterwards; restarting loses session state.
- Catalog gross price and per-product VAT percentage are authoritative. Editing a rate preserves the gross price; net price and VAT amount are derived. Missing rates default to 23% and should be reviewed by the user.
- The rebate limit compares the gross UNIT price: gross > limit uses gross cost; gross <= limit uses net cost. Multiply the selected unit cost by quantity. Changing the limit must never mutate the catalog.
- App palettes live in `.streamlit/config.toml`; the catalogue grid derives its colors from the active Streamlit theme. Verify both Light and Dark through the top-right main menu when changing styles.
- `streamlit-aggrid==1.2.1.post2` clears its grid theme during theme changes. The grid's `rowDataUpdated` listener restores the configured theme; its parameters reference Streamlit's live color variables to avoid stale `st.context.theme` values.
