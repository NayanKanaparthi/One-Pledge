# onepledge-bank-agent: the program that runs inside one bank

The bank agent holds one bank's loan tapes, cleans them, checks every loan against
outside records, tokenises the loans through two key holders and talks to the shared
registry (SPEC 1, 8 to 15). This package is built in work packages. WP2 provides the
domain model, the complete database with its integrity mechanisms, the first three
pipeline stages (intake, clean, check), the hash chained audit log, the redacting logger
and the `onepledge-bank` commands `db`, `seed-reference` and `worker`. WP3 adds the rest
of the pipeline (tokenise, submit, certify), the registry outbox (alerts, case updates,
checkpoints), review, resubmission and release, checkpoint and certificate verification,
deep verification, the integrity report, and the commands `registry sync`, `integrity
report` and `integrity verify`.

What a reviewer can check, and where:

- **History cannot be edited, even by the owner.** Ten tables (`loans_raw`,
  `audit_log`, `cleaning_issues`, `submission_items`, `local_checks`, `loan_tokens`,
  `certificates`, `checkpoints_seen`, `integrity_runs`, `registry_messages_quarantined`)
  have statement triggers that refuse `UPDATE`, `DELETE` and `TRUNCATE` with SQLSTATE
  `OP001`. The state machines are the only path, on insert and on update: a loan is
  created PENDING and changes only along the table of SPEC 8.4 (`OP002`); a loan's
  identity, and the terms of a tokenised loan, are immutable (`OP003`); a tape is
  created RECEIVED, moves exactly one status forward, enters FAILED with its resume
  status, failed stage and code, and leaves FAILED only to its resume status (`OP004`,
  SPEC_DECISIONS 46); an alert is created NEW and OPEN and follows SPEC 8.5, with its
  facts immutable, its position set once and its case leaving OPEN once (`OP006`,
  SPEC_DECISIONS 47); an alert's status and case status move together, enforced by a check
  constraint (SPEC_DECISIONS 86); every column of `loans`, `tape_submissions` and `alerts`
  is classified as immutable, conditionally mutable or mutable, and a RELEASED loan, a
  COMPLETED tape and a closed alert are frozen whole (SPEC_DECISIONS 87,
  `infrastructure/db/guard_columns.py`); an audit entry that does not extend the chain
  fails with `OP005` (`migrations/versions/0002_guards.py`, `0005_state_guards.py`,
  `0010_column_guards.py`). `TRUNCATE loans_raw` by the
  owner fails with `0A000` first, because other tables reference it by foreign key, and
  `TRUNCATE loans_raw CASCADE` fails with `OP001` (SPEC_DECISIONS 24). No row of any other
  table is ever deleted either: the eleven tables without an insert-only trigger refuse
  `DELETE` and `TRUNCATE` for everyone with `OP008` (users are deactivated, never removed), and
  an answered registry request and an item with its result are frozen whole, the answer set
  once with its status (`OP007`; `0014_no_delete.py`, SPEC_DECISIONS 106).
- **What the lender sent can be rebuilt byte for byte.** Each upload keeps its BOM
  flag, its verbatim header record and trailing text, and every record's verbatim text
  and cells (SPEC_DECISIONS 25). Intake refuses a file whose stored forms would not
  rebuild it, `row_sha256` is the hash of the record's text and the database checks it,
  and a test rebuilds every tape of the `full` data set from the database alone.
- **The program cannot even try.** The API and the worker connect as `{db}_app`, which
  has `SELECT, INSERT` on those tables (and `login_attempts`), `SELECT, INSERT, UPDATE`
  on eight working tables (`registry_outbox_messages` and `registry_outbox_state` among
  them), `SELECT` on reference
  data and `alembic_version`, and nothing else: no `UPDATE`, `DELETE`, `TRUNCATE`, no
  sequence and no DDL. `GRANT_MATRIX` in `infrastructure/db/role_check.py` is the one
  statement of these grants: migration `0009_grant_matrix.py` applies it, and the startup
  guard and the integrity report compare the connected role with it. The worker's
  settings object has no field for an owner or superuser URL, so it cannot hold one.
- **Every change is audited in a hash chain.** Each unit of work appends its audit
  entries just before commit, under one advisory lock, each entry hashing the previous
  one (SPEC 17). A rolled back transaction writes no entry. Administrative acts are
  audited too: `seed-reference` writes `REFERENCE_DATA_SEEDED` and `db migrate` writes
  `SCHEMA_MIGRATED` (SPEC_DECISIONS 30). `tests/bank_testkit/audit_chain.py` recomputes
  the chain independently of the product code.
- **Raw loan values never reach a log.** Logs are JSON lines (on stderr for every
  command of this package, SPEC_DECISIONS 27); a
  `RedactionProcessor` keeps only the keys of SPEC 16.5, refuses unsafe value shapes and
  scrubs VIN shaped text. Events carry ids, codes and counts, never values; exceptions
  are logged as their type and frame locations only. A canary test runs known values
  through every stage and failing path and finds none of them in any record.
- **A crash never corrupts or duplicates.** Each stage commits idempotently; a stage is
  run under a per submission advisory lock; crash tests stop the process at every fault
  point of SPEC 21.3 and compare the resumed result (loan states, tokens, registry locks,
  distinct idempotency keys, one registry submission per key) with an uninterrupted run
  on a second database and a second registry (`tests/integration/test_reliability.py`).
- **Nobody sees a loan value but the bank.** The key holders see only blinded points
  (`infrastructure/crypto/blinder.py` is the only module importing `oprf`); the registry
  sees only the eight scrambled codes of each loan; alerts are explained from the
  registry's match facts alone (`AlertFacts` has no loan value).
- **The process refuses more power than it needs.** Every command that connects with
  `DATABASE_URL` except `integrity report` (`worker`, `registry sync`, `integrity verify`)
  inspects the connected role at startup with `inspect_role()` and exits 1 with one line
  naming the variable, the first violation and the advice for it unless the role is
  least-privilege by the ten points of SPEC_DECISIONS 72 and 103: no superuser and no
  `CREATEDB`, `CREATEROLE`, `REPLICATION` or `BYPASSRLS`; a member of no role; owner of
  nothing; `CONNECT` and schema `USAGE` only; no column privilege, and on every table exactly
  the privileges of the grant matrix at table level, counting grants to `PUBLIC`; no sequence
  privilege; no role setting (`ROLE_HAS_SETTING`), no parameter privilege, no database
  setting of `session_replication_role` for every role (`DATABASE_HAS_SETTING`, object
  `database X: session_replication_role`, advice "remove the database setting"), and a
  session that fires triggers (`TRIGGERS_DISABLED_FOR_SESSION` only when neither setting
  explains a session in `replica` or `local`; SPEC_DECISIONS 128); no `SECURITY DEFINER`
  function and `EXECUTE` on no function of a user schema (migration 0015 takes it from
  `PUBLIC`); no default privilege; nothing `WITH GRANT OPTION`. `integrity report` shows the
  same violations under `TRIGGERS_FIRE_FOR_APP_ROLE` (point 7) and `APP_ROLE_GRANTS_EXACT`
  (every other point) instead. Every connection is opened with the bank agent's own
  `search_path`, `statement_timeout`, `lock_timeout` and `idle_in_transaction_session_timeout`
  (`infrastructure/db/connection_settings.py`), and a new pooled connection whose session would
  not fire triggers is refused; `DATABASE_URL` and `MIGRATION_DATABASE_URL` may not carry query
  parameters. `db migrate` and `seed-reference` refuse a `MIGRATION_DATABASE_URL` role that does
  not own the database ("... is not the owner of the database; use the owner role.") or a
  schema with a table another role owns ("... the schema was changed outside db migrate.");
  `db migrate` also refuses a `DB_APP_ROLE` that is not `{database}_app` of that database.
