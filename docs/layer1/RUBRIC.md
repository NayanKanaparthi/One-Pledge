# OnePledge layer 1: release rubric (FIXED)

Version 1.0, 2026-11-21. This rubric is fixed. It is not edited to make a build pass. A change needs Nayan's written approval and a new version number.

## Rules of grading

1. Every criterion is mandatory and is graded PASS or FAIL. There is no partial credit and no "pass with notes".
2. A criterion passes only if every statement in its pass condition is observed by the reviewer on the build machine. A claim in a document, a commit message or an implementer's report is not evidence.
3. The reviewer runs the procedure as written. If a command in a procedure cannot be run because the product lacks what it needs (a missing target, endpoint, file or field), the criterion fails.
4. The reviewer records, for each criterion: PASS or FAIL, the commands run, and the decisive output (trimmed). For a FAIL the reviewer states the concrete failure: what was expected, what was observed, where.
5. Layer 1 is released only when all 70 criteria pass in one review round on one commit. After any fix, every criterion is graded again on the new commit.
6. Reviewers are independent of the implementer and never modify product code. They may write throwaway scripts outside the repository.
7. The specification is `docs/layer1/SPEC.md`. Where a procedure says "per SPEC n" the reviewer compares against that section.
8. A reviewer who finds a defect that no criterion covers reports it under the closest criterion's group as an extra finding. An extra finding that shows a violation of BUILD_PLAN, CONTEXT or SPEC fails the closest criterion.

## Reviewer setup (used by the procedures)

Work on a fresh clone, with a private database prefix and port base so reviews do not collide.

```
git clone /home/claude/onepledge "$SCRATCH/op" && cd "$SCRATCH/op"      # SCRATCH is the reviewer's own empty directory
# follow README.md from here; when the README has created .env, set a private prefix and port base in it:
#   ONEPLEDGE_DB_PREFIX=rv<4 random digits>     ONEPLEDGE_PORT_BASE=<a free multiple of 1000 between 20000 and 40000>
set -a; . ./.env; set +a
PX=$ONEPLEDGE_DB_PREFIX; PB=$ONEPLEDGE_PORT_BASE
H=http://127.0.0.1:$((PB+101)); N=http://127.0.0.1:$((PB+102)); C=http://127.0.0.1:$((PB+103))
OUT=http://127.0.0.1:$((PB+200)); KH1=http://127.0.0.1:$((PB+301)); KH2=http://127.0.0.1:$((PB+302)); REG=http://127.0.0.1:$((PB+400))
PSQL=/usr/lib/postgresql/16/bin/psql
adm(){ $PSQL -X -At "${PG_ADMIN_URL%/*}/${PX}_bank_$1" -c "$2"; }                       # superuser on a bank db: adm harbor "select 1"
app(){ $PSQL -X -At "postgresql://${PX}_bank_$1_app:$2@$PG_HOST:$PG_PORT/${PX}_bank_$1" -c "$3"; }   # app role: app harbor "$BANK_HARBOR_DB_APP_PASSWORD" "select 1"
own(){ $PSQL -X -At "postgresql://${PX}_bank_$1_owner:$2@$PG_HOST:$PG_PORT/${PX}_bank_$1" -c "$3"; }
login(){ curl -s -X POST "$1/v1/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$2\",\"password\":\"$DEMO_USER_PASSWORD\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }
get(){ curl -s "$1" -H "authorization: Bearer $2"; }                                    # get "$H/v1/loans?limit=5" "$TOKEN"
tc(){ curl -s "$@" -H "X-Test-Control-Key: $FAKE_TEST_CONTROL_KEY"; }                   # test controls of the doubles
fid(){ get "$1/v1/facilities" "$2" | python3 -c "import sys,json;print(next(f['facility_id'] for f in json.load(sys.stdin)['items'] if f['code']=='$3'))"; }
upload(){ curl -s -X POST "$1/v1/tapes" -H "authorization: Bearer $2" -F "facility_id=$3" -F "kind=$4" -F "as_of_date=$5" -F "file=@$6"; }
```

Named states:

- **STATE-EMPTY**: `make down; make db-reset && make data PROFILE=small SEED=$S1 && make seed PROFILE=small SEED=$S1 && make up`. Stack running, no tape uploaded. `D=var/data/small-$S1`.
- **STATE-RUN**: `make down; onepledge-eval scorecard --seed $S1 --profile small --keep-running`. Every tape replayed, stack running. `D=var/data/small-$S1`.
- `S1` and `S2` are two integers the reviewer picks. Before using them the reviewer confirms `git grep -nw -e "$S1" -e "$S2"` prints nothing.

"The demo users" are `analyst@<bank>.example`, `officer@<bank>.example`, `admin@<bank>.example` with `DEMO_USER_PASSWORD`.

---

## 1. End-to-end function

### E2E-01 Fresh clone to finished demo with one command
- **Requirement**: From a fresh clone, following only `README.md`, `make demo` takes layer 1 from empty databases to a complete narrative and scorecard.
- **Procedure**: Fresh clone as above. Read only `README.md`. Run exactly the commands it gives up to and including `make demo`, timing the last one (`time make demo`).
- **Pass**: No step needed knowledge outside the README. `make demo` exits 0 in at most 10 minutes. The output contains a block for each of scenes 1, 2, 3, 4, 5, 7, 8 and 9, each ending `Result: PASS`, followed by the scorecard ending with all gates passed. `var/reports/demo_report.md`, `demo_report.json`, `scorecard.md` and `scorecard.json` exist and `python3 -m json.tool` parses both JSON files.

### E2E-02 Full scorecard on the default seed
- **Requirement**: `make scorecard` replays the full data set and every gate passes.
- **Procedure**: `make scorecard; echo exit=$?`. Then `python3 -c "import json;d=json.load(open('var/reports/scorecard.json'));print(json.dumps(d,indent=1)[:3000])"`.
- **Pass**: Exit 0. Every FRAUD and HONEST_LOOKALIKE case type of SPEC 5.7 appears with planted equal to the full profile count and missed equal to 0. False alarms 0, false holds 0, rows with outcome OTHER 0, all three privacy counts 0. The blind spot type is listed separately and is not counted as caught or missed.

### E2E-03 The result does not depend on the seed
- **Requirement**: The system detects by rule, not by memorised data.
- **Procedure**: `onepledge-eval scorecard --seed $S1 --profile small; echo $?` then the same with `$S2`. Compare `tapes/HARBOR/S01*.csv` of the two data directories with `cmp`.
- **Pass**: Both runs exit 0 with all gates passed. The two tape files differ.

### E2E-04 The layer 1 gate is green
- **Requirement**: Lint, types, contract check and all tests pass together.
- **Procedure**: `make ci; echo exit=$?`.
- **Pass**: Exit 0. The output shows ruff, mypy, contracts-check and pytest each completing without error. pytest reports 0 failed, 0 errors, 0 skipped, 0 xfailed, 0 xpassed.

### E2E-05 Nothing about a stopped loan leaves the bank
- **Requirement**: Loans rejected in cleaning or held by local checks cause no key holder and no registry traffic.
- **Procedure**: STATE-RUN. For each bank `b` in harbor, northgate, calder with member id `M` (`BANK_<B>_MEMBER_ID`): (a) `adm $b "select count(*) from loans l where state='HELD_LOCAL' and exists (select 1 from loan_tokens t where t.loan_id=l.loan_id)"`; (b) `adm $b "select count(*) from loans l where state='HELD_LOCAL' and exists (select 1 from registry_request_items i where i.loan_id=l.loan_id)"`; (c) `adm $b "select coalesce(sum(item_count),0) from registry_requests where response_status in (200,201)"` compared with `tc $REG/_test/stats` `by_member[M].items`; (d) `adm $b "select count(*) from loan_tokens"` compared with `tc $KH1/_test/stats` and `tc $KH2/_test/stats` `by_member[M].elements`.
- **Pass**: (a) and (b) are 0 for every bank. (c) is equal for every bank. In (d) each holder's element count equals the bank's token row count for every bank.

---

## 2. Data generation and answer key

### DATA-01 Deterministic by seed
- **Requirement**: Same seed and profile give byte identical output; a different seed gives different data.
- **Procedure**: `onepledge-datagen generate --seed $S1 --profile small --out $SCRATCH/a`; again into `$SCRATCH/b`; with `$S2` into `$SCRATCH/c`. `diff -r $SCRATCH/a $SCRATCH/b; echo $?`. `diff -rq $SCRATCH/a $SCRATCH/c | head`. `grep -rnE "datetime\.now|time\.time|uuid4\(|os\.urandom|random\.(random|randint|choice|shuffle)\(" datagen/src`.
- **Pass**: First diff prints nothing and exits 0. Second diff lists differing tape files. The grep prints nothing (all randomness goes through seeded `Random` instances).

### DATA-02 Entities, sizes and every planted case type
- **Requirement**: The full profile matches SPEC 5.4 to 5.7.
- **Procedure**: `make data` (full, default seed), `D=var/data/full-20261117`. `onepledge-datagen verify --data-dir $D; echo $?`. `python3 - <<'PY'` script that prints: banks, lenders, facilities from `reference.json`; steps from `manifest.json`; the number of distinct VINs over all tapes (csv module, both header styles); per `case_type` the number of cases in `answer_key/answer_key.json`; per messiness `kind` the number of rows.
- **Pass**: `verify` exits 0. 3 banks, 2 lenders, 5 facilities with the names and codes of SPEC 5.4. 15 steps exactly as the table in SPEC 5.6 (step, bank, facility, kind, as-of date, concurrent group). Distinct VINs between 4,900 and 5,200. Case counts equal the full column of SPEC 5.7 for all 16 case types. Every messiness kind of SPEC 5.7 is present, fixable kinds at least 10 rows each, unfixable kinds at least 3 rows each.

