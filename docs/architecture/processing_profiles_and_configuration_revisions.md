# Processing profiles and immutable configuration revisions

6D separates an operator-friendly choice from the executable configuration.
The profile is a versioned starting point; the configuration revision is the
exact immutable contract used by a preview or full processing request.

## Profile revision

`processing_profile_revisions` stores:

- profile code and revision;
- English/Thai display metadata and intended use;
- resolved starting parameters and supported object domains;
- hardware expectation and known trade-offs;
- content hash, creator, creation time, status, and superseded revision.

The built-in catalogue is seeded by the backend from
`apps/backend/app/processing_profiles.py`. A profile may describe a useful
starting point, but selecting it is not benchmark qualification.

## Guided resolution

The guided contract has six controlled fields: analysis quality, processing
preference, detection sensitivity, scene type, occlusion, and object size. The
backend maps these fields deterministically to supported detector/tracker,
crossing, and classification parameters. The UI never owns the resolution
rules.

## Expert resolution

`ExpertProcessingOverrides` is a closed Pydantic model. Supported fields cover
image size, confidence/IoU, frame stride, raw class allowlist, ByteTrack
thresholds/buffer, minimum track evidence, crossing anchor/tolerances/stability
and duplicate cooldown, and classification evidence thresholds. Unknown keys,
unsupported raw classes, invalid ranges, and incompatible windows fail before a
revision is written.

When a value is normalized, the revision retains both the requested override
and resolved parameters, plus a normalization record. The content hash covers
the project context, profile revision, guided values, requested overrides,
resolved values, policy revisions, and validation result.

## Revision invariants

`processing_configuration_revisions` is append-only. It records the parameter
schema revision, model/tracker policy references, crossing policy revision,
classification policy revision, device request, creator/time, validation result,
and content hash. A full job stores the revision ID alongside the legacy
configuration hash; retries reuse the same immutable revision.

The revision does not contain a private media path or model weight bytes. Weight
identity and SHA-256 are represented at two points: the configuration records
the registry's expected/static adapter provenance (or a typed unresolved value),
and the worker records the actual model/weight/tracker provenance after runtime
resolution. The worker compares the configured and actual canonical runtime
hashes and persists `VERIFIED_RUNTIME_PROVENANCE`,
`RUNTIME_PROVENANCE_MISMATCH`, or an unresolved status; it never silently
promotes a mismatch to verified.

`configuration_hash`, `request_provenance_hash`, and
`runtime_configuration_hash` are intentionally separate. `configuration_hash`
identifies normalized requested worker inputs before execution.
`request_provenance_hash` retains profile/guided/operator request context and
normalization history. `runtime_configuration_hash` is absent before execution
and is created by the worker only after device resolution and runtime artifact
inspection. Profile metadata, project identity, creation time, UI labels, and
warnings do not affect runtime identity.
