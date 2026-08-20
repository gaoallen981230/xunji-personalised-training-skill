# Security policy

## Supported version

The current release candidate is version `0.1.0`. Public release requires the release gate, a disclosed static security scan, complete file review, and repository-owner approval.

## Reporting a vulnerability

After publication, use GitHub private vulnerability reporting or another private channel selected by the repository owner. Do not open a public issue containing a credential, training record, private manifest, local path, or reproduction payload derived from a real account.

Reproduce issues with synthetic data. If a credential may have been exposed, revoke or replace it in Xunji before further testing.

## Trust boundaries

- Offline planning and validation must not call a network service.
- Xunji operations may connect only to `https://trains.xunjiapp.cn`.
- Credentials may appear only in the authentication request header.
- Authentication-bearing redirects to another host are forbidden.
- Errors must not echo full responses, payloads, credentials, or personal records.
- Movable read-cache roots, account directories, and entries must be owner-controlled private regular paths; symbolic links, unexpected owners, and group/other permissions fail closed before a cache hit.
- Remote writes require a reviewed digest and a later explicit confirmation; possession of `XUNJI_API_KEY` is authentication, not write authorisation.
- The digest binds the complete profile, the optional baseline, the outgoing records, and each update's imported full-data original snapshot.
- Each update snapshot is also its mandatory internal progression baseline. Calculated dose changes must exactly match the declarations, while any declaration not covered by update snapshots requires one comparable external baseline bound through all three stages.
- A declared movement name present in any create operation requires an external baseline; a same-name update snapshot cannot satisfy the create's ambiguous declaration scope.
- Unknown or unrelated metadata additions, removals, and changes on an existing update fail closed.
- Newly created API objects use known-field allowlists, planned sets require `done: false` when the field is present, and completion evidence is inspected recursively through every nested child set.
- An added set counts only as set progression when its complete target signature matches one comparable original set; signatures cannot be assembled across different sets, and novel targets remain separate dose variables.
- Adding another occurrence of an existing movement name is classified as `exercise_selection`; only pre-existing occurrences contribute dose-change comparisons.
- An unchanged imported legacy movement difficulty may be preserved, but every new or changed value must be exactly `easy`, `normal`, or `hard`.
- A remote change after review blocks the update and requires a new import, manifest, and confirmation.
- Create and update verification require one unique normalised-title match for the date; an update also requires its unique reviewed `localid` to identify that same record.
- Before dispatch, the client durably records a credential-namespace `write_intent`. Acknowledged writes become `verify_pending`; uncertain outcomes become `ambiguous` when possible. All unresolved states require full-data reconciliation and block blind resend.
- A write is incomplete until its credential-namespace transaction marker and a full-data read-back both pass.
- An exclusive local lock serialises cooperating readers, writers, and verifiers for each training-data-Key namespace and date. Locks and markers use one fixed per-user root that ignores `--cache-dir` and `XDG_CACHE_HOME`. New transaction directories, lock entries, and every marker replacement are synchronised to their parent directory; a sync failure fails closed before upsert dispatch. A stale lock fails closed and must be inspected, not blindly removed.
- Every version-3 movable read-cache entry is bound to the full Key fingerprint, exact date, full/light mode, and fixed transaction epoch; any date with a transaction marker bypasses ordinary caches entirely.
- A multi-date retry skips dates already `fully_verified` and may dispatch only dates with no matching transaction marker; unresolved dates remain blocking.
- Every verification invocation performs a fresh full-data read. A historical `fully_verified` marker first becomes blocking `reverify_pending`; interruption keeps it blocking, mismatch becomes `drifted`, and only an exact read-back restores that digest to `fully_verified`. A mismatch during first-time reconciliation retains the original `write_intent`, `ambiguous`, or `verify_pending` evidence. The drifted digest stays blocked; a different digest requires a newly bound manifest, explicit confirmation, and live full-data preflight.

## Runtime capability boundary

The installable Skill reads and writes only user-selected local training files and its private local cache. It reads one environment variable, `XUNJI_API_KEY`. When the user explicitly requests Xunji access, it may send authenticated HTTPS requests only to the fixed read and upsert endpoints on `trains.xunjiapp.cn`. It does not execute shell commands, query host credential stores, follow redirects, or contact other hosts.

Read caches and fixed-root transaction controls are partitioned by a fingerprint of the current Key because the reviewed contract provides no stable account identifier. The full fingerprint is also bound inside every version-3 read-cache envelope together with its date, read mode, and transaction epoch. Replacing a Key creates a different local namespace. Pending transactions must be reconciled before rotation; a new Key must not be treated as proof that no old transaction exists. Guarded write and verification transactions currently fail closed on non-POSIX hosts because a stable per-user root has not been established there.

## Residual write races

No compare-and-swap, revision, ETag, idempotency, server-side title-uniqueness, or equivalent conditional-write contract has been verified for the Xunji upsert endpoint. The client performs a fresh full-data read immediately before an update and refuses visible drift or completion evidence, but another authorised client can still change the record between that check and the upsert. Two concurrent create-only clients can also both observe no matching title and then create duplicate sessions. The current client therefore cannot guarantee atomic updates or exactly-once creation.

The repository owner explicitly accepted publication of guarded writeback with this known residual concurrency risk on 2026-08-17. This is a release-scope decision, not a technical mitigation: a create-only switch does not remove the concurrent duplicate race, manual confirmation does not make the preflight atomic, and the client must not claim conditionally protected or exactly-once writes.

## Release gate

Publishing is blocked unless all of the following pass:

1. Skill structure and metadata validation.
2. Python syntax and offline unit tests.
3. Secret, real-identifier, unexpected-file, and concrete macOS/Linux/Windows home-path scans; only explicit public account placeholders are exempt.
4. A no-LLM static Skill security scan with complete execution, its exact findings and risk score disclosed, and no unreviewed actionable security finding.
5. Review of the complete file manifest and synthetic fixtures.
6. A recorded capability-scope decision for the residual write races: planning/read-only, verified conditional and idempotent server writes, or explicit owner acceptance of guarded non-atomic writeback.
7. Manual approval by the repository owner.

Scanner failure or absence is not a pass. Do not upload from the personal training project directory; publish only this clean, whitelist-built package.
