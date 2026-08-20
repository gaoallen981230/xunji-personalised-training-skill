---
name: xunji-personalised-training
description: Build and review general-purpose personalised training plans for any user-defined direction, entirely offline or with optional Xunji import and explicitly confirmed writeback. Use when a user wants goal-led onboarding, a programme based on availability, equipment, experience, preferences, symptoms and recovery, a weekly review, an auditable draft, or a guarded Xunji API write with full read-back verification.
---

# Xunji Personalised Training

## Core contract

Separate the work into three layers:

1. Build a local user profile and current-phase goal model.
2. Produce and audit a draft using evidence, constraints, and explicit assumptions.
3. Access Xunji only when the user asks; write only after the user reviews the current draft and directly confirms that exact version.

Never treat this Skill as medical care, an outcome guarantee, or an official Xunji product. Never upload a profile, cache, training record, credential, or unpublished plan to another service.

## Route the request

- For general-purpose programme design with no Xunji request, remain offline and stop after the audited draft.
- For a first-time user, complete the minimum onboarding and validate a local profile.
- For a weekly review, read the requested dates with `include_full_data=true`, preserve user corrections, and distinguish observed evidence from inference.
- For a draft update, read the original record with full data, retain it unchanged as `original_xunji_payload`, clone it to `xunji_payload`, and make the smallest reviewed change in that clone.
- For a confirmed write, require a post-summary confirmation for the current payload digest, write, then verify with a fresh full-data read.

## 1. Establish the profile

Read [profile-and-onboarding.md](references/profile-and-onboarding.md) before first-time onboarding or whenever goals, availability, equipment, restrictions, or preferences have changed.

Ask for all material missing fields in one compact message. Infer only what Xunji records can support, such as recent frequency, movement exposure, logged loads, and completion signals. Do not infer health status, training age, goals, pain, or medical clearance from exercise records.

Copy `assets/user-profile.example.json` to a user-chosen local project path and replace synthetic values. The `xunji` block is optional for offline planning; omit it unless Xunji integration is requested. Do not store a real profile inside the Skill or its Git repository.

Validate it:

```bash
python3 scripts/validate_profile.py /local/path/training-profile.json
```

Resolve validation errors and user-visible `unconfirmed` items before programming.

## Connect Xunji when requested

Before the first Xunji import or export, tell the user to open the Xunji app and find their training data key (训练数据 Key). Treat it like a password. Ask them to inject it into the Agent's local process as `XUNJI_API_KEY` through a non-echoing, user-controlled mechanism rather than placing it in a profile, plan, project file, command history, or message. Do not query host credential stores.

Once the key is available, the Agent can import requested Xunji records for local review and export a reviewed plan back to Xunji. Possession of the key is only technical access; it is never permission to write. Keep the draft-summary-confirmation guard for every export.

## 2. Gather current evidence

Use the smallest evidence window that answers the request. For a weekly plan, prefer the recent two to six weeks plus the latest user check-in.

When Xunji access is authorised:

```bash
python3 scripts/xunji_client.py read --date YYYY-MM-DD --full --output /local/private/path/day.json
```

Follow [xunji-api-and-writeback.md](references/xunji-api-and-writeback.md). A version-3 read-cache entry must bind the full training-data-Key fingerprint, exact date, full/light mode, and fixed transaction epoch. Reject cache links or non-private ownership and permissions. Force-refresh any date after a manual correction.

Treat records conservatively:

- Count `done=true` as completion evidence.
- Count positive `trainedSeconds` as an observable signal even when `done=false`.
- Do not treat planned `time`, `duration`, distance, or `metrics` alone as completion.
- Keep structured RPE separate from RPE parsed from comments.
- Mark missing information as unknown; never invent load, RPE, symptoms, or completion.

## 3. Design the programme

Read [programme-design.md](references/programme-design.md) for the active goal types. Read [safety-and-evidence.md](references/safety-and-evidence.md) whenever the user reports pain, symptoms, a medical condition, pregnancy, a return from injury, or a performance test.

Apply priorities in this order:

1. Non-overridable stop rules and clinician constraints.
2. Current symptoms, recovery, and completion evidence.
3. Availability, location, equipment, and schedule.
4. Current phase and user-defined priorities.
5. Preferences and Skill defaults.

For every user-defined direction, require a real task or outcome and a success measure. Translate the direction into measurable capacities rather than assuming a template.

Use conservative defaults when evidence is incomplete. Change no more progression variables per movement or training purpose than the profile allows. Do not progress through new or worsening neurological, radiating, cardiovascular, or acute-injury symptoms.

## 4. Produce and audit a draft

Choose the template from the delivery boundary:

