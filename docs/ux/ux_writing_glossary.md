# UX Writing and Localization Glossary

## Voice

Use calm, direct, technically precise Thai. The interface should name the state, the consequence and the next available action. Preserve uncertainty with explicit `unknown`, `ambiguous` and `needs_review` states instead of forcing a classification.

Avoid hype, decorative AI language, blame, raw exception text and vague error messages.

## Thai Terminology

| English concept | Thai UI term |
|---|---|
| Project | โครงการ |
| Source video | แหล่งวิดีโอ |
| Media metadata | เมตาดาทาวิดีโอ |
| Analysis window | ช่วงวิเคราะห์ |
| Time zone | เขตเวลา |
| Reference frame | reference frame |
| Scene | ฉาก |
| Region of interest | ROI / พื้นที่วิเคราะห์ |
| Counting line | เส้นนับ |
| Direction | ทิศทาง |
| Approach | ทางเข้า |
| Movement | movement |
| Run | รัน |
| Stale | ล้าสมัย |
| Review | การตรวจสอบ |
| Reversal | การย้อนกลับ |
| Certification | การรับรองผล |
| Export | การส่งออก |
| Validation | การตรวจสอบความถูกต้อง |
| Warning | คำเตือน |
| Retry | ลองอีกครั้ง |
| Event ledger | บัญชีเหตุการณ์ |
| Raw totals | ยอดดิบ |
| Reviewed totals | ยอดหลังตรวจสอบ |
| Excluded totals | ยอดที่ไม่นับ |
| Synthetic track | แทร็กสังเคราะห์ |
| Validation-only output | ผลสำหรับตรวจสอบเท่านั้น |

## Approved Untranslated Terms

The following English technical terms may remain in Thai UI where they are clearer than a forced translation:

- `Traffic Video Analytics`
- `TVA`
- `PTS`
- `PHF`
- `FFmpeg`
- `ffprobe`
- `ROI`
- `CSV`
- `SQLite`
- `API`
- `reference frame`
- `movement`
- `manifest`
- `QC`
- `A to B`
- `B to A`
- `Bidirectional`

## Button Labels

Use concrete verbs and outcomes:

- `สร้างโครงการ`
- `เลือกวิดีโอในเครื่อง`
- `ยืนยันช่วงเวลา`
- `บันทึกฉาก`
- `รันการนับสังเคราะห์`
- `ตรวจสอบแทร็กสังเคราะห์`
- `รับรองผลเวอร์ชันนี้`
- `สร้าง manifest CSV`

Avoid generic labels such as `ดำเนินการ`, `ตกลง` or `ยืนยัน` when the consequence is unclear.

## State Copy

### Stale

**ผลล้าสมัย**

ข้อมูลที่มีผลต่อการคำนวณเปลี่ยนไป จึงต้องประมวลผลใหม่ก่อนรับรองผลหรือส่งออกเป็นผลปัจจุบัน

Action: `รันการนับสังเคราะห์`

### Needs Review

**มีรายการที่ต้องตรวจสอบ**

รายการเหล่านี้ต้องได้รับการตัดสินใจจากผู้ตรวจสอบก่อนรับรองผล

Action: `ตรวจสอบเหตุการณ์`

### Unsupported Source

**ยังไม่รองรับวิดีโอนี้**

ระบบอ่านเมตาดาทาวิดีโอไม่ได้หรือยังไม่มีเครื่องมือสื่อที่จำเป็น ให้เลือกไฟล์อื่นหรือติดตั้ง FFmpeg แล้วลองอีกครั้ง

### Save Failure

**บันทึกการเปลี่ยนแปลงไม่ได้**

ข้อมูลเดิมยังคงอยู่ ตรวจสอบพื้นที่จัดเก็บหรือโหลดสถานะล่าสุดแล้วลองอีกครั้ง

## Numeric Copy

- Keep counts as numerals.
- Put units in table headers where practical.
- Do not display false precision.
- Show unavailable values as `-` with an explanation, not `0`.
