# Screen Inventory and Specifications

## S01 — Projects

### Goal

Find or create a study and understand its current operational state.

### Macrostructure

Editorial study ledger with a wide title column and a narrow status/action lane.

### Primary action

`สร้างโครงการ`

### Required content

- project title
- site/study type
- latest source
- current step
- review/certification state
- last modified
- current/stale indicator

### States

- first use
- loading
- no search result
- stale project
- interrupted project
- archived project

### Avoid

- decorative hero
- equal-size project cards with large icons
- generic KPI strip

## S02 — Create Project

### Goal

Create the minimum project identity.

### Fields

- project name
- site/location
- study type
- language
- optional notes

Use a compact sheet or focused page, not a multi-step wizard before a project exists.

## S03 — Source & Time

### Goal

Confirm source identity, real time and exact analysis range.

### Macrostructure

Source preview and metadata left; time worksheet right; interval preview below.

### Primary action

`ยืนยันแหล่งวิดีโอและเวลา`

### Required visual consequence

Show the resulting four 15-minute intervals immediately.

### States

- no source
- reading metadata
- unsupported
- ambiguous metadata
- exact one-hour valid
- incomplete hour
- source changed/stale

## S04 — Scene Setup

### Goal

Define countable space and movements.

### Macrostructure

Dominant video canvas, compact tool strip, contextual inspector and fixed timeline.

### Primary action

`บันทึกฉาก` or `ทดลองนับ`

### States

- no reference frame
- drawing
- invalid geometry
- unsaved changes
- camera compatibility warning
- read-only historical version

## S05 — Trial

### Goal

Test a short range before expensive processing.

### Macrostructure

Remain within video workspace; show concise trial summary as a side/bottom panel.

### Required result

- trial range
- preliminary events
- uncertain events
- scene warnings
- processing factor estimate when meaningful

### Primary action

`เริ่มประมวลผลเต็ม`

## S06 — Processing

### Goal

Understand progress and intervene safely.

### Macrostructure

One progress narrative with optional live evidence and collapsed diagnostics.

### Required content

- analysis range
- current segment
- percent
- committed results
- safe pause/stop
- storage estimate
- warning/error

### Avoid

- dense GPU dashboard
- indefinite spinner when progress is knowable

## S07 — Review

### Goal

Resolve required QC and certify effective events.

### Macrostructure

Queue, evidence and details/actions.

### Primary action

Context-dependent review action; global action is `ตรวจรายการถัดไป`.

### Required states

- unresolved
- approved
- corrected
- excluded
- manual
- suspected duplicate
- no unresolved items
- evidence unavailable

## S08 — Results

### Goal

Read and interrogate exact analytical outputs.

### Macrostructure

Structured result header, table/matrix, interval chart and drill-through.

### Required content

- scope
- current/stale
- class × direction
- entry × exit
- 15-minute values
- hourly total
- PHF
- pedestrian section
- review impact

### Primary action

`ตรวจสอบหลักฐาน` or `ไปยังการรับรองผล`

## S09 — Certification

### Goal

Sign off an exact result version.

### Required checks

- current result
- complete processing
- required QC resolved
- review policy met
- source/config/model versions recorded

### Primary action

`รับรองผลเวอร์ชันนี้`

Certification uses a real decision dialog or dedicated sign-off section.

## S10 — Export

### Goal

Create traceable deliverables.

### Required content

- format
- included sheets/files
- review/certification label
- result version
- provenance
- destination
- existing exports

### Primary action

`ส่งออก Excel` or selected format

## S11 — Settings

### Goal

Manage operational defaults without exposing unnecessary model detail.

Sections:

- language
- storage
- evidence retention
- playback
- accessibility
- advanced diagnostics

## S12 — Diagnostics

### Goal

Explain system readiness and failures.

Show:

- app version
- database/runtime
- media tools
- future CUDA/GPU
- disk
- logs reference
- copy diagnostic summary

Diagnostics must not look like the primary application.
