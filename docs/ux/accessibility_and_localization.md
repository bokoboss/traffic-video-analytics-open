# Accessibility and Localization

## Language

Thai is the default.

English is supported through translation keys from the first implementation milestone.

Do not concatenate sentence fragments in code because Thai and English word order may differ.

## Thai typography

- Allow longer line boxes than English.
- Avoid all-caps transformations.
- Do not apply tight negative tracking to Thai body text.
- Test mixed Thai, English, numerals and abbreviations.
- Preserve non-breaking behaviour for timestamps and units where appropriate.

## Terminology

Use domain terms consistently.

Examples:

- โครงการ
- แหล่งวิดีโอ
- เวลาเริ่มต้นจริง
- ช่วงวิเคราะห์
- เส้นนับ
- ทางเข้า
- ทางออก
- ทิศทางการเคลื่อนที่
- รายการที่ต้องตรวจสอบ
- ผลปัจจุบัน
- ผลล้าสมัย
- รับรองผล
- ส่งออก

Terms must be centralized in the localization catalogue.

## Contrast

Minimum:

- normal text: 4.5:1
- large text: 3:1
- essential non-text controls/graphics: 3:1

Video overlays must be tested over bright and dark frames.

Use outline, shadow, label plate or pattern where necessary.

## Keyboard

All critical workflows must be keyboard-accessible:

- navigate rail;
- control playback;
- select geometry;
- move between review events;
- approve/correct/exclude;
- open command palette;
- dismiss popovers/dialogs safely.

## Focus

- visible focus ring;
- logical order;
- focus moved to meaningful target after route/dialog changes;
- focus returned after closing overlay;
- no keyboard trap.

## Motion

Respect `prefers-reduced-motion`.

Do not auto-play long animated explanations.

Video playback remains user-controlled and is not treated as decorative animation.

## Icons

- consistent icon library;
- visible label for unfamiliar actions;
- accessible name for icon-only controls;
- do not use sparkle/AI icons as generic decoration.

## Tables

- semantic headers;
- scope for row/column headers;
- announced sorting;
- accessible selected state;
- keyboard movement;
- text alternative for dense visual matrices where needed.

## Error handling

- associate error with field;
- summarize multiple errors;
- do not rely on red;
- preserve entered values;
- provide corrective instruction.

## Zoom

The application must remain usable at 125% and 150% desktop scaling.

Internal panels may scroll, but controls must not overlap or disappear.
