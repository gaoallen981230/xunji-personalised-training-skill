# Xunji API and writeback

## Contents

1. Scope and consent
2. Endpoints and authentication
3. Read policy
4. Record interpretation
5. Draft and manifest state machine
6. Write policy
7. Verification
8. Limits and errors

## Scope and consent

Use the Xunji API only when the user asks to read or change their Xunji records. General-purpose training design remains offline, and the user may define any direction. A write sends the selected plan to Xunji and must be disclosed to the user before confirmation.

This Skill is not affiliated with or endorsed by Xunji.

## Endpoints and authentication

- Base URL: `https://trains.xunjiapp.cn`
- Read: `POST /api_trains_for_llm_v2`
- Upsert: `POST /api_upsert_trains_for_llm_v2`
- Schema: `train_open_api_v2`

For first-time setup, tell the user to open the Xunji app and find the training data key (训练数据 Key). Do not invent an app navigation path when it has not been verified. Ask the user to store the key locally rather than paste it into a project or plan.

Read the credential only from the current process's `XUNJI_API_KEY`. The user must inject it through a non-echoing, user-controlled local mechanism. Use `Authorization: Bearer <credential>` by default. Never query host credential stores or place the credential in a URL, request body, manifest, cache, log, message, or Git file.

The key authenticates technical import and export requests. It does not grant standing or one-time write authority. Every export still requires the current reviewed digest and a later explicit confirmation.

Do not follow an authentication-bearing redirect to another host.

## Read policy

Light read:

```json
{
  "schema_version": "train_open_api_v2",
  "datestr": "YYYY-MM-DD",
  "include_full_data": false
}
```

Use full data for weekly review, unchecked sets, RPE, notes, completion feeling, left/right loads, actual training seconds, per-set rest, any update, and verification.

Use version-3 read-cache envelopes. Each one binds the full fingerprint of the current training data Key, exact date, full/light mode, and fixed transaction epoch. Require owner-controlled private regular cache roots, account directories, and files; symbolic links, unexpected owners, and group/other access fail closed. Force-refresh a corrected date. A light cache must never satisfy a full-data request. Ordinary read caches may use `--cache-dir` or `XDG_CACHE_HOME`; transaction locks and markers always use the single fixed per-user POSIX root and ignore both settings. Reads share the fixed date lock with writes and verification. A date with any transaction marker bypasses ordinary caches entirely. Write and verification fail closed on non-POSIX hosts. The Key fingerprint is a local namespace, not a verified stable account identifier; finish or reconcile pending transactions before replacing the Key.

Successful responses normally contain `success: true`; training sessions may be returned as `res.trains` or a list under `res`.

## Record interpretation

- `done=true` is direct completion evidence.
- Positive `trainedSeconds` is an observable completion-specific signal.
- Planned `time`, `duration_s`, `metrics`, reps, weight, or distance alone is not completion evidence.
- Record-style movements may store distance, calories, workout time, or heart rate under `sets[].metrics`.
- Superset and drop-set children may appear under `sets[].items[].set`.
- Set RPE belongs in `movements[].sets[].rpe`; comment-parsed RPE remains a separate qualitative field.
- New or changed movement difficulty values are `easy`, `normal`, and `hard`. An imported legacy value may only be preserved unchanged.
- A history-card colour belongs in `note.trainColor`, not a top-level `color` field.

Preserve unknown or unrelated metadata. If a note is hidden or unparseable, stop rather than overwrite it.

## Draft and manifest state machine

Use these states:

```text
profiled -> evidence_read -> drafted -> user_reviewed
-> explicitly_confirmed -> write_intent -> verify_pending -> fully_verified
                                  \-> ambiguous -> full-data reconciliation
fully_verified -> reverify_pending -> fully_verified
                               \-> drifted -> fresh full-data reconciliation
```

`prepare-write` creates a manifest with a canonical SHA-256 digest and a privacy-reduced summary. It does not call the network. The digest binds the complete profile, the optional baseline, all outgoing records, and every update's exact imported original snapshot. The confirmation applies only to that digest and date range.