- **Nothing is stored that cannot be read back.** A value another party supplies is checked
  against one range before it is stored (SPEC_DECISIONS 100, `domain/storable.py`): a
  timestamp from 2000-01-01T00:00:00Z up to 2100, a date from 2000 to 2099, an integer inside
  its column, text without control characters or surrogates and at most 255 characters, JSON
  made only of such text and integers. Outside it, a registry message is poison
  `VALUE_OUT_OF_RANGE` and an answer `MALFORMED_RESPONSE`; repositories take the value
  objects (`StorableTimestamp`, `StorableDate`), the database holds the same ranges as check
  constraints, and the integrity report and deep verification read each section inside its
  own guard, so a row forced past them is an `UNREADABLE` evidence problem naming the table
  and the row, and every other section is still produced.
- **Others can check us without trusting us, and we check them.** Every certificate and
  checkpoint is verified with the keys of `REGISTRY_PUBLIC_KEYS` only, stored with its
  verdict, and failures are never removed: a certificate that failed stays as evidence
  beside the verified one a retry fetches (SPEC_DECISIONS 61), and an outbox message the
  bank cannot apply is kept in `registry_messages_quarantined` with its reason
  (SPEC_DECISIONS 85). Evidence that disappears is itself a finding: a COMPLETED tape's
  answered request without a verified certificate is `CERTIFICATE_MISSING`, and deep
  verification reconciles every proof table, tape and loan with the audit log and checks
  the audit log still ends where the last verification saw it (SPEC_DECISIONS 89). The
  database controls compare every trigger with a guard manifest of its table, events,
  function and function source hash (`infrastructure/db/guard_manifest.py`,
  SPEC_DECISIONS 90), and `SCHEMA_MATCHES_MANIFEST` compares the whole schema (tables,
  columns, constraints, indexes, triggers, functions, rules, policies and every other object)
  with `infrastructure/db/schema_manifest.json`, naming every object that differs
  (SPEC_DECISIONS 105); a test keeps the file equal to a database migrated to head. The halt rule
  (`HALT_ON_REGISTRY_INTEGRITY_FAILURE`, SPEC_DECISIONS 56) stops every submission,
  resubmission and case position while a checkpoint failed, a request has a failed
  certificate and no verified one, a CHECKPOINT message is quarantined, or the bank is
  blind: its last complete outbox sync is missing or older than `OUTBOX_BLIND_HALT_SECONDS`
  (SPEC_DECISIONS 101; the only condition that clears by itself, and before refusing for it
  the rule runs one sync to catch up); turning the rule off is audited at every start. Deep
  verification recomputes the audit chain, every raw row and file hash, every certificate
  and checkpoint signature and the registry's whole hash chain from seq 1, so it finds an
  edit by a superuser and a history rewrite before the latest checkpoint, and says where.
- **Every decision the bank acts on is checked again** (SPEC_DECISIONS 107,
  `application/deep_evidence.py`). The entry for each stage carries digests of what it
  produced (`items_sha256`, `issues_sha256` for CLEANED; `checks_sha256` for CHECKED;
  `tokens_sha256`, `requests_sha256` for SUBMITTED, every answer and result column and the
  hash of each stored response body); `LOAN_RESUBMITTED` carries its request's digest and
  `REFERENCE_DATA_SEEDED` those of `lenders` and `facilities`. Deep verification recomputes
  them (`DIGEST_MISMATCH` with table, submission and stage), cleans every raw row again and
  derives every loan's terms and balance from its latest accepted row (`DERIVATION_MISMATCH`
  with the row or the loan), and replays loans, tapes, alerts and users from their audit
  entries (`STATE_MISMATCH`). What remains outside its reach: a superuser who rewrites data and
  then the audit chain from that point on, the integrity runs and every later entry
  consistently (an off-host copy of the head hash would detect it); a user's password hash,
  display name and email (only role and active are replayed); `login_attempts` and the outbox
  ledger tables, operational records that are not reconciled; and whether an outside source
  still answers what a local check's evidence records (a later edit of the stored result is
  found; the source is not asked again).

## Layout

```
bank_agent/
  pyproject.toml  README.md  alembic.ini
  migrations/
    env.py
    versions/
      0001_schema.py      # the 18 tables of SPEC 12.1 with named checks and indexes
      0002_guards.py      # immutability and audit chain triggers
      0003_grants.py
      0004_checkpoint_body.py  # the signed checkpoint body
      0005_state_guards.py     # tape guard, insert guards, alert guard
      0006_quarantine.py       # the 19th table
      0007_certificate_evidence.py  # failed certificates kept beside verified ones
      0008_outbox_poison.py    # registry_outbox_attempts, removed by 0013
      0009_grant_matrix.py     # the grant matrix
      0010_column_guards.py    # status pairs, column by column guards
      0011_withheld_payload.py  # a quarantined payload kept as its hash
      0012_round_trip.py       # range checks on times another party supplied
      0013_outbox_ledger.py    # registry_outbox_messages and registry_outbox_state: 21 tables
      0014_no_delete.py        # no row is deleted; answered requests are frozen
      0015_function_privileges.py  # no function executable by PUBLIC
  src/
    onepledge_bank_agent/
      domain/
        values  vin  enums  errors  dependency_errors  store_errors  text_rules  storable
        tape  tape_file  loan  alert  entities  dispositions  checks  lender_names
        cleaning/
          model  header  rows  amounts  dates  text_fields  vin_field
        access_policy     # SPEC 18.2
        canonical_json  audit  tokenization  idempotency  outside_records
        explanation       # TemplateAlertExplainer
        registry_records  registry_results  outbox  certificate_rules  checkpoint_rules
        signatures        # the SignatureVerifier port
        raw_evidence  evidence_reconciliation
        stage_digests  replay  # deep verification of every decision (SPEC_DECISIONS 107)
        integrity  integrity_document  # the report and its one JSON and text source
      application/
        ports  evidence_ports  registry_ports  runtime  faults  events  error_records
        audit_entries  authorisation  section_reads
        ingest_tape  clean_tape  run_local_checks  tokenize_loans
        submit_stage      # the halt rule and the stage of SubmitPledge and ReleaseLoans
        submit_pledge  release_loans  tape_requests  request_planning  requester
        registry_sender  result_application  registry_audit
        collect_certificates  verify_certificate  verify_checkpoint
        sync_registry_outbox  outbox_handlers  outbox_outcomes  alert_maker
        review_alert  resubmit_loan  create_release_instruction  retry_tape
        run_deep_verification  evidence_checks  deep_evidence
        get_integrity_report  integrity_sections
        record_startup  record_schema_migration  seed_reference
        advance_submission  # the stages, registered in order
        run_pipeline_worker
      infrastructure/
        settings  setting_reader  logging  request_context  operator_errors
        audit             # the hash chained writer
        clock  ids  stop_signal  encodings  binding  bounded_json  seed_reference_file
        crypto/
          blinder         # the only oprf importer
          signatures      # Ed25519
        db/
          engines  session  connection_settings  provisioning  migrations  locks
          errors  failures  refusals  readable  probe
          role_check  role_points  role_guard  # the role inspection of SPEC_DECISIONS 103
          controls        # the database control inspector
          guard_columns  guard_manifest
          schema_manifest  schema_manifest.json  # SPEC_DECISIONS 105
          models/
            base  tapes  loans  registry  evidence  reference
          repositories/   # one repository per port
            tapes  raw_rows  items  checks  loans  tokens  requests  alerts  facilities
            certificates  checkpoints  quarantine  integrity_runs  evidence_stores
            audit_log  outbox_ledger  history  stage_rows  mapping
        http/
          base            # timeouts and retries
          answers  classification  clients
          outside_records  key_holder_client  registry_client  registry_wire
      interface/
        composition       # the one composition root
        cli/
          main  parser  commands  registry_commands  output  startup
  tests/
    unit/  integration/  contract/
    ports/                # one behavioural class per repository port, memory and PostgreSQL
    bank_testkit/         # fixtures, in-process doubles, three banks, the cleaning oracle
      memory/             # the in-memory ports and scripted dependency fakes
```

