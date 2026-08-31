# Domain Model Seed

This is a starting contract, not the final schema.

## Core entities

### Project

Container for one study and its sources, configurations, runs and exports.

### VideoSource

References the local source, media metadata and source fingerprint.

### SourceFingerprint

Identifies the exact source bytes and relevant media characteristics.

### SceneProfile / SceneVersion

Stores reference frame, geometry and compatibility information.

### CountingRule / CountingRuleVersion

Defines line crossing, entry/exit movement or pedestrian crossing semantics.

### AnalysisRun

Immutable declaration of inputs used for a processing run.

### ProcessingSegment

Checkpointable portion of a run with warm-up and committed event range.

### ModelBundle

Records detector, classifier, tracker, thresholds, runtime and application versions.

### TrackSummary

Track-level observations and classification summary. Not itself a counted event.

### AutoCountEvent

Immutable machine-generated count event.

### QCFlag

Machine or system concern associated with an event, track, segment or run.

### ReviewAction

Append-only human action over an auto event or manual event.

### AggregateSnapshot

Versioned aggregate derived from effective events.

### CertificationRecord

Records scope, reviewer, time and exact result version certified.

### ExportManifest

Records exported artifacts and the exact certified/current result versions used.

## Identity considerations

Do not expose internal database IDs as stable user-facing identities.

Use UUIDs or similarly stable public identifiers where required.

## Effective event concept

```text
AutoCountEvent
      +
ordered ReviewAction records
      =
EffectiveCountEvent projection
```

A manually added count should have a distinct origin and audit trail, not masquerade as an AI event.
