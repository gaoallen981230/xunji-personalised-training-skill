# Privacy

## Local data

Profiles, weekly check-ins, plans, API responses, caches, write manifests, and verification results may contain sensitive health or training information. Store them in a private local project and exclude them from Git, cloud-synchronised folders, bug reports, screenshots, and examples.

The repository contains synthetic examples only and describes a general-purpose training product. Each user may define their own direction, but no individual programme belongs in the release package.

## Network data

Offline profile validation, plan validation, and `prepare-write` do not need network access.

When the user explicitly requests a Xunji read or write, the client sends only the required request to `https://trains.xunjiapp.cn`. A read sends the requested date and options. A write sends the reviewed Xunji payload. No training data should be sent to any other host.

The project includes no analytics, telemetry, cloud backup, or third-party error reporting.

## Credentials

For first use, the user finds the training data key (训练数据 Key) in the Xunji app and injects it locally into the current process as `XUNJI_API_KEY` through a non-echoing, user-controlled mechanism. The key authenticates API requests; it is not write authorisation. The client does not query host credential stores or store the value in a profile, plan, cache, manifest, log, source file, URL, or request body.

## Cache controls

The client uses a user-local cache boundary. Before a cache hit, it requires owner-controlled private regular directories and files and rejects symbolic links, unexpected owners, and group/other access. Cache namespaces are derived from a fingerprint of the current training data Key; the Key itself is not stored. Each version-3 entry also binds the full fingerprint, exact date, full/light read mode, and current fixed transaction epoch. Ordinary read caches may be relocated, but transaction locks and markers use one fixed per-user root so alternate cache settings cannot bypass them. Any date with a transaction marker bypasses ordinary caches entirely. Transaction markers contain status, request metadata, and digests rather than complete records. A manifest binds a digest of the complete profile, any external baseline, and each update's mandatory original snapshot, but does not copy those documents into the manifest. Users should force-refresh dates after manual corrections and reconcile every pending transaction before replacing a Key. Delete the cache only when no transaction is unresolved and it is no longer needed, or when a shared device changes owner.

## Public contributions

Use fabricated identities, future synthetic dates, placeholder loads, invented notes, and explicit account placeholders in example paths. The release gate scans concrete macOS, Linux, and Windows home paths; ordinary account names are not treated as placeholders. Never contribute exported Xunji data, a real `localid`, health information, personal schedules, or a credential-derived artefact.
