-- Pilot runtime contract: the operator may select one completed processing run
-- for review/certification/export. Historical runs remain immutable and visible.

ALTER TABLE projects ADD COLUMN current_analysis_run_id TEXT;

-- Existing 6E projects get a deterministic initial current result during
-- upgrade. Future runs are not allowed to replace this pointer implicitly;
-- the operator can select another completed run through the API/UI.
UPDATE projects
SET current_analysis_run_id = (
  SELECT id
  FROM analysis_runs
  WHERE analysis_runs.project_id = projects.id
    AND result_ready = 1
  ORDER BY created_at DESC, id DESC
  LIMIT 1
)
WHERE current_analysis_run_id IS NULL;

CREATE INDEX idx_projects_current_analysis_run
  ON projects(current_analysis_run_id);
