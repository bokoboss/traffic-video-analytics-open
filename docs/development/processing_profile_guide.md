# Processing profile guide

Use profiles as starting points for a bounded preview. Choose based on scene
conditions and hardware expectation, then validate the resolved configuration.

| Profile | Use when | Main trade-off |
| --- | --- | --- |
| `BALANCED` | mixed traffic and unknown conditions | practical default, not a quality claim |
| `HIGH_ACCURACY` | small/occluded objects need investigation | higher compute and memory |
| `FAST_PROCESSING` | quick triage on a bounded segment | stride can miss short objects |
| `SMALL_DISTANT_OBJECTS` | objects are small in a wide view | high input resolution cost |
| `DENSE_TRAFFIC` | short occlusions are common | longer buffers can increase fragmentation risk |
| `MOTORCYCLE_HEAVY` | motorcycle/bicycle evidence is important | raw labels remain provisional mappings |
| `PEDESTRIAN_COUNTING` | pedestrian-only counting lines | pedestrians remain a separate domain |
| `CUSTOM` | supported expert overrides are required | every override must pass validation |

Workflow:

1. Select a profile.
2. Set guided values for quality, speed, sensitivity, scene, occlusion, and
   object size.
3. Expand expert settings only when a supported parameter is needed.
4. Validate and inspect warnings/resource impact.
5. Save the immutable configuration revision.
6. Run a bounded preview and compare candidates when useful.
7. Promote only by creating a new full processing request; never treat preview
   events as production results.

Profile selection is not benchmark support and never creates a
`QUALIFIED_FOR_PILOT` result.

When reviewing a saved revision, use `configuration_hash` for normalized
pre-execution identity and `request_provenance_hash` for the operator request
trail. Use `runtime_configuration_hash` only on a completed worker result after
device and artifact resolution. Two results are runtime-equivalent only when
both runtime hashes are present and equal; the UI discloses that guided/profile
provenance may still differ. A missing or legacy runtime hash is unresolved,
not equivalent.