- For local planning with no Xunji import or export, start from `assets/offline-weekly-plan.example.json`. Use `schema_version: "personalised_training_plan_v1"`, keep each programme in `sessions[].programme`, omit all Xunji payload fields, and keep `write_control.remote_write_requested` exactly `false`.
- For Xunji import or export, start from `assets/weekly-plan.example.json`. Use `schema_version: "xunji_weekly_plan_v1"`. Keep analysis fields outside each `xunji_payload`; only `xunji_payload` may be sent to Xunji. A create omits `original_xunji_payload`. An update must include the exact imported full record as `original_xunji_payload`, while `xunji_payload` remains a full clone with reviewed value changes or additions.

Never convert an offline plan into a write implicitly. A Xunji export requires a separately reviewed Xunji-format draft. Never edit an original snapshot. Normal API updates must not remove original movements, sets, nested items, or metadata; ask the user to restructure destructively in the app and then force-refresh the date.

Every draft must contain:

- the profile identifier and week;
- facts, assumptions, corrections, and unconfirmed items;
- goal tags and a reason for each session;
- a concise change summary against the latest comparable evidence;
- exact progression variables and regression gates whenever a real week-to-week progression is proposed; a first week without comparable evidence may use an empty `progressions` list and must state that no progression was inferred;
- `status: "draft"`;
- for an offline draft, `remote_write_requested: false`; or
- for a Xunji draft, `explicit_confirmation_required: true` and `confirmed: false`.

Validate it:

```bash
python3 scripts/validate_plan.py /local/path/weekly-plan.json --profile /local/path/training-profile.json
```

For updates, the exact imported `original_xunji_payload` is the mandatory internal baseline. Declare every actual dose change exactly; an empty or incomplete declaration must fail before it can bypass progression limits or red-flag stops. Dose variables include sets, load, repetitions, duration, distance, calories, intensity or heart rate, difficulty, rest or density, tempo, range of motion, and typed nested metric paths. An imported legacy difficulty may remain unchanged, but any new or changed value must be exactly `easy`, `normal`, or `hard`. Target positions, recursively nested `items[].set` values, every target alias, and load units remain part of this comparison. An added set counts only as a set-count change when its complete target signature matches one comparable original set; never assemble evidence across different sets, and count a novel target as another dose variable. Adding another occurrence of an existing movement name is `exercise_selection`; compare dose only across occurrences present in the original. Unknown or unrelated metadata changes fail closed. If a declared progression is not fully and unambiguously covered by update snapshots, pass a comparable external baseline. A declared movement name that appears in any create operation always needs that external baseline and cannot borrow a same-name update snapshot. Treat an empty, unrelated, or unchanged baseline as an error rather than evidence.

`validate_plan.py` audits an explicitly supplied external baseline. The update-specific exact comparison against `original_xunji_payload` occurs again inside `prepare-write`; neither check substitutes for the other when both apply.

## 5. Show the draft and stop

Present the user with:

- dates and session purposes;
- movement order, sets, repetitions, time, distance, or load ranges;
- what changed and why;
- assumptions and items needing confirmation;
- safety or recovery gates;
- for a Xunji draft, the exact records that would be created or updated; for an offline draft, state that no remote record will be created.

Stop after the summary. A standing authorisation, an early “I confirm”, or phrases such as “looks good” do not authorise a write. Confirmation must occur after the current summary and identify the current week or draft.

## 6. Prepare a write manifest

This stage applies only to a reviewed `xunji_weekly_plan_v1` draft. An offline `personalised_training_plan_v1` draft is deliberately rejected by the write client and must never produce a manifest.

After the summary but before any network write, create a local manifest:

```bash
python3 scripts/xunji_client.py prepare-write \
  --plan /local/path/weekly-plan.json \
  --manifest /local/private/path/write-manifest.json \
  --profile /local/path/training-profile.json \
  --baseline /local/path/comparable-baseline.json
```

`--profile` is required. `--baseline` is required when a declared progression is not fully covered by the mandatory internal baseline in each update's bound original snapshot. It must contain comparable evidence and calculate the exact declared change. A draft whose declarations are fully covered by update snapshots, or which has no progression, may omit it. Show the returned digest with the write summary. The digest binds the complete profile, the optional external baseline, the outgoing records, and every update's original snapshot. If any bound file changes, regenerate the manifest and require new confirmation.

## 7. Write only after direct confirmation

After a later user message explicitly confirms the current digest and date range:

```bash
python3 scripts/xunji_client.py write \
  --plan /local/path/weekly-plan.json \
  --manifest /local/private/path/write-manifest.json \
  --profile /local/path/training-profile.json \
  --baseline /local/path/comparable-baseline.json \
  --expected-digest DIGEST_FROM_PREPARE \
  --write-confirmed
```

