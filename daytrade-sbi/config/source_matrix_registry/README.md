# Historical Source Matrix registry

Each file here is a **byte-exact** historical `config/source_matrix.yaml`,
named `<sha256-of-its-own-bytes>.yaml`.

It exists so that historical runs stay verifiable after the live Source Matrix
changes. `src.selection_calibration.resolve_historical_source_matrix_path()`
looks a run's recorded `ranking.input_hashes.source_matrix_sha256` up in this
directory when the current `--source-matrix` file no longer matches.

## The rule this directory enforces

A historical artifact records the SHA256 of the Source Matrix that **was
actually in force when the run happened**. That recorded hash is immutable
evidence. When the live Source Matrix changes, the correct response is to add
the old bytes here — never to rewrite the historical artifact so its recorded
hash points at the new file. Rewriting the artifact would destroy exactly the
evidence the Trust Chain exists to preserve, and would make a historical run
appear to have been produced under a Source Matrix that did not yet exist.

New-era regression fixtures belong in a **new** regression directory, never on
top of an old one.

| File | Era |
| --- | --- |
| `f141bb351a22548535cd6ea1f2b76002004abeef4a50c51c68d28659cdbd6b44.yaml` | Source Matrix v2, used by the `2026-08-12-ranking-v1-complete` and `2026-08-12-selection-v1-selected` regression fixtures |