Each layer imports only the layers below it; SQL, HTTP and logging libraries appear only
in `infrastructure/`. `tests/unit/test_architecture.py` enforces this and the size and
hygiene limits of SPEC 2.2.

## Commands

```
onepledge-bank db provision               create {db}_owner, {db}_app and the database (needs PG_ADMIN_URL), idempotent
onepledge-bank db migrate                 alembic upgrade head as the owner role; grants go to DB_APP_ROLE
onepledge-bank db status                  current and latest schema revision, read as the app role
onepledge-bank seed-reference --data-dir DIR
                                          upsert this bank's lenders and facilities from DIR/reference.json
onepledge-bank worker [--once]            advance submissions through the five stages and sync the registry
                                          outbox every OUTBOX_POLL_SECONDS; --once stops when nothing is
                                          runnable, otherwise SIGTERM or SIGINT
onepledge-bank registry sync              pull and process the pending registry messages now
onepledge-bank integrity report           the live integrity report (SPEC 15.5)
onepledge-bank integrity verify           deep verification (SPEC 15.6): stored, audited and printed
```

Every command takes `--json`, `--quiet`, `--debug` and `--wide`. Arguments are exact
(SPEC_DECISIONS 121): no option can be abbreviated, an option or flag given twice is a usage
error, and a flag takes no value.

- stdout carries only the result: aligned text, or with `--json` exactly one JSON
  document, `{"ok": true, ...}` with the facts the text shows, or on failure
  `{"ok": false, "error": {"code": "...", "message": "..."}}` (SPEC_DECISIONS 20).
- stderr carries the JSON log lines (`LOG_LEVEL`, or WARNING with `--quiet`) and, on
  failure, one plain sentence.
- Exit 0 on success, 1 for a failure (a variable missing, PostgreSQL not reachable, the
  schema not at the latest revision, an unusable reference file), 2 for a usage error.
  A usage error prints the usage text and one sentence of the product's own on stderr (it
  never repeats what was typed), every line at most 100 columns, help included, and, when an
  argument starting with `--json` was given, also `{"ok": false, "error": {"code": "USAGE", "message": "The command line
  is not valid. See the usage text."}}` on stdout (SPEC_DECISIONS 78). An unexpected
  error prints `Unexpected error. Run with --debug for details.` and writes one `error`
  log record `unhandled_exception` with `exception_type` and `stack` (frames only), for
  every command; `--debug` prints the exception type and call frames. No output holds a
  URL with a password or a key.
- Sentences name the real cause and the variable, never its value: a server that is not
  there, a server that refuses the connection, a database without tables, a role without
  a privilege, and `db provision` with a `PG_ADMIN_URL` role that is not a superuser are
  different sentences. A setting every service shares has the same sentence in each of
  them (`PORT`, `LOG_LEVEL`, `BIND_HOST`, a database URL, a value that is not plain
  text), and a well formed number outside its bounds says "is out of range".
- Human readable output is at most 100 columns. The last column of a table and the
  value of a name and value pair wrap onto lines that start under that column, breaking
  only between facts (after a comma), never inside one and never after a colon
  (SPEC_DECISIONS 108); any other long line, an error sentence included, wraps at spaces,
  never after a colon, onto lines indented two more spaces; a long identifier in a table is shortened as a hash is (12 characters and
  `...`). `--wide` turns the limit and the shortening off; `--json` carries every value
  in full.
- `worker` without `--once` runs until stopped and prints no result, so `--json`
  without `--once` is a usage error. The worker refuses a schema that is not at the
  latest revision.
- Every runtime command starts through named checks (SPEC_DECISIONS 104,
  `interface/cli/startup.py`): settings, database reachable, session parameters, schema at
  head and role inspection (`integrity report`: the tables only, so it can show a false
  control). Each has its own sentence; a statement the database refuses inside one is "The
  database refused a statement of the startup check NAME (SQLSTATE X); check the settings of
  the role of DATABASE_URL and of its database.", never the last resort sentence.
- SIGTERM or SIGINT stops the worker after the unit it is in, not at the end of the
  stage (SPEC_DECISIONS 51): after the current check chunk, tokenise chunk, registry
  request with its stored response, certificate or outbox message, nothing further is
  committed, the advisory lock is released and the process exits 0. The submission keeps
  its status and the stage resumes from that point on the next start. The clean stage is
  one transaction and runs to its commit; an HTTP call in flight finishes or times out,
  so shutdown takes at most one unit plus `HTTP_READ_TIMEOUT_SECONDS`.
- `db migrate` audits an upgrade that changed the revision (`from_revision`, null on a
  new database, and `to_revision`). A downgrade is a development operation and writes no
  entry; after `downgrade base` the audit table no longer exists.
- `seed-reference` reports, and audits in the same transaction, how many lenders and
  facilities were inserted, updated or unchanged, with the SHA-256 of `reference.json`;
  its closing line gives the same counts, and a run that changes nothing says so and is
  audited too. `reference.json` is read as hostile input (SPEC_DECISIONS 74): at most 5
  MiB, at most 32 levels deep, no key twice, no number literal of more than 30 characters, no
  text the database cannot store (U+0000, a surrogate code point), numbers as JSON
  integers only and ids in their canonical spelling, and each problem is one sentence that
  starts with the file's path.
- `worker` writes `HALT_RULE_DISABLED` at every start while
  `HALT_ON_REGISTRY_INTEGRITY_FAILURE` is false.
- `registry sync` prints the messages processed, alerts created, case updates applied
  and checkpoints verified or failed, and is audited as `REGISTRY_SYNC_REQUESTED` with
  `via` cli. When another sync holds the bank's outbox lock it waits for it, up to two
  minutes. It exits 0 only for a complete sync (SPEC_DECISIONS 101): one that read to
  `has_more: false`, gave every element its outcome and had every acknowledgement taken.
  A registry it cannot read prints its dependency error; any other incomplete sync prints
  `OUTBOX_SYNC_INCOMPLETE` with its reason.
- `worker --once` runs every runnable stage whatever the outbox sync did, and exits 0 and
  reports `ok: true` only when its last sync was complete, the bank is not halted and no
  tape ended FAILED during the run; otherwise it lists every problem (`problems`, the first
  also as `error`) and exits 1.
