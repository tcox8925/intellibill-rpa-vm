-- Migration: Extract JSON fields into dedicated columns on membercare_agent_assessment_recordings
-- Run on PostgreSQL (myopsprod). All columns are nullable to support existing rows.

ALTER TABLE wpo.membercare_agent_assessment_recordings
ADD COLUMN IF NOT EXISTS max_total_score INTEGER;

ALTER TABLE wpo.membercare_agent_assessment_recordings
ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION;

ALTER TABLE wpo.membercare_agent_assessment_recordings
ADD COLUMN IF NOT EXISTS agent_name VARCHAR(200);

ALTER TABLE wpo.membercare_agent_assessment_recordings
ADD COLUMN IF NOT EXISTS caller_name VARCHAR(200);

ALTER TABLE wpo.membercare_agent_assessment_recordings
ADD COLUMN IF NOT EXISTS sale_or_not_sale VARCHAR(20);

ALTER TABLE wpo.membercare_agent_assessment_recordings
ADD COLUMN IF NOT EXISTS edited_sale_or_not_sale VARCHAR(20);

ALTER TABLE wpo.membercare_agent_assessment_recordings
ADD COLUMN IF NOT EXISTS edited_sale_reason TEXT;
