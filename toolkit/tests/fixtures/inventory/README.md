# Inventory fixtures (synthetic)

Hand-written object models for test_inventory.py and test_estimate.py, in the format of
docs/formats/model.md. Nothing here comes from a client or Microsoft file: `access/` describes a
made-up widget shop (an accdt model whose `properties/*.axl` parts are metadata, never data macros),
and `xlsm/` a made-up quote workbook that provides every xlsm count key. `xlsm/objects.json`
carries `facts.array_formula_cells`, which the model schema does not list yet (S0.parse request);
the inventory takes the key from there when present and leaves it out otherwise.
