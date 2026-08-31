ALTER TABLE analysis_runs ADD COLUMN weight_identifier TEXT;
ALTER TABLE analysis_runs ADD COLUMN weight_sha256 TEXT;

ALTER TABLE real_inference_runs ADD COLUMN model_revision TEXT;
ALTER TABLE real_inference_runs ADD COLUMN tracker_revision TEXT;
ALTER TABLE real_inference_runs ADD COLUMN weight_identifier TEXT;
ALTER TABLE real_inference_runs ADD COLUMN weight_sha256 TEXT;