Use exactly the same unchanged plan, profile, and optional baseline files that were passed to `prepare-write`. If `--baseline` was omitted during preparation, omit it during write and verification as well.

The write command must refuse changed payloads, normalised-title collisions visible at preflight, missing identity on updates, remote changes since the reviewed full-data snapshot, and updates to sessions with completion evidence at any nested set depth. For an existing session, only reviewed title changes, recognised dose fields, and movement or set additions are mutable. Every newly created session, movement, set, and nested item uses the reviewed known-field allowlist; a planned `done` field must be false. Unknown or unrelated metadata additions, removals, or changes at the session, existing-movement, set, or nested-set level fail closed; make them in the app and force-refresh the date. Never add a bypass for these guards in normal operation.

Before dispatch, the client durably records a credential-namespace `write_intent`. An acknowledged response advances the marker to `verify_pending`; an uncertain response advances it to `ambiguous` when possible, while failure to advance leaves the conservative intent marker. Writing is not completion. Report the exact unresolved state and never resend while any unresolved marker exists.

The client must hold its exclusive training-data-Key-namespace/date lock throughout each read, write, or verification. Transaction state uses the fixed per-user root and must never follow a caller-selected `--cache-dir` or `XDG_CACHE_HOME`. New transaction directories, lock entries, and marker replacements require parent-directory synchronisation; a failure before upsert dispatch is a stop condition. A concurrent or stale lock is also a stop condition. Never delete a lock blindly; first establish whether another process or unresolved marker remains.

Xunji exposes no verified compare-and-swap, revision, ETag, idempotency, or server-side title-uniqueness precondition in the reviewed API contract. A fresh full-data preflight narrows but cannot remove the interval in which another authorised client may change a record before upsert. Two create-only clients can also both pass the duplicate-title preflight and create duplicates. Treat guarded writeback as non-atomic and not exactly once. Warn the user not to edit the same date in another client during writeback, require reconciliation after any uncertain outcome, and never describe either update or create as conditionally protected.

## 8. Verify the remote result

Wait through any rate-limit interval, then run:

```bash
python3 scripts/xunji_client.py verify \
  --plan /local/path/weekly-plan.json \
  --manifest /local/private/path/write-manifest.json \
  --profile /local/path/training-profile.json \
  --baseline /local/path/comparable-baseline.json
```

Use the same unchanged files as the other two stages. Require a matching credential-namespace `write_intent`, `ambiguous`, `verify_pending`, `reverify_pending`, `drifted`, or historical `fully_verified` marker, then perform a fresh full-data comparison of date, title, `localid`, preserved time fields, movement order, set counts, and target fields. A historical `fully_verified` marker must durably become `reverify_pending` before the read; timeout remains blocking, mismatch becomes `drifted`, and only an exact comparison restores that digest to `fully_verified`. A first-time mismatch keeps its original unresolved state. The same drifted digest cannot be rewritten; a different digest requires a new manifest, explicit confirmation, and live full-data preflight. Create and update records must each be the unique normalised-title match; an update's unique `localid` must select that same record. Do not resend an unresolved or already successful record, and do not claim current success from a marker or title presence alone.

For a partially dispatched multi-date plan, verify the same unchanged full input. Reconciled dates become `fully_verified`; dates without a matching marker are reported as not dispatched and are not read. Rerun the same write command only after reconciliation: it skips verified dates and may send only dates that were never dispatched. An unresolved date still blocks.

## Non-negotiable data rules

- Read credentials only from the current process's `XUNJI_API_KEY`; send them only in request headers.
- Keep real profiles, records, caches, payloads, and manifests out of Git.
- Use only synthetic fixtures for examples and tests.
- Never print credentials, full API responses, or full training records in errors.
- Use Chinese standard movement names for Xunji writes. If a name is uncertain, verify it or ask the user; never guess an internal key.
- Use `0 kg` only when the service needs a technical placeholder for a reps-only set, and label it as a placeholder rather than a target load.

## Runtime capability boundary

- Read and write only user-selected local training files plus the private local cache.
- Read only `XUNJI_API_KEY` from the current process environment.
- Connect only to the fixed Xunji read and upsert endpoints after the relevant user request and write confirmation.
- Do not execute shell commands, query host credential stores, follow redirects, or contact another host.
- Finish or reconcile pending transactions before the user replaces their training data Key. Read caches and the fixed transaction root are partitioned by Key fingerprint, not a verified stable Xunji account identifier.
- Bind every movable version-3 read-cache entry to the full Key fingerprint, exact date, full/light mode, and fixed transaction epoch; reject unsafe linked or non-private paths, and bypass ordinary caches entirely for any date that has a transaction marker.
- Fail closed for write and verification on non-POSIX hosts until a non-redirectable per-user transaction root is implemented there.