### DATA-03 VINs are valid by an independent check
- **Requirement**: Every VIN carries a correct ISO 3779 check digit unless the answer key says the row is planted as invalid.
- **Procedure**: The reviewer writes their own check digit function from SPEC 5.5 (not importing product code), runs it over the `vin` cell of every tape row of `$D` after upper casing and removing spaces and hyphens, and lists rows that fail. Compare the failing set with the answer key rows whose expected cleaning problem is one of `VIN_CHECK_DIGIT_INVALID`, `VIN_LENGTH_INVALID`, `MISSING_REQUIRED_FIELD`, `VIN_CHARACTER_SUBSTITUTED`, `NON_DATA_ROW`.
- **Pass**: Every failing row is in that answer key set, and every answer key row with `VIN_CHECK_DIGIT_INVALID` is in the failing set.

### DATA-04 Tapes look like what lenders send, and carry no labels
- **Requirement**: Formats and messiness per SPEC 5.9; no hint of planted cases outside the answer key.
- **Procedure**: `head -c 3 $D/tapes/HARBOR/S01*.csv | xxd`; `file $D/tapes/HARBOR/S01*.csv $D/tapes/HARBOR/S02*.csv`; `head -3` and `tail -3` of both. `grep -rilE "fraud|copy|invent|planted|lookalike|blind_spot|race_pair|messi|exact|likely" $D/tapes $D/manifest.json $D/reference.json $D/outside_records; ls -R $D | grep -iE "fraud|copy|invent|answer"`.
- **Pass**: The Summit file starts with bytes `ef bb bf`, has CRLF line ends, the Summit header of SPEC 5.9, `MM/DD/YYYY` dates, quoted `$` amounts and a final `TOTAL` row. The Lakeside file has no BOM, LF line ends, its own header, ISO dates and plain amounts. The grep prints nothing. The only path containing `answer` is `answer_key/answer_key.json`.

### DATA-05 The answer key is honest
- **Requirement**: Every expected outcome is one the system can produce from the data as constructed (SPEC 5.7).
- **Procedure**: With a throwaway script, for 3 randomly chosen cases of each case type in `$D`, look up the case rows in the tape files and in `outside_records/*.csv` and check the construction: for example `INVENTED_NO_TITLE` has no title row and no payment rows, a contract row, and an origination 120 to 400 days before 2026-11-05; `COPY_AMOUNT_CHANGED` has two contract rows for the VIN, one with each amount, and the two amounts fall in different 100 dollar buckets; `COPY_AMOUNT_CHANGED_NO_CONTRACT` has one contract row with the original amount; `COPY_VIN_ALTERED_TITLE_PENDING` differs from its original in exactly one VIN character of the same transliteration value, has no title row, a contract row, and an origination at most 30 days before 2026-10-29; `COPY_VIN_ALTERED_CHECK_DIGIT_INVALID` fails the reviewer's check digit function; each `TWO_CARS_ONE_BORROWER` pair shares the borrower and differs in VIN, and near pairs share month and 1,000 dollar band but not day or 100 dollar band; `TITLE_PENDING_HONEST` has no title row and is 3 to 15 days old at its step's as-of date.
- **Pass**: All sampled cases (at least 48) match their construction rule and their expected outcome follows from SPEC sections 9 to 14. No expected outcome contradicts the rules.

### DATA-06 Outside records service
- **Requirement**: Seeded from datagen output, read-only, authenticated, contract conformant, and silent about VINs in its logs.
- **Procedure**: STATE-EMPTY. `wc -l $D/outside_records/*.csv` versus `$PSQL -X -At "${PG_ADMIN_URL%/*}/${PX}_outside" -c "select (select count(*) from title_records),(select count(*) from payments),(select count(*) from contracts)"`. Take a VIN `V` from `titles.csv`: `curl -s -i $OUT/v1/titles/$V -H "X-API-Key: $BANK_HARBOR_OUTSIDE_API_KEY"`; without the header; with a wrong key; `curl -s -i "$OUT/v1/titles/NOTAVIN" -H "X-API-Key: ..."`; `curl -s "$OUT/v1/payments?vin=$V&limit=2" -H ...`; `curl -s -i $OUT/v1/contracts/1HGCM82633A004352 -H ...`. As the app role: `$PSQL "postgresql://${PX}_outside_app:$OUTSIDE_DB_APP_PASSWORD@$PG_HOST:$PG_PORT/${PX}_outside" -c "insert into payments values (gen_random_uuid(),'$V',current_date,1,'X')"` and `-c "delete from title_records"`. `$PSQL ... -c "select api_key_sha256 from api_clients"`. `grep -c "$V" var/log/outside-records.jsonl`.
- **Pass**: Row counts equal the CSV data rows. 200 with the contract's `TitleRecord` shape; 401 `UNAUTHENTICATED` without or with a wrong key; 422 `VALIDATION_ERROR` whose body does not contain `NOTAVIN`; payments page shape correct and newest first; 404 `CONTRACT_NOT_FOUND` whose body does not contain the VIN. Both write statements fail with `permission denied`. `api_clients` holds 64 hex character hashes and no column holds a key starting `opk_`. The grep count is 0.

### DATA-07 The 20 loan example is committed and reproducible
- **Requirement**: SPEC 5.9 example files exist, regenerate identically, and the bank agent reproduces the expected results.
- **Procedure**: `ls contracts/examples/tapes/`; `onepledge-datagen generate --seed 20261117 --profile example --out $SCRATCH/ex` then `cmp` the generated tape and outside record files against the committed ones; `grep -c "" contracts/examples/tapes/example_tape_20.csv`; `python3 -m pytest bank_agent/tests -k example -q`.
- **Pass**: The files of SPEC 5.9 exist. `cmp` reports no difference. The tape has a header and exactly 20 data rows. The expected file lists all 20 rows with the composition of SPEC 5.6 (example profile). At least one test runs and passes.

---

## 3. Bank agent correctness

### BANK-01 Cleaning follows the rule table
- **Requirement**: Every rule of SPEC 10.4 and 10.5 behaves as written.
- **Procedure**: STATE-EMPTY. The reviewer hand-writes a Summit-format CSV for facility HARBOR-SUMMIT (as-of 2026-09-30) with these 12 data rows, each built from a different real row of `$D/tapes/NORTHGATE/S04*.csv` (rewritten into the Summit column order and formats; only the stated cell is altered): 1 clean; 2 VIN in lower case; 3 VIN with two hyphens inserted; 4 origination date written as `DD-Mon-YYYY`; 5 original amount written without decimals (for example `23450`) and a balance below it; 6 last VIN digit `d` replaced by `(d+1) mod 10`; 7 empty VIN; 8 original amount `N/A`; 9 origination `12/31/2027`; 10 loan number `=1+1`; 11 an exact repeat of row 1; 12 `TOTAL,,,,,"$1.00",,,,,,`. Upload as analyst, wait for COMPLETED, `get "$H/v1/tapes/<id>/items?limit=50" $T`.
- **Pass**: Rows 1 to 5 are accepted (disposition `ACCEPTED_NEW`, whatever the local checks later decide); row 1 has no issue; rows 2, 3, 4, 5 carry exactly one FIXED issue each: `VIN_CASE_NORMALISED`, `VIN_SEPARATORS_REMOVED`, `DATE_FORMAT_NORMALISED`, `AMOUNT_FORMAT_NORMALISED`. Rows 6 to 12 are REJECTED with, in order, `VIN_CHECK_DIGIT_INVALID`, `MISSING_REQUIRED_FIELD`, `AMOUNT_UNPARSEABLE`, `DATE_IN_FUTURE`, `UNSAFE_CHARACTERS`, `DUPLICATE_VIN_IN_FILE`, `NON_DATA_ROW` among their problems. `counts.rows` is 12 and `counts.rows_rejected` is 7. `python3 -m pytest bank_agent/tests/unit -k clean -q` passes with at least 30 tests.

### BANK-02 Raw rows are stored exactly and fingerprinted
- **Requirement**: SPEC 10.1 and 10.2.
- **Procedure**: Continue from BANK-01 with the uploaded file `F` and its submission id `SID`. `sha256sum F` versus `file_sha256` from `GET /v1/tapes/SID`. `adm harbor "select raw_payload from loans_raw where submission_id='SID' and row_number=2"` versus the second data row of `F`. Reviewer script: recompute `row_sha256` for each row as SHA-256 of the canonical JSON (SPEC 4) of `raw_payload` and `rows_sha256` per SPEC 10.2; compare with the stored values.
- **Pass**: The file hash is equal. `raw_payload` maps the original header texts to the cell texts unchanged (the lower case VIN is still lower case, nothing trimmed). All recomputed row hashes and the rows hash equal the stored ones.

### BANK-03 Local checks decide as specified, with evidence
- **Requirement**: SPEC 11.
- **Procedure**: STATE-RUN. As Harbor analyst list the items of the S10 tape; pick one row per failing pattern using only the API (`checks` with FAIL). For each, `get $H/v1/loans/<loan_id>/lineage`. Also `adm harbor "select count(*) from local_checks where reason_code is null or evidence = '{}'::jsonb"`, `adm harbor "select count(*) from (select loan_id,submission_id from local_checks group by 1,2 having count(*)<>4) x"`, and `adm harbor "select check_type,result,reason_code,count(*) from local_checks group by 1,2,3 order by 1,2,3"`. Run `python3 -m pytest bank_agent/tests/unit -k "check" -q`.
- **Pass**: Held loans show the reason codes of SPEC 11.1 (`NO_TITLE_RECORD`, `LIENHOLDER_MISMATCH`, `NO_PAYMENT_IN_WINDOW` all occur in S10). Every lineage check has the evidence keys of SPEC 11.2. Both counts are 0. The grouped listing contains only (type, result, reason) combinations that SPEC 11.1 allows, and includes `UNKNOWN / TITLE_PENDING_WINDOW` and `UNKNOWN / FIRST_PAYMENT_NOT_DUE`. Unit tests pass and include boundary ages 30 and 31 days and 44 and 45 days (shown by `-k` selection or by reading the test names).

