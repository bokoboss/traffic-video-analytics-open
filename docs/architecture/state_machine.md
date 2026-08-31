# State Machine

Foundation project states:

```text
draft
source_ready
scene_configured
processing_complete
needs_review
review_complete
certified
exported
stale
incomplete
```

## Gates

- `review_complete` requires all mandatory QC flags to be resolved.
- `certified` requires current, complete, review-complete results.
- `exported` requires a certification record for the exact result version.
- `stale` blocks certification and export until reprocessing creates current results.

Green UI states are reserved for reviewed, certified or current states. Processing complete alone is not shown as certified.