- `integrity report` and `integrity verify` print the seven sections with their facts,
  then under "Open failures" every finding that makes a section BROKEN (the fifteen
  database controls and the violations behind a false one, each with its control, code
  and object; every open quarantined message; the outbox while blind, with since when and
  why; every evidence problem with its stage) and under "Recovered (evidence kept)" the
  failures a later proof recovered, a quarantined message applied late among them. The
  facts of `registry_messages` give the outbox state (last complete sync, or blind since
  when). The text and the JSON form are rendered from the same objects and carry the same
  findings in both directions (SPEC_DECISIONS 88, 108): `violations`, `messages` and
  `outbox`, and the evidence problems with `stage`. Each list holds at most 200 entries
  (messages newest first) beside its total (`violations_total`, `quarantined`,
  `problems_total`), and the text says "first 200 listed" when a list is longer. With
  `--json` the document is the `IntegrityReport` of `bank_agent.yaml` plus `"ok": true`. Both exit 0 when the status is OK and 1 when it is
  BROKEN, after printing the whole report. `integrity report` skips the head and role
  checks, so it can show `SCHEMA_AT_MIGRATION_HEAD` or a role control false, and refuses
  only a database that was never migrated ("The bank database tables are missing; run db
  migrate first."); a section whose query the database refuses is `NOT_VERIFIED` with the
  SQLSTATE, and a role that cannot read `alembic_version` shows `SCHEMA_AT_MIGRATION_HEAD`
  false beside the missing grant. `integrity verify` needs the registry: when it cannot be
  reached it prints `DEPENDENCY_UNAVAILABLE` and stores nothing.
- A transient dependency failure prints `DEPENDENCY_UNAVAILABLE`; a refusal or an
  answer outside the contract prints `DEPENDENCY_REJECTED` (SPEC_DECISIONS 59); the halt
  rule prints `REGISTRY_INTEGRITY_BROKEN`. Each with its fixed message.
- `--debug` adds the exception type and its frames (file, line, function) only: no
  message, no source line, no locals (SPEC_DECISIONS 53.12).
- `db migrate` logs one `schema_migration_applied` record with its `revision` per
  applied revision; third party log records below WARNING are dropped (SPEC_DECISIONS
  43.6).

## Pipeline

| Stage | Use case | Does |
|---|---|---|
| RECEIVED | `IngestTape` | Authorises the role, validates the whole file (size, UTF-8, CSV shape, header, row count), checks that the verbatim forms rebuild the file, refuses a duplicate file for the same facility and kind, and stores the submission and every raw row in one transaction. A refused upload stores nothing but one audit entry. |
| CLEANED | `CleanTape` | Cleans every row from `header_cells` and `raw_cells` (SPEC 10.4, SPEC_DECISIONS 13 and 25), records each fix or rejection as a cleaning issue, opens or updates loans and records one disposition per row, in one transaction. |
| CHECKED | `RunLocalChecks` | Looks up each accepted loan's title, payments and contracts, evaluates the four local checks (SPEC 11), stores the results per loan and holds failing loans locally, in chunks of `CHECK_CHUNK_LOANS` that each commit. |
| TOKENIZED | `TokenizeLoans` | For PENDING loans without their eight tokens, in chunks of `TOKENIZE_CHUNK_LOANS`: blinds the eight inputs of SPEC 9.2, sends them to key holder 1 and its answer to key holder 2, unblinds, derives the tokens and stores them with the key version pair, one transaction per chunk. Both holders must answer for a chunk to be stored; a holder that changes key version mid submission fails it with `KEY_VERSION_MISMATCH`. |
| SUBMITTED | `SubmitStage` | Checks the halt rule, then plans (once) one registry request per `SUBMIT_BATCH_ITEMS` eligible loans with its idempotency key (SPEC 13.4), and sends every unanswered request in batch order. Each send is counted before it leaves (the client's own retries included); each answer is checked against what was sent and applied in one transaction (SPEC 13.5). RELEASE submissions send only asset codes. |
| COMPLETED | `CollectCertificates` | Fetches and verifies the certificate of every answered request that has no verified one (SPEC 15.2) and stores it either way; any failure ends the tape FAILED with `CERTIFICATE_INVALID`, resume status SUBMITTED (loan states are kept as the registry answered). A retry fetches again; the same failed certificate stores nothing new. Then one outbox sync. |

Outside the stages: `SyncRegistryOutbox` processes ALERT, CASE_UPDATE and CHECKPOINT
messages in position order, one transaction each, then acknowledges (SPEC 14.1), at three
levels (SPEC_DECISIONS 101, `application/outbox_outcomes.py`). A page that is not one object
with a `messages` array and a boolean `has_more` acts on nothing and leaves the sync
incomplete. An element without an object or a canonical `message_id` cannot be acknowledged:
it is kept once by its hash as `UNACKNOWLEDGEABLE`, audited, and the sync goes on, incomplete.
A message with a usable id is applied, ignored (a type of the type pattern the bank does not
know), deferred, or poison. A deferral (the bank's database connection lost, a serialisation
failure or deadlock, or a passing registry outage) is never turned into poison, and a
deferred CHECKPOINT holds back only later checkpoints. Poison is everything else: it is
quarantined with its reason (`SCHEMA_MISMATCH`, `VALUE_OUT_OF_RANGE`,
`CONTRADICTS_STORED_STATE`, `REFUSED_BY_DATABASE` with the SQLSTATE,
`REJECTED_BY_DEPENDENCY`, `UNMATCHED`, `OVERSIZED`, `UNEXPECTED_ERROR`; a type that is not of
the pattern is stored as `UNREADABLE`), audited as `REGISTRY_MESSAGE_QUARANTINED`,
acknowledged, and the sync goes on. A CASE_UPDATE that names no alert is kept `UNMATCHED`
and applied when its ALERT arrives. Each handled message has its row in
`registry_outbox_messages` in the handler's transaction (outcome set once, the
acknowledgement's status), so a redelivery is only acknowledged again; an acknowledgement
the registry refuses is audited once as `REGISTRY_ACK_REFUSED`. `registry_outbox_state` keeps
the last complete sync; becoming blind and recovering are each audited once. Nothing a
registry sends can block the outbox or go unrecorded. Every message is validated against
its whole schema before a handler runs, every stored number must fit its column, and
every id and base64 value must be spelled canonically (SPEC_DECISIONS 75), in answers of
the shared services too. A quarantined CHECKPOINT activates the halt rule;
`VerifyCheckpoint` applies SPEC 15.3 and stores every checkpoint with its verdict;
`ReviewAlert`, `ResubmitLoan`, `CreateReleaseInstruction` and `RetryTape` are the
actions of SPEC 13.8, 14.5 and 14.6 (their HTTP routes come with WP4);
`RunDeepVerification` and `GetIntegrityReport` are SPEC 15.5 and 15.6.

`AdvanceSubmission` dispatches to the next registered stage. Tapes of one facility run
in upload order. Failures (SPEC 13.7, SPEC_DECISIONS 26):

| Situation | Handling | Failure code |
|---|---|---|
| Outside records unreachable, timed out, 429 or 5xx after the client's two retries | Stage retried with backoff `min(2^attempts, 60)` seconds | `OUTSIDE_RECORDS_UNAVAILABLE` after `PIPELINE_MAX_ATTEMPTS` |
| 401, 403, 422, any other 4xx except an agreed 404, a 3xx (redirects are not followed), or an answer outside the contract (not decodable, not JSON, another object, an incomplete payments window) | FAILED at once | `OUTSIDE_RECORDS_REJECTED` |
| 404 `TITLE_NOT_FOUND` or `CONTRACT_NOT_FOUND` | A check result, not an error | none |
| A key holder unreachable, 429 or 5xx | Stage retried with backoff; nothing of the chunk is stored | `KEY_HOLDER_UNAVAILABLE` after `PIPELINE_MAX_ATTEMPTS` |
| A key holder refusal (any 4xx) or an answer outside the contract (wrong count, an element that is not a point, a float) | FAILED at once | `KEY_HOLDER_REJECTED` |
| The key version pair changed within a submission, or the registry answered 409 `KEY_VERSION_MISMATCH` | FAILED at once, not retryable | `KEY_VERSION_MISMATCH` |
| The registry unreachable, 429 or 5xx | Stage retried; answered batches keep their results, the rest are sent again with the same key | `REGISTRY_UNAVAILABLE` after `PIPELINE_MAX_ATTEMPTS` |
| A registry refusal or an answer that does not match what was sent | FAILED at once | `REGISTRY_REJECTED_REQUEST` |
| The halt rule is on and active (a failed checkpoint, a request with only a failed certificate, a quarantined CHECKPOINT message, or a blind bank after one catch-up sync) | FAILED before anything is sent | `REGISTRY_INTEGRITY_BROKEN` |
| A certificate failed verification | FAILED; retryable once the registry serves a valid one | `CERTIFICATE_INVALID` |
| Any other error | FAILED at once | `INTERNAL_ERROR` |

Every HTTP failure of the four clients is classified by one table
(`infrastructure/http/classification.py`, SPEC_DECISIONS 49): connection failures,
timeouts, 429 and 5xx are transient; 401, 403, other 4xx, 3xx and every answer outside
the contract are permanent; no exception of the HTTP library reaches a stage as an
unexpected error. `TAPE_STAGE_ATTEMPT_FAILED` and `TAPE_FAILED` carry the same six keys,
always present (SPEC_DECISIONS 58): `stage`, `failure_code`, `attempts`, `dependency`
(`outside_records`, `key_holder_1`, `key_holder_2`, `registry` or null), `http_status`
(null when no answer arrived) and `reason` (`CONNECTION_FAILED`, `TIMEOUT`,
`RATE_LIMITED`, `SERVER_ERROR`, `UNAUTHENTICATED`, `FORBIDDEN`, `REQUEST_REFUSED`,
`MALFORMED_RESPONSE`, `KEY_VERSION_MISMATCH`, `CERTIFICATE_INVALID`, `INTEGRITY_HALT`,
`PROTOCOL_VIOLATION` or `UNEXPECTED`); `TAPE_FAILED` adds `resume_status`. The log
records and the worker output carry the same keys. Every failure code except
`KEY_VERSION_MISMATCH` can be resumed by a retry (SPEC_DECISIONS 26 as amended by 61).

## Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `BANK_CODE` | `seed-reference`, `worker`, `registry sync`, `integrity report`, `integrity verify` | Any code of 3 to 12 upper case letters, for example the demo banks `HARBOR`, `NORTHGATE` and `CALDER`. Required by every runtime command (SPEC_DECISIONS 93, item 11); `seed-reference` with a code that has no facility in `reference.json` says so. |
| `BANK_NAME` | `worker`, `registry sync`, `integrity report`, `integrity verify` | The bank's display name, 1 to 100 printable ASCII characters. Required by every runtime command. |
| `DATABASE_URL` | `db provision`, `db status`, `worker`, `registry sync`, `integrity report`, `integrity verify` | `postgresql://{db}_app:PASSWORD@HOST:PORT/{db}`, the app role. |
| `MIGRATION_DATABASE_URL` | `db provision`, `db migrate`, `seed-reference` | `postgresql://{db}_owner:PASSWORD@HOST:PORT/{db}`, the owner role. Never read by the worker. |
| `DB_APP_ROLE` | `db migrate` | The role that receives the grants: exactly `{db}_app` of the database of `MIGRATION_DATABASE_URL`, never its owner (SPEC_DECISIONS 103). |
| `PG_ADMIN_URL` | `db provision` | Superuser URL. Provisioning only. |
| `OUTSIDE_RECORDS_URL`, `OUTSIDE_RECORDS_API_KEY` | `worker` | The outside records service and this bank's key. |
| `REGISTRY_URL`, `REGISTRY_API_KEY`, `REGISTRY_MEMBER_ID` | `worker`, `registry sync`, `integrity verify` | The registry, this bank's key and its member id. |
| `REGISTRY_PUBLIC_KEYS` | `worker`, `registry sync`, `integrity verify` | JSON object of signing key id to base64 Ed25519 public key: the only trust anchors (SPEC 15.1). The published test vector key is refused. |
| `KEY_HOLDER_1_URL`, `KEY_HOLDER_1_API_KEY`, `KEY_HOLDER_2_URL`, `KEY_HOLDER_2_API_KEY` | `worker` | The two key holders and this bank's keys. |
| `HALT_ON_REGISTRY_INTEGRITY_FAILURE` | `worker` | Default true (SPEC 15.4). False is audited as `HALT_RULE_DISABLED` at every start. |
| `OUTBOX_POLL_SECONDS` | `worker` | Default 2. |
| `OUTBOX_BLIND_HALT_SECONDS` | `worker`, `registry sync`, `integrity report`, `integrity verify` | Default 300 (0.001 to 3,600): a bank whose last complete outbox sync is older is blind, the fourth condition of the halt rule (SPEC_DECISIONS 101). |
| `TOKENIZE_CHUNK_LOANS` | `worker` | Default 100 (1 to 125: at most 1,000 elements per key holder request). |
| `SUBMIT_BATCH_ITEMS` | `worker` | Default 500 (1 to 500, the contract maximum). |
| `LOG_LEVEL` | `worker` | Default `INFO`. |
| `PIPELINE_MAX_ATTEMPTS` | `worker` | Default 8 (1 to 100). |
| `PIPELINE_POLL_SECONDS` | `worker` | Default 1.0. |
| `CHECK_CHUNK_LOANS` | `worker` | Default 100. |
| `HTTP_CONNECT_TIMEOUT_SECONDS`, `HTTP_READ_TIMEOUT_SECONDS` | `worker` | Defaults 2 and 10. |
| `HTTP_TOTAL_TIMEOUT_SECONDS` | `worker`, `registry sync`, `integrity verify` | Default 30: the most one call may take, so a body that trickles in is abandoned (SPEC_DECISIONS 102). |
| `HTTP_MAX_RESPONSE_BYTES` | `worker`, `registry sync`, `integrity verify` | Default 4,194,304 (65,536 to 67,108,864): a larger answer is refused before or while it is read, as `MALFORMED_RESPONSE`; the bank asks for and accepts only `identity` encoding. |
| `TITLE_PENDING_WINDOW_DAYS`, `FIRST_PAYMENT_GRACE_DAYS`, `PAYMENT_WINDOW_DAYS`, `CONTRACT_DATE_TOLERANCE_DAYS` | `worker` | Check windows of SPEC 11; defaults 30, 45, 90, 3. |

`BIND_HOST` (default `127.0.0.1`) and `PORT` are read only by `serve`, the HTTP API of
WP4 (SPEC_DECISIONS 73): no other command parses them, so a wrong value cannot stop a
worker or a one-shot command. `BIND_HOST` is an IPv4 or IPv6 literal, or the wildcard
`0.0.0.0` or `::`; a name such as `localhost` is refused with "Configuration error:
BIND_HOST must be an IPv4 or IPv6 address; set it to 127.0.0.1 or 0.0.0.0.", the sentence
of the other services. The loader checks the format (`ServeSettings`). The probe of
SPEC_DECISIONS 73 is `ServeSettings.check_binding()` (`infrastructure/binding.py`), which
`serve` (WP4) calls before it opens anything: it binds a socket of the literal's family to
`(BIND_HOST, 0)` and then to `(BIND_HOST, PORT)`, every socket call guarded, and refuses
with the sentences of outside records and the doubles, word for word: "BIND_HOST is an
IPv6 address and this machine has no IPv6. Set BIND_HOST to 127.0.0.1 or 0.0.0.0.",
"BIND_HOST is not an address of this machine. Set BIND_HOST to 127.0.0.1 or 0.0.0.0.",
"BIND_HOST cannot be bound. Set BIND_HOST to 127.0.0.1 or 0.0.0.0." and "PORT is already
in use or not permitted on BIND_HOST; set another PORT."
Every value is trimmed of ASCII space, tab, CR and LF at both ends; a value that still
holds a control character or whitespace other than the ASCII space names its variable,
secrets included. A database URL must be `postgresql://USER:PASSWORD@HOST:PORT/DATABASE`
(the superuser URL may omit the database), checked where it is read. `PORT` has no
default (SPEC_DECISIONS 12); it is used by the HTTP API of WP4. Role names
are derived from the two database URLs (SPEC_DECISIONS 17): the roles must be named
`{db}_owner` and `{db}_app`. Database URLs and keys are held as secrets and never appear
in a settings repr, a log line or an error message.

## Running it by hand

From the repository root, with PostgreSQL on port 55432 and the data set of `make data`.
The `opdev` prefix keeps this away from the stack's own databases. The administrative
commands get the superuser and owner URLs in a subshell only (SPEC_DECISIONS 53.13): the
shell that later starts `worker` never has `PG_ADMIN_URL` or `MIGRATION_DATABASE_URL`
exported, and `worker` refuses to start as the owner anyway.

```
OWNER_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
APP_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
OWNER_URL=postgresql://opdev_bank_harbor_owner:$OWNER_PASSWORD@localhost:55432/opdev_bank_harbor
export DATABASE_URL=postgresql://opdev_bank_harbor_app:$APP_PASSWORD@localhost:55432/opdev_bank_harbor
export BANK_CODE=HARBOR BANK_NAME="Harbor Bank"

(
  export PG_ADMIN_URL=postgresql://postgres@localhost:55432/postgres
  export MIGRATION_DATABASE_URL=$OWNER_URL DB_APP_ROLE=opdev_bank_harbor_app
  onepledge-bank db provision
  onepledge-bank db migrate
  onepledge-bank seed-reference --data-dir var/data/full-20261117
)
onepledge-bank db status
```

`OWNER_PASSWORD`, `APP_PASSWORD` and `OWNER_URL` are shell variables, not exported, so no
child process sees them unless a subshell exports them as above.

To see the guards at work:

```
psql "$DATABASE_URL" -c "DELETE FROM audit_log"      # ERROR: permission denied for table audit_log
psql "$OWNER_URL" -c "DELETE FROM audit_log"         # ERROR: table audit_log is insert-only (SQLSTATE OP001)
```

With outside records, the registry double and the two key holder doubles running (their
READMEs) and the variables above set, `onepledge-bank worker --once` advances every
uploaded tape to COMPLETED, then `onepledge-bank integrity verify` shows what the bank
verified. Uploads arrive through the HTTP API of WP4; the tests upload through
`IngestTape`. Users are created by WP4 too, with a `USER_CREATED` audit entry; a user row
inserted by hand has none, so deep verification lists it under `evidence` as
`STATE_MISMATCH` in `users` (SPEC_DECISIONS 107). The test kit's `insert_user` writes the
row and its entry together, as WP4 will.

## Tests

```
python3 -m pytest bank_agent -q
python3 -m pytest bank_agent/tests -m reliability -v
python3 -m pytest bank_agent/tests -k "vector or oprf or certificate or checkpoint" -q
python3 -m pytest bank_agent/tests/unit bank_agent/tests/integration -q --cov=onepledge_bank_agent.domain --cov=onepledge_bank_agent.application --cov-fail-under=85
python3 -m pytest bank_agent/tests -m unit -q --cov=onepledge_bank_agent.domain --cov=onepledge_bank_agent.application --cov-branch --cov-fail-under=85
```

Two coverage gates hold for the domain and application layers together, branches on
(SPEC_DECISIONS 63): the full run, and the unit-only run (`-m unit`: no PostgreSQL, no
network, no child process and no file write; reading committed files is allowed). An
autouse fixture of the shared test tools makes a `unit` test that connects, binds, starts
a process or asks for `tmp_path` fail (SPEC_DECISIONS 77). The tests of SPEC 21.3 are
marked `reliability`; the tokenisation vector tests are marked `contract` as well.
`make test` enforces both gates; `make test-fast` enforces the
unit-only one. The unit tests reach every use case through `bank_testkit/memory/`: an
in-memory unit of work with every repository (leaving a scope without commit discards
it, and committed audit entries are chained by the domain's own rule), and scripted
fakes of the registry, both key holders and the blinder, the outside records clients,
the signature verifier, the clock and the identifier generator. `MemoryBank` wires
them the way the composition root wires the real ones. Nothing under `src/` imports
them. They are held to the real repositories by `tests/ports/`: each class there is
the only definition of one port's behaviour and runs twice, against memory (marker
`unit`) and against PostgreSQL (marker `integration`), so a behaviour the two disagree
on fails the suite.

Integration tests need the PostgreSQL server at `PG_ADMIN_URL` (default
`postgresql://postgres@localhost:55432/postgres`). Each test creates a uniquely named
database (`optest_<UTC time>_<pid>_<8 hex>_bank_harbor`, SPEC_DECISIONS 76) and role pair
through the product's own provisioning and migrations, and drops it afterwards, also when
the run is stopped with SIGTERM; each session first sweeps `optest_` objects more than two
hours old, and `make test-sweep` drops all of them. Every test has a time limit (120
seconds, 600 for `e2e`). Data sets come from the
`onepledge-datagen` command; outside records runs in process on its own test database;
the registry and both key holders are the real doubles of `contracts/fakes` (real OPRF,
real Ed25519, the real registry rules) reached in process through the bank's own HTTP
clients, with one member per bank. Nothing mocks the cryptography or the registry.

- `unit/`: value objects and the VIN check digit, every cleaning rule (with Hypothesis),
  dispositions, the four checks, state machine tables, the access matrix, the audit
  hash rule, redaction, settings, the HTTP client's retries and error mapping, every use
  case against in-memory ports, and the architecture rules.
- `unit/` (WP3): token inputs and the tokenisation vector, the two-holder OPRF equal to
  direct evaluation and independent of the blind (Hypothesis), idempotency keys
  (Hypothesis), the explainer for every role, kind and basis, every certificate and
  checkpoint reason with real signatures, result application, the key holder and
  registry clients over scripted transports.
- `integration/`: the migrated schema (models equal migrations, checks equal enums, up
  down up), every database guard and grant as the owner and the app role, repositories
  and advisory locks under concurrency, the pipeline on S01, S04, S09 and S10 compared
  with an independent computation, the command line, and:
  `test_reliability.py` (every crash point of SPEC 21.3, a lost answer after the
  registry committed, a 503 on the middle of three batches, key holder 2 failing after
  key holder 1, outages to the attempt limit then retry, a duplicate tape, two workers);
  `test_three_banks.py` (every step of the `small` data set for three banks, checked
  without the answer key, then every registry tamper mode); `test_proofs.py` (all four
  certificate reasons, all six checkpoint reasons, the halt rule); `test_integrity.py`
  (a superuser's edits found by deep verification, controls switched off);
  `test_alert_cases.py`, `test_registry_flow.py`, `test_registry_cli.py`; and the log
  canary over the whole pipeline, which also scans every body the registry and key
  holders received, every audit detail and every alert text.
- `contract/`: the committed 20 loan example against its expected file; every request
  of the outside records, key holder and registry clients against their YAML; the
  tokenisation and signature vectors; `AccessPolicy` against the `x-roles` of
  `bank_agent.yaml`; error codes and enumerations against the YAML.

## Additions to SPEC 12.1

Everything SPEC 12.1 and SPEC_DECISIONS require is in the schema; these are the
constraints and indexes added beyond them, each with its reason (SPEC_DECISIONS 29).
None rejects a state SPEC allows, and `tests/integration/test_schema.py` checks that the
models equal the migrated schema, so every one of them is covered by the drift test.

Required by SPEC_DECISIONS 25 (not additions, listed so the table is complete):
`tape_submissions.has_bom`, `header_text`, `header_cells`, `trailer_text`;
`loans_raw.raw_text`, `raw_cells` with `ck_loans_raw_raw_cells` (an array); the four
tape columns are immutable under the tape guard. Required by SPEC_DECISIONS 26 and 30:
`OUTSIDE_RECORDS_REJECTED` in `ck_tape_submissions_failure_code`, and
`REFERENCE_DATA_SEEDED`, `SCHEMA_MIGRATED`, `REFERENCE_DATA`, `SCHEMA` in the audit
action and object type checks.

| Table | Constraint or index | Reason |
|---|---|---|
| tape_submissions, loans_raw, loan_tokens, registry_requests, certificates, checkpoints_seen, audit_log, login_attempts | `ck_<table>_<column>` on every hash or token column (`file_sha256`, `rows_sha256`, `row_sha256`, `token`, `idempotency_key`, `body_hash`, `head_hash`, `prev_hash`, `entry_hash`, `email_sha256`): `^[0-9a-f]{64}$` | A digest column holds exactly a lower case SHA-256 hex digest, so a truncated or upper case value cannot be compared wrongly. |
| loans_raw | `ck_loans_raw_row_sha256_text`: `row_sha256 = sha256(raw_text)` | The row hash is a fact of the row, enforced where it is stored, not only at intake. |
| tape_submissions | `ck_tape_submissions_header_cells`: an array | The header is an ordered list of cells (SPEC_DECISIONS 25 rule 4). |
| loans_raw, local_checks, registry_requests, certificates, audit_log, integrity_runs | `ck_<table>_<column>`: `jsonb_typeof = 'object'` on `raw_payload`, `evidence`, `response_body` (or null), `body`, `detail`, `report` | Readers index these documents by key; a scalar or array would break them silently. |
| tape_submissions | `ck_tape_submissions_resume_status`, `ck_tape_submissions_failed_stage`, `ck_tape_submissions_failure_code`: value lists | SPEC 12.1 names the columns; the lists are the statuses, stages and failure codes of SPEC 8.3 and 13.7. |
| tape_submissions | `ck_tape_submissions_failed_has_stage`: `(status = 'FAILED') = (failed_stage IS NOT NULL)` | The pair of the two SPEC checks for the third failure field. |
| tape_submissions | `ck_tape_submissions_attempts`: `>= 0` | A count of attempts. |
| tape_submissions | `ix_tape_submissions_facility_uploaded_at` | The worker's "one facility's tapes in upload order" query. |
| facilities | `ck_facilities_code`: `^[A-Z0-9-]{3,40}$` | The stable human key used by the manifest and seeding. |
| users | `ck_users_token_version`: `>= 0` | A counter. |
| loans | `ck_loans_lender_loan_number`: `^[A-Za-z0-9][A-Za-z0-9_/-]{0,39}$` | The shape the cleaning rules accept (SPEC 10.4). |
| cleaning_issues | `ck_cleaning_issues_field`: `row` and the six canonical fields | The field names cleaning writes (SPEC 10.4). |
| submission_items | `ck_submission_items_row_number`: `>= 1` | Same rule as `loans_raw.row_number`. |
| local_checks | `ck_local_checks_combination`: the allowed (check type, result, reason) triples | Every answer names a rule that can produce it (SPEC 11.1). |
| registry_requests | `ck_registry_requests_attempts`, `ck_registry_requests_batch_index`: `>= 0`; `ck_registry_requests_key_version`: `^[1-9][0-9]*\.[1-9][0-9]*$` | Counters, and the key version format of `loan_tokens`. |
| registry_request_items | `ck_registry_request_items_position`: `>= 0`; value lists on `match_kind`, `match_basis` (or null); `ck_registry_request_items_kinds_matched`: a subset of the eight token kinds | The values the registry contract defines. |
| certificates | `ck_certificates_as_of_seq`: `>= 1`; `ck_certificates_kind`: the three kinds | A sequence number and the tape kinds. |
| checkpoints_seen | `ck_checkpoints_seen_checkpoint_seq`: `>= 1`; `ck_checkpoints_seen_last_event_seq`: `>= 0` | Sequence numbers. |
| checkpoints_seen | `ck_checkpoints_seen_verified`: `verified = (failure_reason IS NULL)` | The same rule SPEC 12.1 states for certificates; a checkpoint is stored once, after verification (SPEC 15.3). |
| alerts | `ck_alerts_kinds_matched`, `ck_alerts_kinds_not_matched`: subsets of the eight token kinds | The values the registry contract defines. |
| alerts | `ix_alerts_loan_id` | Lineage and loan pages list a loan's alerts (SPEC 14). |
| checkpoints_seen | `body jsonb NOT NULL` with `ck_checkpoints_seen_body` (an object), migration 0004 | The signed body exactly as received. SPEC 12.1 stores `signed_at` as a timestamp, but the signature covers its text; deep verification (SPEC 15.6 step 4) re-verifies every stored signature, which needs the exact signed bytes (SPEC_DECISIONS 54). |
| certificates | migration 0007 (SPEC_DECISIONS 61, 64): primary key `certificate_row_id`; `delivered_body_sha256 char(64) NOT NULL` with `ck_certificates_delivered_body_sha256` (a hex digest); `uq_certificates_verified_certificate_id` and `uq_certificates_verified_request_id` (partial, `WHERE verified`); `uq_certificates_delivery` on `(certificate_id, delivered_body_sha256, signature)`; `ix_certificates_request_id` | A failed certificate is kept beside the verified one a retry stores. `delivered_body_sha256` is the SHA-256 of the canonical JSON of the body as it arrived, computed by the bank, never taken from the registry; `body_hash` keeps the registry's claim, so the two differ exactly in a row whose body was altered. `VerifyCertificate` compares the stored column with the claim, and deep verification recomputes it from the stored body, so an edit of both `body` and `body_hash` is still found. The claim is not in the delivery key because CERTIFICATE_BODY changes only the body. |
| registry_messages_quarantined | the 19th table, migration 0006 (SPEC_DECISIONS 57), amended by 0008 (SPEC_DECISIONS 85): `uq_registry_messages_quarantined_message_id`; value list on `reason` (the eight reasons of decision 85); `payload_sha256` a hex digest; `position` null or `>= 1`; `message_type` 1 to 64 characters; `reason <> 'OVERSIZED' OR payload IS NULL` (migration 0011; 0006 had `(reason = 'OVERSIZED') = (payload IS NULL)`) | One row per message however often it is delivered. An oversized payload is always kept as its hash alone, and so is one holding text the bank cannot store (U+0000, which `jsonb` refuses, or a surrogate code point, which UTF-8 cannot carry), so that such a message can still be quarantined and acknowledged. A position too large to store is kept as unknown. `REGISTRY_MESSAGE_QUARANTINED` joins the audit action list. |
| registry_messages_quarantined | migration 0013 (SPEC_DECISIONS 101): `payload_withheld` with `ck_registry_messages_quarantined_payload`: `payload_withheld = (payload IS NULL)` and always for OVERSIZED; `message_id` null exactly for `UNACKNOWLEDGEABLE` (`ck_registry_messages_quarantined_message_id`); `uq_registry_messages_quarantined_element` on `payload_sha256` where `message_id IS NULL`; the nine reasons of decision 101 | An element without a usable id is kept once by its hash however often it is delivered; the report and the audit entry say whether the payload was withheld. |
| registry_outbox_messages | the 20th table, migration 0013 (SPEC_DECISIONS 101, replacing `registry_outbox_attempts` of 0008): `message_id` primary key; value lists on `outcome` (or null) and `ack_status`; `message_type` of the type pattern or `UNREADABLE`; `(outcome IS NULL) = (handled_at IS NULL)`; `registry_outbox_messages_guard` (`OP009`): `message_id`, `message_type`, `first_seen_at` immutable, `outcome` set once except `QUARANTINED` to `APPLIED_LATE` | One row per message with a usable id, written in the handler's transaction: what makes "handled once" and "nothing unrecorded" checkable. The app role may insert and update; rows are never removed. |
| registry_outbox_state | the 21st table, migration 0013: one row (`ck_registry_outbox_state_singleton`), with the last complete sync, since when syncs are incomplete and why, and since when the bank is blind | The blind rule of SPEC_DECISIONS 101 reads it; the integrity report shows it. |
| certificates, checkpoints_seen, tape_submissions, loans, facilities | `ck_certificates_issued_at_range`, `ck_checkpoints_seen_signed_at_range`: from 2000-01-01T00:00:00Z up to, not including, 2100-01-01T00:00:00Z; `ck_tape_submissions_as_of_date_range`, `ck_loans_origination_date_range`, `ck_facilities_opened_on_range`: 2000-01-01 to 2099-12-31 (migration 0012, SPEC_DECISIONS 100) | A time another party supplied is held only inside the range every reader can read back, the range of `domain/storable.py`; a row forced past it by a superuser is reported as an `UNREADABLE` evidence problem, never a failure of the report. |
| lenders, facilities, users, login_attempts, tape_submissions, loans, registry_requests, registry_request_items, alerts, registry_outbox_messages, registry_outbox_state | `{table}_no_delete`, migration 0014 (SPEC_DECISIONS 106): before `DELETE` and `TRUNCATE`, for each statement, SQLSTATE `OP008` | No row of the bank database is ever removed; with the insert-only triggers this covers every table. |
| registry_requests, registry_request_items | `registry_requests_guard`, `registry_request_items_guard`, migration 0014 (`OP007`): identities immutable; attempts and send times change until the answer; the answer (the result) is set once, in the update that sets `response_status` (`result`); then the row never changes | What the registry answered cannot be rewritten by the app role, nor by the owner without disabling a trigger, which the controls show. The columns are classified in `guard_columns.py`. |
| alerts | `ck_alerts_status_case_status`, migration 0010 (SPEC_DECISIONS 86): NEW and REVIEWING only with an OPEN case, CLEARED only with `CLEARED_DATA_ERROR` or `CLEARED_LATE_RELEASE`, ESCALATED only with CONFIRMED | Only the registry's case outcome closes an alert; no statement can store one without the other. |
| loans, tape_submissions, alerts | insert guards `loans_insert_guard`, `tape_submissions_insert_guard`; `alerts_guard` on insert and update, migration 0005 (SPEC_DECISIONS 46, 47) | Creation in the one state SPEC allows, and the tape and alert machines as the only path. |
| audit_log | `ck_audit_log_seq`: `>= 1`; `ck_audit_log_actor_user`: `(actor_type = 'USER') = (actor_user_id IS NOT NULL)`; `ck_audit_log_request_id`: at most 64 characters; value lists on `action` and `object_type` | The chain starts at 1; a user entry names its user and a system entry names none; request ids are bounded; actions and object types are those of SPEC 17.2 and SPEC_DECISIONS 30. |

## Decisions recorded here

- `registry_requests.attempts` counts every POST, including the client's own two
  retries: the count is written in its own transaction before each attempt leaves
  (SPEC 13.4 "increment attempts ... then POST"), so a lost answer replayed by the
  client shows attempts 2 and status 200.
- A deep verification finding (SPEC 15.6 step 4) is reported against the stored
  verified checkpoint that covers the bad seq (the first whose `last_event_seq` is at
  least the seq), because a `CheckpointFailure` names a checkpoint.
- The halt rule is checked by the submit stage, `ResubmitLoan` and the SUBMIT_POSITION
  action of `ReviewAlert`, before anything is sent (SPEC_DECISIONS 56); a release is
  accepted and its tape fails at the submit stage.
- An outbox message the bank cannot apply is quarantined and acknowledged
  (SPEC_DECISIONS 85); a number with a fraction or an exponent, or an integer literal of
  more than 4,000 digits, in an outbox page is kept as its text so it fails that
  message's schema instead of the whole page, and an object that gives a key twice makes
  only its message poison. A message of a known type holding text the bank cannot store
  (U+0000 or a surrogate code point, anywhere in it) is poison as `SCHEMA_MISMATCH`, and
  its payload is kept as the SHA-256 of its canonical JSON with every non-ASCII character
  escaped; any answer of a shared service holding such text is `MALFORMED_RESPONSE`,
  outside records included, which are read with the same bounds as every other answer. An
  unknown type is acknowledged and audited as `REGISTRY_MESSAGE_IGNORED`. Only a message
  without a parseable id or a usable type (a type holding such text is none), or a page
  that is not one bounded JSON document (deeper than 32 levels, a key twice at page
  level), makes the page malformed: the sync then fails with `DEPENDENCY_REJECTED`, the
  stages still run, and the page is read again on the next sync.
- The certificates section counts as `total` the answered requests and as `verified`
  those with a verified row; `failures` lists every failed row ever stored with the
  last deep verification's own findings (a stored failed row is not one). Deep
  verification re-verifies verified rows only.
- The key version pair of a submission is the one of the tokens already stored for its
  eligible loans, so a resumed tokenise stage detects a holder that changed version.

- A blank line before the header record is kept in `header_text`, so the
  reconstruction of SPEC_DECISIONS 25 rule 1 also holds for such a file; re-parsing
  `header_text` with blank lines dropped yields `header_cells`.
- `details[].issue` codes raised by the domain come from the shared vocabulary of
  SPEC_DECISIONS 22 (a test reads them from the source). The duplicate upload detail
  carries the existing submission id in `issue`, as `contracts/examples` shows.