### BANK-04 Tokenisation is exactly the specified two-holder blind OPRF
- **Requirement**: SPEC 9; vector reproduced; values objects sound.
- **Procedure**: `python3 -m pytest bank_agent/tests -k "vector or oprf or token" -q`. Reviewer script, independent of product code, using `oprf` directly: for one LOCKED Harbor loan from STATE-RUN (terms read with `adm`, LEI from `lenders`), compute the eight tokens with the scalars `FAKE_KH1_SCALAR_B64` and `FAKE_KH2_SCALAR_B64` by direct evaluation (SPEC 9.2, 9.3) and compare with `loan_tokens`. `python3 -c "from onepledge_bank_agent.domain import Vin; print(repr(Vin.parse('1HGCM82633A004352'))); Vin.parse('1HGCM82633A004353')"`. `grep -rnE "float\(|: float|-> float" bank_agent/src/onepledge_bank_agent/domain`.
- **Pass**: Tests pass and include a test that loads `contracts/examples/vectors/tokenization.json` and a Hypothesis test of series equals direct evaluation. All eight independently computed tokens equal the stored ones. The repr shows no VIN characters. The altered VIN raises `InvalidVin`. The grep prints nothing.

### BANK-05 Registry results, loan states and certificates agree
- **Requirement**: SPEC 13.5, 13.6, 15.2.
- **Procedure**: STATE-RUN. For each bank: `adm $b "select count(*) from loans where state='PENDING'"`; `adm $b "select count(*) from loans l where state in ('LOCKED','CONFLICT','RELEASED') and not exists (select 1 from registry_request_items i where i.loan_id=l.loan_id and i.result = case l.state when 'CONFLICT' then 'CONFLICT' when 'RELEASED' then 'RELEASED' else 'LOCKED' end)"`; `adm $b "select count(*) from registry_requests r where response_status in (200,201) and not exists (select 1 from certificates c where c.request_id=r.request_id and c.verified)"`; `adm $b "select count(*) from tape_submissions where status<>'COMPLETED'"`; `adm $b "select count(*) from loans where state='LOCKED'"` versus `tc $REG/_test/stats` `by_member[M].active_locks`. For one certificate id from `GET /v1/certificates`, fetch the detail and verify the Ed25519 signature over the canonical JSON of `body` with `REGISTRY_SIGNING_PUBLIC_KEY_B64` in a reviewer script; compare `summary` with the tape counts.
- **Pass**: All four counts are 0 for every bank. LOCKED loan counts equal the registry's active locks per member. The reviewer's own signature check succeeds and the summary equals the item counts of that request.

### BANK-06 Both banks are alerted, each about its own loan only
- **Requirement**: Scene 3 semantics; BUILD_PLAN release checklist lines 1 and 2.
- **Procedure**: STATE-RUN. As Northgate analyst `get "$N/v1/alerts?limit=200"`; as Harbor analyst `get "$H/v1/alerts?limit=200"`; save both. `grep -ciE "harbor|$BANK_HARBOR_MEMBER_ID" northgate_alerts.json`; `grep -ciE "northgate|calder|$BANK_NORTHGATE_MEMBER_ID|$BANK_CALDER_MEMBER_ID" harbor_alerts.json`. Print three `explanation_text` values of different (`role`, `match_kind`, `match_basis`). `grep -cE "[0-9]{6,}|[A-HJ-NPR-Z0-9]{17}" ` over the explanation texts only.
- **Pass**: Northgate has CLAIMANT alerts with `match_kind` EXACT; Harbor has HOLDER alerts for the same `registry_case_id` values. Both identity greps count 0. Alerts with `match_kind` LIKELY exist with `match_basis` ASSET and with `match_basis` FIELDS (at Calder and at the holders), each listing `kinds_matched` and `kinds_not_matched`. Explanation texts are plain sentences per SPEC 14.3, name the agreeing and differing fields, and the last grep counts 0.

### BANK-07 Release, then another bank locks with no alert
- **Requirement**: Scene 7; SPEC 14.6; SPEC 24 item 1.
- **Procedure**: STATE-RUN. `adm harbor "select count(*) from loans where state='RELEASED'"`. `adm harbor "select count(*) from (select vin from loans where facility_id=(select facility_id from facilities where code='HARBOR-SUMMIT') group by vin having count(*)=2 and bool_or(state='RELEASED') and bool_or(state='LOCKED')) x"`. For the released VINs present at Northgate: `adm northgate "select state,count(*) from loans where vin in (<released vins>) group by 1"` and `adm northgate "select count(*) from alerts a join loans l using(loan_id) where l.vin in (<released vins>)"`. Then, with a Harbor credit officer token, `POST $H/v1/releases` for one LOCKED loan id (as-of today), wait for COMPLETED, and repeat the identical call.
- **Pass**: Released count is positive. The second query is positive (a released row and a new locked row for the same car at Harbor). At Northgate all those loans are LOCKED and the alert count is 0. The release call returns 202, the loan becomes RELEASED, the registry's active lock count for Harbor drops by 1, and the identical repeat returns 409 (`TAPE_DUPLICATE` or `LOAN_NOT_LOCKED`).

### BANK-08 Case handling: review, position, clearance, resubmission
- **Requirement**: SPEC 8.5, 14.4, 14.5.
- **Procedure**: STATE-RUN. Pick at Calder a CLAIMANT alert with `match_basis` FIELDS, and the HOLDER alert with the same `registry_case_id` at Harbor. (1) As Calder analyst: `POST /v1/alerts/<id>/review {"action":"SUBMIT_POSITION","position":"DATA_ERROR"}`. (2) As Calder analyst `START_REVIEW`; repeat `START_REVIEW`. (3) As Calder officer `SUBMIT_POSITION DATA_ERROR`; then `SUBMIT_POSITION CONFIRMED_DOUBLE_PLEDGE`. (4) As Calder officer `POST /v1/loans/<loan>/resubmit`. (5) At Harbor: analyst `START_REVIEW`, officer `SUBMIT_POSITION DATA_ERROR`. (6) `POST /v1/registry/sync` at both as officers; get both alerts. (7) Calder officer `POST /v1/loans/<loan>/resubmit` twice. (8) For a different case with basis ASSET, both officers start review and submit `CONFIRMED_DOUBLE_PLEDGE`, sync, get both alerts.
- **Pass**: (1) 403. (2) 200 with status REVIEWING, then 409 `ALERT_TRANSITION_INVALID`. (3) 200 with `bank_position` DATA_ERROR and status still REVIEWING, then 409 `POSITION_ALREADY_RECORDED`. (4) 409 `LOAN_NOT_RESUBMITTABLE`. (6) Both alerts are CLEARED with `case_status` CLEARED_DATA_ERROR. (7) First call 200 with the loan LOCKED; second call 409 `LOAN_NOT_RESUBMITTABLE`; the registry's submission count for Calder (`tc $REG/_test/stats`) grows by exactly 1 over step 7 and the loan stays LOCKED. (8) Both alerts ESCALATED with `case_status` CONFIRMED; the claimant loan is still CONFLICT and the holder loan still LOCKED.

### BANK-09 State machines are enforced in the database too
- **Requirement**: SPEC 8.3, 8.4, 12.4.
- **Procedure**: STATE-RUN, as the owner role of Harbor (`own harbor "$BANK_HARBOR_DB_OWNER_PASSWORD" "..."`): (a) `update loans set state='LOCKED' where loan_id=(select loan_id from loans where state='RELEASED' limit 1)`; (b) `update loans set state='PENDING' where loan_id=(select loan_id from loans where state='LOCKED' limit 1)`; (c) `update loans set original_amount_cents=original_amount_cents+1, current_balance_cents=current_balance_cents where loan_id=(select loan_id from loan_tokens limit 1)`; (d) `update tape_submissions set status='RECEIVED' where status='COMPLETED'`; (e) `update tape_submissions set file_sha256=repeat('0',64)`; (f) `insert into loans (...)` a second non-released row with an existing `(facility_id, vin)`. `python3 -m pytest bank_agent/tests/unit -k "transition or state" -q`.
- **Pass**: (a) to (e) each fail with the guard errors of SPEC 12.4 (SQLSTATE `OP002`, `OP002`, `OP003`, `OP004`, `OP004`) and change no row. (f) fails with a unique violation. Unit tests pass and cover every legal transition and reject every other pair for all three state machines.

### BANK-10 The implementation equals the contracts
- **Requirement**: SPEC 18.1, 21.4.
- **Procedure**: `make contracts-check`. `python3 -m pytest -m contract -q`. STATE-RUN: reviewer script loads `contracts/bank_agent.yaml`, and for 10 GET endpoints of the reviewer's choice (including `/v1/tapes/{id}/items`, `/v1/loans/{id}/lineage`, `/v1/integrity`, `/v1/audit-log`) validates the live JSON against the response schema with `jsonschema` (Draft 2020-12, components resolved). With `EXPOSE_API_DOCS` at its default: `curl -s -o /dev/null -w "%{http_code}" $H/docs $H/openapi.json`.
- **Pass**: The checker ends with `contracts-check: OK`. Contract tests pass and include a test comparing the app's routes, methods and `x-roles` with the YAML. All 10 live responses validate. Both doc URLs return 404.

---

## 4. Reliability