Any payload change invalidates confirmation. An early confirmation, a standing rule, or a vague approval does not authorise the current write.

Keep manifests outside the repository and treat them as private training metadata.

## CLI contract

Use the same unchanged plan and profile files in all three stages. Each update's exact `original_xunji_payload` is a mandatory internal baseline. Every actual dose change must match its declaration exactly and must have comparable evidence in that snapshot. Comparison covers set count, load, repetitions, duration, distance, calories, intensity or heart rate, difficulty, rest or density, tempo, range of motion, and typed nested metric paths. An unrecognised metrics leaf becomes a separate JSON-Pointer-style `metric:/normalised/path` variable; escaped tokens distinguish literal separators from actual nesting. Comparison preserves target positions, recursively includes nested `items[].set` targets at every depth, and retains every alias and load unit. An added set counts only as set progression when its complete target signature matches one comparable original set; signatures cannot be assembled across different sets, and novel targets count separately. Adding a duplicate occurrence of an existing movement name remains `exercise_selection`; dose comparison is limited to occurrences already in the original. Unknown or unrelated metadata additions, removals, and changes fail closed. An external `--baseline` is required when the plan's complete declared progression map is not exactly and unambiguously covered by bound update snapshots. A declared movement name in any create operation always requires the external baseline rather than borrowing a same-name update snapshot. Once supplied, it must comparably cover every declared progression; empty, unrelated, and unchanged external baselines fail this check. The same unchanged external baseline file is required during write and verification:

```bash
python3 scripts/xunji_client.py prepare-write \
  --plan /local/path/weekly-plan.json \
  --manifest /local/private/path/write-manifest.json \
  --profile /local/path/training-profile.json \
  --baseline /local/path/comparable-baseline.json

python3 scripts/xunji_client.py write \
  --plan /local/path/weekly-plan.json \
  --manifest /local/private/path/write-manifest.json \
  --profile /local/path/training-profile.json \
  --baseline /local/path/comparable-baseline.json \
  --expected-digest DIGEST_FROM_PREPARE \
  --write-confirmed

python3 scripts/xunji_client.py verify \
  --plan /local/path/weekly-plan.json \
  --manifest /local/private/path/write-manifest.json \
  --profile /local/path/training-profile.json \
  --baseline /local/path/comparable-baseline.json
```

Omit `--baseline` from all three commands only when there is no declared progression outside the bound update snapshots. Changing any bound input invalidates the manifest.

## Write policy

Before every write:

1. Read the original date with full data.
2. Match updates by `localid`.
3. Store the exact imported session in `original_xunji_payload`; clone it into `xunji_payload` before making the smallest reviewed change.
4. Reject a create or update title collision using the same normalised-title rule as verification; an update must also retain its unique `localid`.
5. Reject an update missing `localid`, `start`, or `end`.
6. Reject an update to a session with completion, timing, RPE, comment, or start evidence.
7. Reject hidden note placeholders; unknown or unrelated metadata additions, removals, or changes; unclassified fields on any newly created session, movement, set, or nested item; and removal of original movements, sets, or nested items. Reject completion evidence recursively at every nested set depth. Destructive restructuring belongs in the app followed by a force-refresh.
8. Show date, title, create/update status, movement count, set count, and payload digest.
9. Wait for a new user message that explicitly confirms this version.
10. Immediately before each date's write, require the current full record to equal the reviewed original snapshot.
11. Pass `--write-confirmed` and the expected digest only after that confirmation.
12. Acquire the exclusive training-data-Key-namespace/date lock from the fixed per-user transaction root and keep it through the transaction step. Never derive this root from `--cache-dir` or `XDG_CACHE_HOME`. Synchronise new transaction directories and lock entries to their parent directories.
13. Durably store a credential-namespace `write_intent` before dispatch. Advance an acknowledged request to `verify_pending`; record an uncertain outcome as `ambiguous` when possible. Synchronise every marker replacement to its parent directory, and refuse dispatch when intent durability cannot be established.
14. Refuse another write while `write_intent`, `ambiguous`, `verify_pending`, `reverify_pending`, or `drifted` remains unresolved.
15. Treat an existing or stale local lock as a stop condition; inspect it and the transaction marker rather than deleting it blindly.

