# Layer 1 build: context for every agent working on it

Read this file and `docs/BUILD_PLAN.md` in full before doing anything.

## Who you work for

Nayan Kanaparthi, owner of layer 1 (bank side and data). Teammates Shreyas (layer 2: key holders, registry, ledger verifier) and Vedant (layer 3: console, infra, e2e) will build on top of this repository after it is pushed to GitHub. Layer 1 must be finished, correct and easy for them to build against.

## The standard (Nayan's words, condensed)

- Build as if a bank's risk, security and audit teams will read every line. Security, transparency, auditability and reliability must be implemented AND visible (something a reviewer or demo audience can point at and verify), not claimed in prose.
- Production-grade, object-oriented, layered code. No loose scripts, no god functions, no long untyped modules.
- "Tests pass" is not "done". Done means the whole layer works end to end, seamlessly, from a fresh start, and the output a human sees (API responses, CLI output, reports, logs, docs) is clean and professional, not sloppy.
- No vague assumptions. Where a real bank would need something, either build it or record it explicitly as a known limitation with the reason.
- No patchwork or hardcoding to make tests pass. Tests and feedback loops must be rigorous and correct. Never accept self-declared completion; completion is decided by independent review against the fixed rubric.

## Environment facts (cloud build machine)

- Repository: `/home/claude/onepledge` (git, branch main). Commit author is already configured.
- Python 3.13 (target `>=3.12`). Installed: fastapi, sqlalchemy 2, alembic, psycopg 3, pydantic 2, argon2-cffi, PyJWT, cryptography, oprf 5.0.0, hypothesis, pytest, pytest-cov, ruff, mypy, structlog, httpx, uvicorn. Install more with `pip install <pkg> --break-system-packages` if truly needed, and declare every dependency in the right `pyproject.toml`.
- PostgreSQL 16 server already running for development and tests: `postgresql://postgres@localhost:55432/postgres` (trust auth, superuser). Create and drop your own databases. Do not stop this server. Binaries in `/usr/lib/postgresql/16/bin`.
- No Docker daemon here. Still write the Docker Compose / Dockerfiles the team will use on their laptops, but everything must also run here without Docker (scripts that use the local Postgres via env vars).
- No internet services are required at runtime. The alert explainer must work with no model configured (deterministic template fallback); an LLM adapter may exist behind an interface but must be off by default and never required for tests.

## Boundaries

- Layer 1 owns: `datagen/`, `outside_records/`, `bank_agent/`, `evaluation/`. Layer 1 may draft `contracts/` (shared; mark as DRAFT pending Shreyas and Vedant approval).
- Layer 1 must NOT build the real key holder or registry. To run and test end to end, build faithful, clearly labelled contract test doubles in `contracts/fakes/` (in-process and runnable as small HTTP services) that implement Contract B semantics exactly as written in the contract files: real OPRF evaluation for the key holder fake; idempotent submissions, lock uniqueness, conflicts, signed certificates, hash-chained events, signed checkpoints and an outbox for the registry fake. They exist so layer 1 is verifiable now and so Shreyas has an executable reference. They are not production code for layer 2.
- Raw loan values never leave the bank agent except to the outside-records service lookups that a bank legitimately makes (VIN lookups), and never appear in logs.