### REL-01 Kill the agent mid-pipeline, restart, same result
- **Requirement**: BUILD_PLAN "a crashed bank agent resumes where it stopped"; SPEC 13.
- **Procedure**: `make down; make db-reset && make data && make seed && make up` (full profile, `D=var/data/full-20261117`). As Harbor analyst upload `S01_HARBOR-SUMMIT_BACK_BOOK.csv` (BACK_BOOK, 2026-09-30). Poll `GET /v1/tapes/<id>` every 0.2 s; as soon as `status` is `CLEANED`, run `kill -9 $(cat var/run/bank-harbor.pid)`. Confirm the process is gone, note the status in the database (`adm harbor "select status from tape_submissions"`), run `make up`, wait for COMPLETED. Repeat the whole procedure twice more, killing when the status is `CHECKED` and when it is `TOKENIZED` (if the tape reaches COMPLETED before the kill lands, reset and try again; a kill at any moment between upload and completion is a valid run as long as three different moments are exercised). After each run: `adm harbor "select count(*) from (select loan_id,submission_id from local_checks group by 1,2 having count(*)<>4) x"`; `adm harbor "select count(*) from (select loan_id,key_version from loan_tokens group by 1,2 having count(*)<>8) x"`; `adm harbor "select count(*),count(distinct idempotency_key),count(*) filter (where response_status in (200,201)) from registry_requests"`; `tc $REG/_test/stats` for Harbor; `adm harbor "select state,count(*) from loans group by 1 order by 1"`; `adm harbor "select count(*) from certificates where verified"`. Finally run once without killing on a fresh reset and record the same figures.
- **Pass**: In all three kill runs the tape reaches COMPLETED after restart with no manual action other than `make up`. Both anomaly counts are 0. The three request figures are equal to each other and to the registry's submission count for Harbor. Loan counts per state, registry active locks and verified certificate counts are identical to the run without a kill.

### REL-02 Every crash point is covered by an automated test that compares against a clean run
- **Requirement**: SPEC 21.3.
- **Procedure**: `python3 -m pytest bank_agent/tests/integration -k "crash or resume or fault" -v 2>&1 | tail -40`. Read the test module.
- **Pass**: Tests pass. There is one test (or parametrised case) for each of the nine crash points listed in SPEC 21.3, plus the middle batch 503 case, the holder 2 failure case, the outside records outage case, the max attempts then retry case and the two workers case. Each test builds the expected state from an uninterrupted run on a second database (not from literals) and compares loan states, tokens, registry locks and idempotency keys.

### REL-03 A duplicate tape is refused and changes nothing
- **Requirement**: SPEC 10.1 step 3.
- **Procedure**: STATE-RUN. Record `adm harbor "select (select count(*) from tape_submissions),(select count(*) from loans_raw),(select count(*) from loans),(select count(*) from registry_requests)"` and `tc $REG/_test/stats`. As Harbor analyst upload `S01` again with the same facility, kind and date; then again with a different `as_of_date`. Record the figures again.
- **Pass**: Both uploads return 409 `TAPE_DUPLICATE` with the existing submission id in `details`. All database figures and the registry statistics are unchanged. One new audit entry `TAPE_UPLOAD_REJECTED` exists per attempt.

### REL-04 A lost registry response does not lock twice
- **Requirement**: BUILD_PLAN "a retried request never locks twice".
- **Procedure**: STATE-EMPTY. `tc -X POST $REG/_test/fail-next -H 'content-type: application/json' -d '{"count":1,"status":503,"when":"AFTER_COMMIT"}'`. As Harbor analyst upload `S01` (small). Wait for COMPLETED. `adm harbor "select attempts,response_status from registry_requests order by batch_index"`; `tc $REG/_test/stats`; `adm harbor "select count(*) from loans where state='LOCKED'"`; `tc $REG/_test/received | python3 -c "import sys,json,collections;r=[x for x in json.load(sys.stdin)['requests'] if x['path']=='/v1/submissions'];print(len(r), len(set(x['body'] for x in r)))"`.
- **Pass**: The tape completes. One request shows `attempts` 2 and `response_status` 200 (a replay). The registry counts one submission per request row (not two). LOCKED loans equal the registry's active locks for Harbor. The received bodies show the retry was sent with an identical body (2 received, 1 distinct for a one batch tape).

