# Layer 2 build: context for every agent working on it

Read this file, `docs/BUILD_PLAN.md`, `docs/layer2/SPEC.md`, and `docs/layer2/SPEC_DECISIONS.md` before doing anything.

## Who you work for

Shreyas Vyamajala, owner of layer 2 (the shared service). Nayan Kanaparthi owns layer 1 and has already built the bank agent, the data, the outside-records service, and the in-memory contract doubles. Vedant Chawardol owns layer 3 (screens, infra, end-to-end tests).

Layer 2 replaces the doubles with real services. The bank already calls the draft contract. Layer 2 answers that call. It does not rewrite the bank to make the call easier.

## The standard

- Build as if a bank's risk, security and audit teams will read every line. A stolen registry database shows codes, hashes and member ids. It shows no VIN, name, amount or date.
- Production-grade, layered code. `domain/` has no database, web framework or network. `application/` is one class per use case and depends on interfaces. SQL appears only under `infrastructure/`. FastAPI appears only under `interface/`.
- "Tests pass" is not "done" until the new service has been exercised by Nayan's existing client. The join is environment URLs (`KEY_HOLDER_1_URL`, `KEY_HOLDER_2_URL`, `REGISTRY_URL`) and the HTTP those clients already send.
- No patchwork to make a test pass. No hardcoded expected answers. The doubles in `contracts/fakes/` are a behavior oracle. Production code does not import `onepledge_contract_fakes`.
- Completion of the layer is decided by review against `docs/layer2/RUBRIC.md`. An implementer does not declare the layer complete.

## How a work package finishes

Each package in `docs/layer2/WORK_PACKAGES.md` is one step.

1. Build that package, including the edge cases listed for it. The package runs. A skeleton that only imports is not a finished package.
2. Test it against Nayan's existing build, using the done-check in the package. His files under `bank_agent/`, `datagen/`, `outside_records/`, `evaluation/` and `contracts/fakes/` stay as he wrote them.
3. Re-run the layer 1 checks the package names, so his tree is still green.
4. Show it working. Run the command in the package's "Show it working" lines and keep the output. Explain, in a few sentences, what that output means and which part of Nayan's bank it answers. The explanation names the request that came in, the decision or calculation that ran, and what was stored or returned.
5. Stop. Report the command output, the explanation, and any SPEC question.
6. Start the next package only after Shreyas says to build the next one.

## Boundaries

- Layer 2 owns: `key_holder/`, `registry/`, `ledger_verifier/`, and `docs/layer2/`.
- Layer 2 reads `contracts/key_holder.yaml` and `contracts/registry.yaml`. It does not edit them unless all three owners approve an additive change. Where a YAML file and this SPEC disagree on the wire, the YAML wins and the SPEC is wrong.
- Layer 2 does not build screens, the data generator, the bank pipeline, or the scorecard.
- A raw loan value never arrives here. Blinded points arrive at a key holder and are not stored. Hex codes arrive at the registry. Neither is written to a log line, an error message, or a URL.

## What is already true in this repository

- `contracts/key_holder.yaml` and `contracts/registry.yaml` are draft `1.0.0-draft.1`, written by layer 1, pending approval by Shreyas and Vedant.
- `contracts/fakes/` implements both drafts in memory. `/healthz` on a double includes `"test_double": true`. State is lost on restart. That storage is not the layer 2 design. The decisions inside the double are the reference for behavior.
- The bank blinds, calls holder 1, forwards that answer to holder 2, unblinds, then submits tokens. See `bank_agent` `TokenizeLoans` and `KeyHolderHttpClient`.

## Sources, and which one wins

Three write-ups of the same plan exist. They are not byte for byte the same. `docs/layer2/SPEC_DECISIONS.md` records each difference that affects layer 2.

| Source | What it is |
|---|---|
| `contracts/*.yaml` | Wire format. Wins over every prose doc. |
| `docs/BUILD_PLAN.md` | Team plan in the repository, agreed 17 Nov 2026. Wins over the PDF where both speak and disagree, except on the wire. |
| OnePledge Build Plan PDF, 17 Nov 2026, 23 pages | Longer form of the same plan. Use it where `BUILD_PLAN.md` is silent (registry column lists, Shreyas's phase column, class names). |
| `docs/layer1/` | How the caller behaves. Do not edit it. |
