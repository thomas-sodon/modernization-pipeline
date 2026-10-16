"""The Access reference engine (S2.reference, docs/contracts.md#reference-catalog).

Modules (none is imported here, so `import toolkit.reference.bridge` loads only the bridge):

- `bridge`: one in-process JVM (JPype1) with Jackcess 5.1.7 and UCanAccess 5.1.8; S0's readers of
  accdb and mdb files and of `source.data_copy` use it too;
- `qbe`: saved-query designs of the model to Access SQL through Jackcess's query formatter
  (`python -m toolkit.reference.qbe <eng>` prints them);
- `catalog`: the `reference sql` step (out/reference/catalog.json and out/reference/sql/);
- `build`: the `reference build` step (seed and engagement reference databases);
- `engine`: opening a reference database read-only, creating the catalog's queries as views, and
  running units (`reference run`, `ReferenceAdapter`);
- `commands`: the Access side of `toolkit reference sql|build|run`.
"""