### REL-05 A dependency outage delays work and corrupts nothing
- **Requirement**: SPEC 11.4, 13.7.
- **Procedure**: STATE-EMPTY. `kill $(cat var/run/outside-records.pid)`. Upload `S01` (small) at Harbor. Wait 15 seconds. `get $H/v1/tapes/<id>`; `adm harbor "select state,count(*) from loans group by 1"`; `adm harbor "select count(*) from local_checks"`; `adm harbor "select action,count(*) from audit_log where action like 'TAPE_STAGE_ATTEMPT%' group by 1"`. `make up`. Wait for COMPLETED. Compare final loan state counts with the S01 counts of a normal run (from STATE-RUN's scorecard or a second clean run).
- **Pass**: During the outage the tape stays at CLEANED with `attempts` at least 1, no loan is HELD_LOCAL, and attempt failures are audited. After `make up` the tape completes without any other action, and final state counts equal the normal run.

### REL-06 Two workers do not do the same work twice
- **Requirement**: SPEC 13.1 step 3.
- **Procedure**: Full profile, fresh as in REL-01. Upload `S01` at Harbor and immediately start, in two other shells after `eval "$(onepledge-eval stack env bank-harbor)"`, `onepledge-bank worker --once` in each. Wait for COMPLETED. Run the anomaly and request queries of REL-01.
- **Pass**: Same pass condition as REL-01 (anomaly counts 0, request figures equal, states identical to the clean run). Both extra workers exit 0.

### REL-07 A simultaneous pledge gives exactly one lock
- **Requirement**: Scene 8.
- **Procedure**: STATE-RUN. Read the RACE_PAIR cases from the answer key of `$D` (the reviewer may read it for auditing). For each pair's VIN: `adm harbor "select state from loans where vin='<vin>'"` and `adm calder "select state from loans where vin='<vin>'"`. `python3 -m pytest contracts/fakes/tests -k "race or concurrent" -q`.
- **Pass**: For every pair exactly one bank has LOCKED and the other CONFLICT. The doubles' tests include a test that submits the same asset token from two members concurrently at least 100 times and asserts exactly one lock each time, and it passes.

---

## 5. Security

### SEC-01 History tables are insert-only for the app role and for the owner
- **Requirement**: SPEC 12.2, 12.4.
- **Procedure**: STATE-RUN. For each table `t` in `loans_raw audit_log cleaning_issues submission_items local_checks loan_tokens certificates checkpoints_seen integrity_runs`: as the app role run `update t set <first non-key column> = <same column>`, `delete from t`, `truncate t`; as the owner role run the same three statements. Record row counts before and after.
- **Pass**: All 27 app role statements fail with `permission denied` (SQLSTATE 42501). All 27 owner statements fail with the insert-only trigger error (SQLSTATE `OP001`). Row counts are unchanged.

### SEC-02 The runtime role has least privilege
- **Requirement**: SPEC 12.2.
- **Procedure**: `adm harbor "select rolname,rolsuper,rolcreatedb,rolcreaterole from pg_roles where rolname like '${PX}_bank_harbor%'"`; `adm harbor "select tableowner,count(*) from pg_tables where schemaname='public' group by 1"`; `app harbor "$BANK_HARBOR_DB_APP_PASSWORD" "create table x(i int)"`; `app harbor ... "drop table loans"`; `app harbor ... "delete from loans"`; `app harbor ... "alter table loans_raw disable trigger all"`; connect as the Harbor app role to the Northgate database: `$PSQL "postgresql://${PX}_bank_harbor_app:$BANK_HARBOR_DB_APP_PASSWORD@$PG_HOST:$PG_PORT/${PX}_bank_northgate" -c "select 1"`. `tr '\0' '\n' < /proc/$(cat var/run/bank-harbor.pid)/environ | grep -c "_owner"`.
- **Pass**: Neither role is superuser, createdb or createrole. All tables are owned by the owner role. All four app role statements fail with permission errors. The cross-bank connection is refused. The running bank agent's environment contains no owner role credential (count 0).

### SEC-03 Credentials at rest
- **Requirement**: SPEC 16.1, 4 (service keys).
- **Procedure**: `adm harbor "select password_hash from users"`. `adm harbor "select count(*) from users where password_hash not like '\$argon2id\$v=19\$m=65536,t=3,p=4\$%'"`. Dump every text and jsonb column of every Harbor table to a file (`pg_dump --data-only` with the admin URL) and `grep -c -e "$DEMO_USER_PASSWORD" -e "$BANK_HARBOR_REGISTRY_API_KEY" -e "$BANK_HARBOR_JWT_SECRET" -e "opk_"`. Same grep over the outside records dump and over `var/log/`.
- **Pass**: Every hash is Argon2id with those parameters. The second query is 0. All greps count 0.

### SEC-04 Tokens are short-lived, bound to one bank, and revocable
- **Requirement**: SPEC 16.2.
- **Procedure**: STATE-RUN. Log in as Harbor analyst; decode the token payload (base64) and print the claims. Call `GET $H/v1/auth/me` with: the token; the token with its last signature character changed; a token whose header is `{"alg":"none"}` and empty signature; the same Harbor token against `$N/v1/auth/me`; no token; a token the reviewer signs with a wrong secret. Then as Harbor admin `PATCH /v1/users/<analyst id> {"role":"CREDIT_OFFICER"}` and call `/v1/auth/me` with the analyst's old token; restore the role. `python3 -m pytest bank_agent/tests -k "token and expir" -q`.
- **Pass**: Claims include `iss`, `aud` equal to `HARBOR`, `sub`, `role`, `tv`, `iat`, `exp` with `exp - iat == 900`. Only the first call returns 200; the others return 401 `UNAUTHENTICATED` with `WWW-Authenticate: Bearer`. The old token is rejected with 401 after the role change. A test proves an expired token is rejected using an injected clock.

### SEC-05 The permission matrix holds on every endpoint
- **Requirement**: SPEC 18.2.
- **Procedure**: `python3 -m pytest bank_agent/tests -k "permission or matrix or rbac" -q`. STATE-RUN spot checks with real tokens: analyst `GET /v1/users`; analyst `GET /v1/audit-log`; analyst `POST /v1/tapes` with kind RELEASE; analyst `POST /v1/releases`; analyst `POST /v1/integrity/verify`; officer `POST /v1/users`; admin `GET /v1/loans`; admin `GET /v1/alerts`; admin `GET /v1/tapes`; admin `GET /v1/certificates`; admin `GET /v1/loans/<id>/lineage`; admin deactivating themselves when they are the only admin. After the spot checks: `adm harbor "select count(*) from audit_log where action='ACCESS_DENIED'"`.
- **Pass**: The automated test covers every row and column of the SPEC 18.2 table (reading the test shows it is driven by the full matrix, not a sample) and passes. The first eleven spot checks return 403 `FORBIDDEN`; the last returns 409 `LAST_ADMIN`. At least eleven `ACCESS_DENIED` audit entries exist with the acting user.

### SEC-06 Login is rate limited and does not reveal accounts
- **Requirement**: SPEC 16.2.
- **Procedure**: STATE-EMPTY. `curl -s -i` login at Harbor with: a wrong password for `analyst@harbor.example`; an unknown email; save both bodies with the `request_id` removed and `diff` them. Then 5 more wrong passwords for the analyst, then the correct password. `adm harbor "select action,count(*) from audit_log where action like 'LOGIN%' group by 1"`. `adm harbor "select count(*) from login_attempts"`; `adm harbor "select * from login_attempts limit 3"`.
- **Pass**: Both failures are 401 `INVALID_CREDENTIALS` with identical bodies. From the sixth wrong password for the analyst on, responses are 429 `LOGIN_RATE_LIMITED` with a `Retry-After` header, including for the correct password. Audit shows `LOGIN_FAILED` and `LOGIN_RATE_LIMITED`. `login_attempts` holds an email hash, not an email.

### SEC-07 Malformed and malicious uploads are refused cleanly
- **Requirement**: SPEC 10.1, 16.6.
- **Procedure**: STATE-EMPTY, Harbor analyst, facility HARBOR-SUMMIT. Record `adm harbor "select (select count(*) from tape_submissions),(select count(*) from loans_raw)"`. Upload, one at a time: (1) an empty file; (2) header only; (3) `head -c 6000000 /dev/urandom`; (4) a valid tape re-encoded to Latin-1 with an `é` in a make cell (`iconv`); (5) a file containing a NUL byte; (6) a file with an unterminated quote; (7) a header missing the VIN column; (8) a header with two `VIN` columns; (9) a valid header with 20,001 data rows; (10) a row with a 600 character cell; (11) a zip archive named `tape.csv`; (12) a valid small tape with file name `../../etc/passwd`; (13) `as_of_date=2999-01-01`; (14) `kind=NONSENSE`; (15) a valid tape containing a row whose loan number is `=cmd|'/C calc'!A0` and a row whose borrower is `@SUM(1+1)`. Files 9 and 10 use short rows so they stay under 5 MiB. Record the figures again. `grep -c Traceback var/log/bank-harbor.jsonl`.
- **Pass**: Status and code, in order: 422 `TAPE_EMPTY`; 422 `TAPE_EMPTY`; 413 `TAPE_TOO_LARGE`; 422 `TAPE_NOT_UTF8`; 422 `TAPE_MALFORMED`; 422 `TAPE_MALFORMED`; 422 `TAPE_HEADER_INVALID`; 422 `TAPE_HEADER_INVALID`; 413 `TAPE_TOO_LARGE`; 413 `TAPE_TOO_LARGE`; 422 (`TAPE_NOT_UTF8` or `TAPE_MALFORMED`); 202 with a stored `file_name` that contains no `/` and no `..`; 422 `VALIDATION_ERROR`; 422 `VALIDATION_ERROR`; 202 and, once COMPLETED, both rows REJECTED with `UNSAFE_CHARACTERS`. No response is a 500. Only uploads 12 and 15 added rows. Every response has content type `application/json` and the error envelope. The traceback grep counts 0.

### SEC-08 Responses never echo submitted values
- **Requirement**: SPEC 4, 16.6.
- **Procedure**: STATE-RUN. With canary `ZQXJ7CANARY9`: send it as a field value in the body or parameter of every POST and PATCH endpoint and as a query value on 5 GET endpoints (`state=`, `status=`, `kind=`, `limit=`, `facility_id=`), as a path id, as the email in a login, and as a VIN in `POST /v1/loans/search` (use `ZQXJ7CANARY9AAAAA` padded to 17). Collect every response body and header. `grep -c ZQXJ7CANARY9` over all of them. Inspect three 422 bodies.
- **Pass**: The grep counts 0. Every 422 has `details` entries with `field` and `issue` only (no `input`, no `ctx`, no `url`). No response exposes a stack trace, a SQL fragment, a file path or a library name.

### SEC-09 No raw loan value in any log
- **Requirement**: BUILD_PLAN section 7 rule; SPEC 16.5.
- **Procedure**: After `make demo` (or STATE-RUN). Reviewer script: collect every VIN (as written and upper cased with separators removed), borrower id, and loan number from all tape files of the run; for every line of every file in `var/log/`, parse the JSON, drop the `ts` key, and search the remaining text (case-insensitive) for each value as a whole token. Also `grep -cE "/v1/(titles|contracts)/[A-Za-z0-9]{17}|vin=" var/log/*`. Also provoke errors first (run SEC-07 and SEC-08 before scanning). `python3 -m pytest bank_agent/tests -k "redact or canary or log" -q`. `python3 -c` a snippet that logs `vin='1HGCM82633A004352'` and an unknown key through the product's configured logger and prints the output.
- **Pass**: Zero hits in the scan. The URL grep counts 0. Tests pass and include the canary pipeline test of SPEC 16.5. The snippet's output has the unknown key dropped and named in `redacted_keys` and no VIN text.

### SEC-10 No raw loan value reaches the registry or the key holders
- **Requirement**: BUILD_PLAN "the registry never sees a raw value".
- **Procedure**: STATE-RUN. `tc $REG/_test/received > reg.json; tc $KH1/_test/received > kh1.json; tc $KH2/_test/received > kh2.json; tc $REG/_test/dump > dump.json`. With the value list of SEC-09 plus, for 200 random tape rows, the amount (as cents, as `d.dd`, as `$d,ddd.dd`) and the origination date (ISO, `MM/DD/YYYY`), search the four files for whole-token hits. Print the distinct JSON keys present in the request bodies.
- **Pass**: Zero hits. Request bodies to the registry contain only the keys of `SubmissionRequest` and `CasePositionRequest`; bodies to the holders only those of `EvaluateRequest`; every token matches `^[0-9a-f]{64}$` and every element is 44 base64 characters.

### SEC-11 A tampered certificate is detected
- **Requirement**: SPEC 15.2.
- **Procedure**: `python3 -m pytest bank_agent/tests -k "certificate" -v`. STATE-RUN. As Harbor officer take a `certificate_id` `K` from `GET /v1/certificates` and confirm `POST /v1/integrity/verify` reports OK. Then as superuser `adm harbor "alter table certificates disable trigger all; update certificates set body = jsonb_set(body,'{summary,locked}','999') where certificate_id='K'; alter table certificates enable trigger all"`. As Harbor officer `POST /v1/integrity/verify`, then `GET /v1/integrity`.
- **Pass**: Tests pass and cover all four failure reasons of SPEC 15.2 (unknown key, body hash mismatch, bad signature, content mismatch), using the registry double's `CERTIFICATE_BODY` tamper for at least one and asserting the tape ends FAILED with `CERTIFICATE_INVALID`. After the database edit, the report has `status` BROKEN, `certificates.status` BROKEN, and `certificates.failures` lists `K` with `BODY_HASH_MISMATCH`.

### SEC-12 A rewritten ledger or forged checkpoint is detected and halts submissions
- **Requirement**: BUILD_PLAN "others can check us without trusting us"; SPEC 15.3, 15.4.
- **Procedure**: `python3 -m pytest bank_agent/tests -k "checkpoint" -q`. STATE-RUN. Confirm `GET /v1/integrity` at all three banks has `registry_chain.status` OK. `tc -X POST $REG/_test/tamper -H 'content-type: application/json' -d '{"mode":"REWRITE_HISTORY","seq":3}'`. As each bank's officer `POST /v1/registry/sync`, then `GET /v1/integrity`. Then as Harbor officer `POST /v1/releases` for one LOCKED loan and wait for a terminal status; `tc $REG/_test/stats` before and after. Separately (fresh STATE-RUN): `tc -X POST $REG/_test/tamper -d '{"mode":"BAD_CHECKPOINT_SIGNATURE"}'`, sync Harbor, get integrity. Separately (fresh STATE-RUN): `tc -X POST $REG/_test/tamper -d '{"mode":"EDIT_IN_PLACE","seq":3}'`, Harbor officer `POST /v1/integrity/verify`.
- **Pass**: Tests pass and cover all six failure reasons of SPEC 15.3. After the rewrite all three banks report `status` BROKEN with a `registry_chain.failures` entry whose reason is `CHAIN_BREAK` or `HEAD_MISMATCH` and a `first_bad_event_seq`. The release ends FAILED with `REGISTRY_INTEGRITY_BROKEN` and the registry's submission count for Harbor did not change. The bad signature case reports `BAD_SIGNATURE`. The in-place edit is found by deep verification with `first_bad_event_seq` 3. Each failure is in `checkpoints_seen` with `verified = false` and in the audit log as `CHECKPOINT_REJECTED` (or, for deep verification, in `integrity_runs`).

### SEC-13 Secrets come only from the environment and are never committed or printed
- **Requirement**: SPEC 3.1, 16.7.
- **Procedure**: `git ls-files | grep -E "(^|/)\.env$"`; `git check-ignore .env`; `grep -vE "^\s*#|^\s*$" .env.example | grep -iE "PASSWORD|SECRET|KEY|SEED|SCALAR" | grep -v CHANGE_ME`; `stat -c %a .env`; for every secret value in `.env`: `git grep -c -F "<value>"`; `git grep -nE "opk_[A-Za-z0-9_-]{20,}|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|password\s*=\s*[\"'][^\"']+[\"']" -- . ':!contracts/examples' ':!docs'`; `grep -rn "os.environ\|os.getenv" --include=*.py */src contracts/fakes/src | grep -v "/settings.py"`; after `eval "$(onepledge-eval stack env bank-harbor)"`, `python3 -c` printing `repr()` of the bank agent settings loaded from the environment; start `onepledge-bank serve` with `JWT_SECRET` unset and with it set to `short`.
- **Pass**: `.env` is not tracked and is ignored. Every secret in `.env.example` is `CHANGE_ME` (derived public values and ids excepted). `.env` mode is 600. No secret value from `.env` occurs in the repository. The pattern grep prints nothing. The `os.environ` grep prints nothing. The repr shows no secret value. Both bad starts exit non-zero with one sentence naming the variable and no traceback and no value.

---

## 6. Auditability and transparency

### AUD-01 Every user and system action is in the audit log
- **Requirement**: SPEC 17.1, 17.2.
- **Procedure**: STATE-EMPTY. Note `adm harbor "select coalesce(max(seq),0) from audit_log"`. Perform at Harbor: a failed login; a successful analyst login; analyst `GET /v1/users` (denied); an S01 upload to COMPLETED; the same upload again; analyst `GET /v1/loans/<id>/lineage`; officer `GET /v1/audit-log`; admin creating a user and deactivating it; officer `POST /v1/registry/sync`; officer `POST /v1/integrity/verify`. Then `adm harbor "select seq,actor_type,actor_user_id is not null,action,object_type,outcome from audit_log where seq > <noted> order by seq"` and `adm harbor "select action,count(*) from audit_log group by 1 order by 1"`.
- **Pass**: Entries exist for: `LOGIN_FAILED`, `LOGIN_SUCCEEDED`, `ACCESS_DENIED` (DENIED), `TAPE_UPLOADED`, five `TAPE_STAGE_ADVANCED` (to CLEANED, CHECKED, TOKENIZED, SUBMITTED, COMPLETED), `LOAN_STATE_CHANGED` for every loan that changed state (count equals the number of state changes), `REGISTRY_REQUEST_SENT`, `REGISTRY_RESPONSE_RECEIVED`, `CERTIFICATE_VERIFIED`, `CHECKPOINT_VERIFIED`, `TAPE_UPLOAD_REJECTED`, `LINEAGE_VIEWED`, `AUDIT_LOG_VIEWED`, `USER_CREATED`, `USER_UPDATED`, `REGISTRY_SYNC_REQUESTED`, `INTEGRITY_VERIFICATION_RUN`. User actions carry the acting user id; pipeline steps have `actor_type` SYSTEM.

### AUD-02 The audit log is a verifiable hash chain
- **Requirement**: SPEC 17.1.
- **Procedure**: STATE-RUN. Reviewer script, independent of product code: read all `audit_log` rows of Harbor ordered by `seq` through `GET /v1/audit-log` (all pages, officer token) and recompute each `entry_hash` per SPEC 17.1 from the JSON fields; check `seq` is 1..n without gaps and each `prev_hash` equals the previous `entry_hash`. Then as owner: `insert into audit_log` a row with `seq = max+2`, and one with `seq = max+1` and a wrong `prev_hash`.
- **Pass**: Every recomputed hash equals the stored one; no gaps; links hold; the first `prev_hash` is 64 zeros. Both inserts fail with SQLSTATE `OP005`.

### AUD-03 An edit by a privileged user is detected and located
- **Requirement**: BUILD_PLAN "history cannot be edited quietly"; SPEC 15.6.
- **Procedure**: STATE-RUN. As Harbor officer `POST /v1/integrity/verify` and confirm `status` OK. As superuser: `adm harbor "alter table audit_log disable trigger all; update audit_log set detail = detail || '{\"x\":1}' where seq = 7; alter table audit_log enable trigger all"`. Verify again. Then `adm harbor "alter table loans_raw disable trigger all; update loans_raw set raw_payload = raw_payload || '{\"x\":\"1\"}' where raw_id = (select raw_id from loans_raw limit 1) returning submission_id; alter table loans_raw enable trigger all"`. Verify again. Then `adm harbor "alter table loans_raw disable trigger user"` and `GET /v1/integrity`; re-enable.
- **Pass**: After the first edit: `status` BROKEN, `audit_log.status` BROKEN, `first_bad_seq` 7. After the second: `raw_data.status` BROKEN and `bad_submission_ids` contains the returned submission id. With the trigger disabled: `database_controls.status` BROKEN and `LOANS_RAW_IMMUTABILITY_TRIGGER_ENABLED` is false. Each verification is a row in `integrity_runs` and an `INTEGRITY_VERIFICATION_RUN` audit entry.

### AUD-04 The lineage view shows everything a loan's state rests on
- **Requirement**: SPEC 17.3.
- **Procedure**: STATE-RUN. As an analyst fetch `/v1/loans/<id>/lineage` for: a LOCKED loan at Harbor that has a HOLDER alert; a CONFLICT loan at Northgate; a HELD_LOCAL loan at Harbor; a RELEASED loan at Harbor. After `eval "$(onepledge-eval stack env bank-harbor)"` run `onepledge-bank lineage show <id>` for the first.
- **Pass**: Each response validates against `LoanLineage` and contains: the tape (file name, 64 hex file hash, as-of date); the raw row with payload and row hash; cleaning issues (possibly empty); four checks with reason codes and evidence (for the pledge step); for the first two a registry request with idempotency key, item result, match fields, and a verified certificate with `as_of_seq`; eight tokens with key version for the first two and none for the HELD_LOCAL loan; the alert with explanation for the first two; audit entries including `LOAN_STATE_CHANGED`. The RELEASED loan shows two steps (pledge and release) with a certificate each. The CLI prints the same content as aligned sections with no traceback.

### AUD-05 The integrity report states what was verified, live
- **Requirement**: SPEC 15.5.
- **Procedure**: STATE-RUN. `get $H/v1/integrity $T | python3 -m json.tool` (analyst, then admin). `onepledge-bank integrity report` (Harbor environment loaded as in AUD-04). Compare `registry_chain.last_verified` with `tc $REG/_test/dump` latest checkpoint and with `curl -s $REG/v1/checkpoints/latest -H "X-API-Key: $BANK_HARBOR_REGISTRY_API_KEY"`. `adm harbor "select count(*), count(*) filter (where verified) from checkpoints_seen"`.
- **Pass**: The report validates against `IntegrityReport`, has the five sections, lists exactly the eleven control names of SPEC 15.5 all true, shows checkpoints received equal to verified and equal to the database counts, and `last_verified` equal to the registry's latest checkpoint (seq, last event seq, head hash). Analyst and admin both get 200. The CLI report is a readable aligned table with the same facts.

### AUD-06 Audit entries and evidence carry reasons, never raw values
- **Requirement**: SPEC 17.1 (detail), 11.2.
- **Procedure**: STATE-RUN, each bank. Export `select detail::text from audit_log` and scan with the value list and whole-token rule of SEC-09 and SEC-10 (VINs, borrower ids, loan numbers, amounts, dates of 200 random rows). `adm $b "select count(*) from audit_log where action='LOAN_STATE_CHANGED' and not (detail ? 'from' and detail ? 'to')"`. `adm $b "select count(*) from alerts where explanation_text is null or explanation_text='' or kinds_matched = '{}'"`. `adm $b "select count(*) from certificates where as_of_seq is null"`.
- **Pass**: Zero hits in the scan. All three counts are 0.

---

## 7. Code quality and architecture

### CODE-01 Lint, format and strict types are clean without suppressions
- **Requirement**: SPEC 2.2.
- **Procedure**: `make lint; make typecheck`. `grep -rn "noqa\|type: ignore" --include=*.py .`; `grep -nE "ignore_missing_imports|ignore_errors|disallow_|follow_imports|exclude" pyproject.toml */pyproject.toml contracts/fakes/pyproject.toml`; `grep -rn ": Any\|-> Any\|cast(" --include=*.py */src contracts/fakes/src | wc -l`.
- **Pass**: Both targets exit 0. Every `noqa` and `type: ignore` names a specific code and has a reason on the same line; there are at most 10 of each in the repository. The only mypy relaxation anywhere is `ignore_missing_imports` for `oprf` and `oblivious`, and no source or test tree is excluded from ruff or mypy. `Any` and `cast` appear only at JSON and third party boundaries (the reviewer reads each occurrence; more than 25 in total fails).

### CODE-02 Layering is real
- **Requirement**: BUILD_PLAN section 9; SPEC 2.1.
- **Procedure**: `python3 -m pytest -k "architecture or boundaries or imports" -q`. `grep -rnE "^(from|import) (sqlalchemy|psycopg|alembic)" bank_agent/src outside_records/src | grep -v "/infrastructure/"`; `grep -rnE "^(from|import) fastapi" bank_agent/src outside_records/src | grep -v "/interface/"`; `grep -rnE "^(from|import) httpx" bank_agent/src | grep -v "/infrastructure/"`; `grep -rnE "^(from|import) (onepledge_bank_agent\.(infrastructure|interface)|sqlalchemy|fastapi|httpx|structlog|oprf)" bank_agent/src/onepledge_bank_agent/domain bank_agent/src/onepledge_bank_agent/application`; `grep -rnE "select |insert into|update .* set " -i bank_agent/src/onepledge_bank_agent/{domain,application,interface}`; `grep -rnE "onepledge_(datagen|evaluation|contract_fakes|outside_records)" bank_agent/src`. Read three route functions and three use cases chosen at random.
- **Pass**: Architecture tests pass. Every grep prints nothing. The sampled routes validate, authorise, call one use case and map the result, with no business rule. The sampled use cases depend only on ports and domain classes, receive collaborators through their constructor, and are under 150 lines.

### CODE-03 No hardcoded answers; only evaluation reads the answer key
- **Requirement**: BUILD_PLAN section 9 ("no hardcoded expected answers; only evaluation reads the answer key").
- **Procedure**: `grep -rn "answer_key" --include=* -r bank_agent outside_records contracts | grep -v "^contracts/README.md"`; `grep -rn "answer_key" evaluation/src | grep -vE "onepledge_evaluation/(scorecard|demo)/"`; `grep -rnE "EXACT_COPY|COPY_AMOUNT|COPY_DATE|COPY_VIN|INVENTED|TWO_CARS|MOVED_AFTER|REFINANCED|RACE_PAIR|BLIND_SPOT|M_VIN|M_DATE|M_AMOUNT" bank_agent outside_records contracts/fakes`; `grep -rnE "20261117|S0[1-9]_|S1[0-5]_" bank_agent/src outside_records/src contracts/fakes/src evaluation/src/onepledge_evaluation/scorecard`; `grep -rnE "[A-HJ-NPR-Z0-9]{17}" bank_agent/src outside_records/src contracts/fakes/src`; `grep -rnE "manifest|reference\.json" bank_agent/src | grep -v seed`. Read `evaluation/src/onepledge_evaluation/scorecard/` in full. E2E-03 must have passed.
- **Pass**: The first four greps print nothing. The VIN shaped grep prints nothing. The bank agent reads `reference.json` only in the reference seeding command and never the manifest. The scorecard computes every number from observations and the key; it contains no literal count, VIN, id or per-seed constant, and no branch on a case id. E2E-03 passed.

### CODE-04 Small, finished, clean modules
- **Requirement**: SPEC 2.2 limits; CONTEXT standard.
- **Procedure**: Reviewer script over every `.py` under `*/src` and `contracts/fakes/src`: list files over 400 lines, functions or methods over 60 lines (by `ast` end line), and classes with more than 15 public methods. `grep -rnE "TODO|FIXME|XXX|HACK|TBD|lorem" . --include=*.py --include=*.md --include=*.yaml --include=*.toml --include=Makefile | grep -v "docs/layer1/RUBRIC.md"`; `grep -rnE "^\s*pass\s*$|NotImplementedError|\.\.\.\s*$" */src contracts/fakes/src | head -40` (read each); `grep -rn "print(" */src contracts/fakes/src | grep -v "/interface/"`; `grep -rl $'\xe2\x80\x94' . --exclude-dir=.git --exclude-dir=var`; `grep -rnE "except\s*:|except Exception:\s*$" -A1 */src contracts/fakes/src | grep -B1 "pass"`.
- **Pass**: No file over 400 lines and no function over 60 lines. No class with more than 15 public methods. No TODO style marker. `pass`, `...` and `NotImplementedError` occur only in abstract methods, protocols and exception class bodies. No `print(` outside `interface/` (CLI output goes through one output helper). No em dash in any file. No swallowed exception.

### CODE-05 Dependencies and schema are declared and consistent
- **Requirement**: SPEC 2.1, 12.5.
- **Procedure**: For each package, reviewer script: collect top-level third party imports under its `src/` and compare with `[project].dependencies` in its `pyproject.toml`; the same for `tests/` against `[project.optional-dependencies].dev`. Check each package has `py.typed`, console scripts per SPEC 19, and `requires-python >= 3.12`. `python3 -m pytest -k "migration or schema" -q`. With the owner URL of a scratch database: `alembic upgrade head`, `alembic downgrade base`, `alembic upgrade head` (via `onepledge-bank db migrate` equivalents or `alembic -c bank_agent/alembic.ini`), then `\dt`. `adm harbor "\d+ loans"` and three other tables compared with SPEC 12.1.
- **Pass**: No undeclared import and no declared but unused runtime dependency. Only packages allowed by SPEC 2.1 are used. Tests pass and include the model versus migration drift test. Up, down, up succeeds and leaves all 18 tables of SPEC 12.1 plus `alembic_version`. The sampled tables match SPEC 12.1 column for column, including checks, unique constraints and the partial unique index on `loans`.

### CODE-06 Object-oriented design with injected dependencies and named errors
- **Requirement**: BUILD_PLAN section 9; SPEC 8.
- **Procedure**: `ls bank_agent/src/onepledge_bank_agent/*/`. `grep -rnE "^[a-z_]+ *= *(\[|\{|dict\(|list\(|set\()" */src contracts/fakes/src | grep -v "__all__"`; `grep -rn "^global \|    global " */src contracts/fakes/src`; `grep -rnE "class .*\((Exception|ValueError|RuntimeError)\)" bank_agent/src | head`; `grep -rnE "raise (Exception|ValueError|RuntimeError)\(" bank_agent/src/onepledge_bank_agent/{domain,application}`; `grep -rnE "datetime\.(now|utcnow)|date\.today|uuid4\(" bank_agent/src/onepledge_bank_agent/{domain,application}`. Open the composition root (the one module that builds the object graph) and the classes named in SPEC 8.1, 8.2 and 8.6.
- **Pass**: The classes of SPEC 8.1 and 8.2, the use cases of SPEC 8.6 and the ports exist with those names. No module level mutable state and no `global`. All business errors derive from one `DomainError` and carry a `code`; domain and application raise no bare built-in exceptions. Clock and id generation are injected (last grep empty). Exactly one composition root wires implementations to ports; use cases never instantiate their own collaborators.

---

## 8. Tests

### TEST-01 The whole suite passes with the coverage gates
- **Requirement**: SPEC 2.2, 21.
- **Procedure**: `make test 2>&1 | tail -60`. `python3 -m pytest --collect-only -q | tail -3`. `python3 -m pytest --collect-only -q -m unit | tail -1`; same for `integration`, `contract`, `e2e`.
- **Pass**: Exit 0; 0 failed, 0 errors, 0 skipped, 0 xfailed. Coverage output shows at least 85% for `onepledge_bank_agent.domain` plus `application` and at least 85% for each of `onepledge_datagen`, `onepledge_outside_records`, `onepledge_contract_fakes`, `onepledge_evaluation.scorecard`. Every marker selects at least one test; there are at least 250 tests in total.

### TEST-02 Tests are honest
- **Requirement**: CONTEXT "no patchwork or hardcoding to make tests pass".
- **Procedure**: `grep -rnE "pytest\.(skip|xfail)|@pytest\.mark\.(skip|xfail)|assert True|assert 1 *== *1" --include=*.py .`; `grep -rnE "sqlite|:memory:" --include=*.py bank_agent outside_records`; `grep -rnE "mock\.patch|monkeypatch\.setattr|MagicMock|Mock\(" --include=*.py */tests contracts/fakes/tests | wc -l` and read each hit; `grep -rnE "if .*(PYTEST|pytest|TESTING|under_test)" */src contracts/fakes/src`; `grep -rn "answer_key" */tests contracts/fakes/tests | grep -vE "^(evaluation|datagen)/"`. Read 15 tests chosen at random across packages.
- **Pass**: First, second, fourth and fifth greps print nothing. Mocks are used only to replace a port or an external boundary, never the class under test. Product code has no test-mode branch. The sampled tests assert on behaviour and outcomes (not only that a call did not raise, and not on values copied from a previous run of the code under test without an independent derivation).

### TEST-03 Property based tests exist for the core rules
- **Requirement**: SPEC 21.1.
- **Procedure**: `grep -rln "from hypothesis" */tests contracts/fakes/tests`; `python3 -m pytest -k "property or hypothesis" -q` (or the files found); read them.
- **Pass**: Hypothesis tests exist and pass for all six properties of SPEC 21.1 (VIN check digit under one-character changes, cleaning idempotence, Money round trip, canonical JSON key order invariance, OPRF series equals direct and blind independence, idempotency key sensitivity).

### TEST-04 The tests catch real defects (mutation spot check)
- **Requirement**: CONTEXT "tests and feedback loops must be rigorous and correct".
- **Procedure**: On a scratch copy of the clone, apply each mutation alone, run `make test-fast` (and the named integration test where stated), then revert: (1) change one VIN weight (for example the first weight 8 to 7); (2) in the payments check change the window comparison so a payment on the first day of the window no longer counts; (3) make certificate verification return success without checking the signature; (4) make checkpoint verification skip the `prev_hash` link check; (5) derive the idempotency key without `batch_index`; (6) add `"vin"` to the logging allow-list, and separately remove the VIN shape scrub from the redaction processor; (7) let ANALYST perform `SUBMIT_POSITION` in `AccessPolicy`; (8) remove the `AMOUNT_100` bucket floor division (use the exact amount); (9) in the registry double make Rule B report EXACT when the amount differs; (10) drop the insert-only trigger on `audit_log` from the migration (run the integration tests).
- **Pass**: Each of the eleven mutations (item 6 is two) makes at least one test fail. A mutation that leaves the suite green fails this criterion.

### TEST-05 End to end test with two seeds runs inside the suite
- **Requirement**: SPEC 21.7.
- **Procedure**: `python3 -m pytest evaluation/tests -m e2e -v`. Read the test.
- **Pass**: It passes. It starts the real services as processes on free ports with a unique database prefix, uses two different seeds, asserts every scorecard gate for both, runs the scene 9 checks, and removes its databases and processes afterwards (`$PSQL -l | grep` shows no leftover test database; `pgrep -f onepledge` shows no leftover process).

---

## 9. Developer experience and docs

### DX-01 README and documents are complete and true
- **Requirement**: SPEC 2.4.
- **Procedure**: Read `README.md`, the five package READMEs, `docs/layer1/ARCHITECTURE.md`, `docs/layer1/THREAT_MODEL_L1.md`. Run every command shown in a fenced block of `README.md` in order on the fresh clone. Check 10 factual statements across the documents against the code (ports, variable names, table names, endpoint names, file paths).
- **Pass**: All files exist with the content listed in SPEC 2.4. Every README command runs as written. All 10 sampled statements are true. The README lists the demo logins without printing the password (it points to `DEMO_USER_PASSWORD` in `.env`), states the Docker path and that it was not run on the build machine, and links to the known limitations. THREAT_MODEL_L1 maps each layer 1 threat to a control, a file and a test or rubric id.

### DX-02 Make targets and the local stack work
- **Requirement**: SPEC 2.3, 20.2.
- **Procedure**: `make` (no argument); then on a fresh clone after `make setup`: `make env` twice; `make db-up` twice; `make migrate` twice; `make data` twice; `make seed` twice; `make up` twice; `make status`; `curl -s $H/healthz $N/healthz $C/healthz $OUT/healthz $KH1/healthz $KH2/healthz $REG/healthz`; `curl -s -o /dev/null -w "%{http_code}" $H/readyz`; `make down`; `make status`; `pgrep -fa "onepledge-(bank|outside|fake)"`; `make clean`.
- **Pass**: `make` lists every target of SPEC 2.3 with one line each. Every repeated target succeeds both times (idempotent) and the second `make env` does not change `.env`. All seven health endpoints answer `status: ok`; the two key holder and the registry responses contain `"test_double": true`; `/readyz` is 200. After `make down`, status shows every service down and `pgrep` prints nothing.

### DX-03 Expected failures give one clear sentence
- **Requirement**: SPEC 2.3 last paragraph.
- **Procedure**: (1) Move `.env` away and run `make up`. (2) Restore it, set `PG_ADMIN_URL` to port 1 and run `make db-up`. (3) Restore; start `python3 -m http.server $((PB+101))` and run `make up`. (4) `onepledge-datagen generate --seed 1 --profile nope --out x`. (5) `onepledge-bank tapes show not-a-uuid` with Harbor's environment loaded. (6) `onepledge-eval scorecard --seed 1 --profile small` with PostgreSQL URL wrong.
- **Pass**: Each prints a short message saying what is wrong and what to do, exits non-zero (2 for the usage error in 4), and prints no Python traceback.

### DX-04 Docker artefacts for teammates are present and well formed
- **Requirement**: SPEC 22.
- **Procedure**: `ls bank_agent/Dockerfile outside_records/Dockerfile contracts/fakes/Dockerfile docker-compose.layer1.yml .dockerignore`; `docker compose -f docker-compose.layer1.yml config -q; echo $?`; `docker compose -f docker-compose.layer1.yml config --services`; `grep -n "USER\|HEALTHCHECK\|FROM" */Dockerfile contracts/fakes/Dockerfile`; `grep -nE "PASSWORD|SECRET|KEY" */Dockerfile contracts/fakes/Dockerfile docker-compose.layer1.yml | grep -v '\${'`.
- **Pass**: All files exist. `config -q` exits 0 with the generated `.env`. Services: `postgres`, `provision`, `registry-fake`, `keyholder-1-fake`, `keyholder-2-fake`, `outside-records`, `bank-harbor`, `bank-northgate`, `bank-calder`. Each Dockerfile uses `python:3.12-slim`, a non-root `USER` and a `HEALTHCHECK`. No secret literal in any of the files (last grep empty).

### DX-05 Contracts are valid, exemplified, marked draft, and the doubles are labelled
- **Requirement**: Task brief; SPEC 7.
- **Procedure**: `make contracts-check`. `python3 -c "import yaml,sys;[yaml.safe_load(open(f'contracts/{n}.yaml')) for n in ('bank_agent','key_holder','registry','outside_records')]"`. `grep -n "DRAFT" contracts/README.md contracts/*.yaml | head`; `grep -rn "TEST DOUBLE\|test double" contracts/fakes/README.md contracts/fakes/src | head`; `ls contracts/examples/*/ | wc -l`. Break one example on a scratch copy (delete a required field) and run the checker.
- **Pass**: The checker prints `contracts-check: OK` and exits 0; on the broken copy it exits 1 and names the file. All four YAML files parse and declare `openapi: 3.1.0`. The README states the draft status, the pending approval by Shreyas and Vedant, and the additive-only rule. Every module of the doubles and their README say they are test doubles. Every contract operation that has a request or response body has at least one example file listed in `examples/index.json` (the registry's outbox acknowledgement returns 204 and has none).

---

## 10. Presentation quality

### PRES-01 The demo narrative is clean and professional
- **Requirement**: CONTEXT "the output a human sees is clean and professional"; SPEC 20.5.
- **Procedure**: `make demo 2>&1 | tee demo.txt`. `awk '{ if (length($0) > 100) n++ } END { print n+0 }' demo.txt`; `grep -cE "Traceback|Exception|DEBUG|WARNING|DeprecationWarning|\x1b\[" demo.txt`; `grep -nP "[^\x00-\x7F]" demo.txt | head`; scan `demo.txt` and `var/reports/*` for raw values with the SEC-09 script; read the whole output.
- **Pass**: No line over 100 columns. The grep counts 0. ASCII only. Zero raw value hits. The narrative has a header (data set, seed, services), one block per scene with the same structure (title, plain sentences with measured numbers, evidence list, result line), a closing summary table, the scorecard, report paths and total duration. Numbers in the narrative agree with `demo_report.json` and with the scorecard. A reader who has not seen the code can follow what happened in each scene. No leftover debugging text, no misaligned table, no misspelling in the fixed text.

### PRES-02 The scorecard is clear and its three forms agree
- **Requirement**: SPEC 20.4.
- **Procedure**: `make scorecard | tee sc.txt`; open `var/reports/scorecard.md` and `scorecard.json`.
- **Pass**: The console table has the sections and columns of SPEC 20.4, aligned, at most 100 columns, with thousands separators in counts. Every number on the console equals the JSON and the Markdown. The JSON has, per case type, planted, as expected, missed and the list of missed case ids; false alarms and false holds by group; data quality by kind; blind spots; privacy counts; gates with pass or fail each. A failing gate would be shown by name (verify by reading the rendering code path or the scorecard unit tests for a failing example).

### PRES-03 Command line tools are consistent and readable
- **Requirement**: SPEC 19.
- **Procedure**: For each console script (`onepledge-bank`, `onepledge-outside`, `onepledge-datagen`, `onepledge-eval`, `onepledge-contracts`, `onepledge-fake-keyholder`, `onepledge-fake-registry`): `--help` and each subcommand `--help`. STATE-RUN, after `eval "$(onepledge-eval stack env bank-harbor)"`: `onepledge-bank tapes list`; `tapes show <id> --items --rejected-only`; `alerts list`; `audit show --limit 10`; `integrity report`; `users list`; each again with `--json | python3 -m json.tool`; `onepledge-bank loans show 00000000-0000-4000-8000-000000000000; echo $?`.
- **Pass**: Every command and subcommand has help text with a one line description and described arguments. Tables have a title, headers, aligned columns, a count line; hashes are truncated to 12 characters with `...`; times are UTC ISO; no table prints a VIN, borrower or amount except `loans show` and `lineage show`. Every `--json` output parses. The unknown id prints one sentence to stderr and exits 1. No command prints a traceback or a secret.

### PRES-04 API errors are uniform and well written
- **Requirement**: SPEC 4, 18.3.
- **Procedure**: STATE-RUN. Provoke at least one response for each of these codes and save status, headers and body: `INVALID_CREDENTIALS`, `UNAUTHENTICATED`, `FORBIDDEN`, `VALIDATION_ERROR` (body, query and path variants), `NOT_FOUND`, `FACILITY_NOT_FOUND`, `TAPE_DUPLICATE`, `TAPE_TOO_LARGE`, `TAPE_NOT_UTF8`, `TAPE_EMPTY`, `TAPE_HEADER_INVALID`, `TAPE_MALFORMED`, `TAPE_NOT_RETRYABLE`, `LOAN_NOT_LOCKED`, `LOAN_NOT_RESUBMITTABLE`, `ALERT_TRANSITION_INVALID`, `USER_EMAIL_TAKEN`, `LAST_ADMIN`; also an unknown route and a wrong method. Stop the registry double and call `POST /v1/registry/sync` for `DEPENDENCY_UNAVAILABLE`.
- **Pass**: Every response is `application/json` with exactly the `ErrorResponse` shape, the status of SPEC 18.3, a `request_id` equal to the `X-Request-ID` header, and headers `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`. Each message is one plain sentence ending with a full stop, identical every time for the same code, with no identifiers, values, class names or library text. The unknown route and wrong method also return the envelope (404 and 405), not a framework default body.

### PRES-05 Logs are structured and quiet
- **Requirement**: SPEC 16.5.
- **Procedure**: STATE-RUN (a run with no tampering and no provoked errors). `for f in var/log/*.jsonl; do python3 -c "import sys,json;[json.loads(l) for l in open('$f')]" || echo BAD $f; done`. Reviewer script: per file, count lines by `level`, list distinct `event` values, and list keys outside the allow-list of SPEC 16.5. `grep -c "redacted_keys" var/log/*.jsonl`.
- **Pass**: Every line of every log file is one JSON object with `ts`, `level`, `event`, `service`. In a clean run there is no line at level `error` or `critical`, and at most a handful of `warning` lines each explained by the scenario (for example a conflict is not a warning). `event` values are fixed snake case names, at most about 60 distinct ones, none containing interpolated data. No key outside the allow-list appears. `redacted_keys` does not appear in a clean run (the product never tries to log a disallowed key).

### PRES-06 Written material is free of sloppiness
- **Requirement**: CONTEXT standard; task rules.
- **Procedure**: `grep -rl $'\xe2\x80\x94' . --exclude-dir=.git --exclude-dir=var`; `grep -rnE "TODO|TBD|FIXME|lorem|coming soon|placeholder" --include=*.md . | grep -v RUBRIC.md`; read every docstring of 10 random public classes and the module docstrings of 10 random modules; read `.env.example`, the Makefile help text, every fixed error message table and the alert explanation templates.
- **Pass**: No em dash in any tracked file. No placeholder text. Every public class and module sampled has a docstring that says what it is for in plain words and is accurate. `.env.example` documents every variable. Fixed texts (error messages, explanations, CLI and report strings) are grammatical, consistent in terminology (the same thing is always called by the same name: tape, submission, loan, code, lock, alert, case, certificate, checkpoint), and free of misspellings.
