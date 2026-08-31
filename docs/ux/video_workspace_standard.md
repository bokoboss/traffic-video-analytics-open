# Video Workspace Standard

## Purpose

The video workspace is a professional scene-configuration and evidence instrument.

It must remain usable with:

- bright daylight;
- dark night scenes;
- low contrast;
- rain;
- dense overlays;
- long Thai movement labels.

## Structure

```text
Tool strip
Video canvas
Overlay legend
Context inspector
Timeline and playback controls
```

## Canvas

- dark ground outside the video
- video centred and fitted without distortion
- zoom and pan available
- actual aspect ratio preserved
- letterbox area visually distinct
- selected geometry remains above detections
- labels avoid obscuring critical vehicle evidence

## Tools

Core tools:

- Select
- Pan
- ROI
- Exclusion
- Count line
- Entry zone
- Exit zone
- Pedestrian crossing
- Delete
- Undo/redo
- Fit
- Zoom

Only one drawing tool is active at a time.

Cursor and toolbar state must make the current mode obvious.

## Geometry

### Anchor points

- 8–10 px visual size
- larger hit area
- high-contrast outline
- selected and hover states
- keyboard deletion
- arrow-key nudge where practical

### Count lines

- no arrowheads; the line itself is visually undirected
- Side A and Side B labels placed on opposite physical sides
- line name shown near midpoint
- selected glow is subtle and temporary
- derived `A_TO_B` and `B_TO_A` directions are shown in inspector/review/export,
  not by reversing an arrow on the line

### Zones

- translucent fill below 18% opacity
- clear outline
- label outside the densest traffic area where possible
- entry/exit encoded by both colour and label

### Exclusion

Use hatch or repeated diagonal pattern, not colour alone.

## Overlay hierarchy

From back to front:

1. video
2. ROI/exclusion fill
3. track trails
4. detections
5. geometry outlines
6. labels
7. selected handles
8. contextual tooltip

## Detection display

Defaults for normal users:

- box
- compact class label
- track ID hidden unless review/debug mode
- confidence hidden unless advanced or needs-review
- trail hidden unless tracking issue is inspected

Avoid turning the canvas into a model-debug view.

## Timeline

Shows:

- full source duration
- analysis window
- warm-up
- processed segments
- selected event
- review/QC markers
- current playhead

Intervals are visually aligned with the selected interval origin.

## Playback controls

- play/pause
- ±5 seconds
- previous/next frame
- previous/next event
- speed
- source time
- elapsed time
- zoom

Keyboard shortcuts are mandatory.

## Trial mode

Trial results appear without leaving the workspace.

Show:

- analysed duration;
- approximate event count;
- uncertain events;
- scene warnings;
- overlay toggle.

The primary decision is either:

- Adjust scene
- Start full processing

## Camera compatibility

When applying a saved scene to a new source, present:

- reference frame comparison;
- dimensions/aspect compatibility;
- possible camera movement;
- action to confirm, realign or create a new scene version.

Never silently reuse geometry after a suspected camera move.