New plans normally omit `start` and `end`. Existing plans preserve both. Never delete an old session merely because it is absent from an outgoing list.

Only Chinese movement `name` values are accepted for normal writes. Verify uncertain names against the public movement-name source or ask the user. Do not guess internal keys.

Each set needs at least one target field: `weight`, `weight_kg`, `reps`, `time`, `duration_s`, or `selfWeight`. Keep unchecked planned sets as `done: false`. Use `0 kg` only as a labelled technical placeholder when required to preserve a reps-only set.

### Residual write races

No compare-and-swap, revision, ETag, idempotency, server-side title-uniqueness, or equivalent conditional-write behaviour has been verified for the Xunji upsert endpoint. The immediate full-data preflight detects visible changes but cannot prevent another authorised client from changing an existing record between that check and the upsert. Two create-only clients can also both pass the duplicate-title preflight and then create duplicates. Confirmation, digest binding, deterministic request identifiers, and retry controls do not make either path atomic.

The repository owner has accepted publication of guarded writeback with this residual risk. That release decision does not weaken the runtime boundary: do not infer an API guarantee, describe the operation as atomic or exactly once, widen retry behaviour, or treat a create-only switch as protection against concurrent duplicates.

## Verification

Treat a successful upsert response as `verify_pending`, not complete. A transport or response failure after dispatch is `ambiguous` when that state can be persisted; if it cannot be persisted, the earlier `write_intent` remains authoritative. Verification must first find the matching credential-namespace transaction marker and hold the date lock through marker transition. After the rate-limit window, read with `include_full_data=true` and compare:

- date and title;
- `localid`, `start`, and `end` for updates;
- movement order and count;
- set count;
- target fields and units;
- notes and preserved metadata when the API exposes them;
- absence of an unintended planned duration.

Title-only verification is insufficient. Both create and update candidates must be the unique normalised-title match for the date, and an update must also be the unique reviewed `localid` match to that same record. A match reconciles `write_intent`, `ambiguous`, or `verify_pending` to `fully_verified`. A mismatch in those first-time states preserves the original state so its dispatch evidence is not lost. Never infer that a timeout means failure, and never blindly resend.

Run the full-data read on every verification invocation, including when the marker is already `fully_verified`. Before that historical read, durably transition the marker to blocking `reverify_pending`. Timeout or interruption leaves it blocking; a mismatch becomes `drifted`; an exact match restores that digest to `fully_verified`. Reverification of `drifted` follows the same blocking path. The same drifted digest cannot be rewritten. A different digest may proceed only after a new bound manifest, explicit confirmation, and live full-data preflight. Historical drift must never be hidden by the old success marker.

For a multi-date plan that stops after partial dispatch, run verification with the same unchanged complete plan, profile, baseline, and manifest. Dates with matching unresolved markers are reconciled; dates without markers are reported as not dispatched and are not read. A later invocation of the same write command skips `fully_verified` dates and may dispatch only dates that were never sent. Any unresolved mismatch remains blocking.

## Limits and errors

- At most 4 sessions per write request, all for the same date.
- At most 15 movements per session.
- At most 20 sets per movement.
- Typical same-date intervals are 15 seconds for light reads, 30 seconds for full reads, and 45 seconds for writes.

If the API says `too frequent`, follow the retry hint only after checking the transaction marker. Reconcile `write_intent`, `ambiguous`, `verify_pending`, `reverify_pending`, and `drifted` with a full-data read. Resume only dates known not to have been dispatched; do not resend an unresolved or successful date.

For `apikey missing` or `apikey invalid`, ask the user to renew or copy the credential in Xunji. `仅VIP可用` means the account requires the relevant Xunji access level.

Errors must report action, date, status, and a safe category. Do not echo complete service responses or submitted records.
