# onepledge-ledger-verifier: an independent check of the registry ledger

Layer 2 of OnePledge (`docs/layer2/SPEC.md` section 8). The verifier reads the registry's
ledger, checkpoints and certificates as a reader and recomputes every hash and signature
itself. It does not import the registry's write path, so a forced edit of the database
fails at the sequence number it touched.

Status after WP1: the package installs and has its boundary tests. It has no command
yet. `onepledge-ledger-verifier verify` and its checks arrive in WP8.

## Layout

```
ledger_verifier/
  pyproject.toml  README.md
  src/
    onepledge_ledger_verifier/   # package docstring only until WP8
  tests/
```

## Tests

```
python3 -m pytest ledger_verifier/tests -q
```

The tests check that `src/` imports no layer 1 package and no web framework, and that the
package holds no em dash and no `.env` file, as the repository's pre-commit hooks require.
