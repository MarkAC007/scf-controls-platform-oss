-- Migration: 002_enhance_tasks_system.sql
-- Description: Enhance evidence collection tasks with lifecycle management
-- Date: 2025-12-08

-- Add new columns to evidence_collection_tasks
ALTER TABLE evidence_collection_tasks
    ADD COLUMN IF NOT EXISTS task_type VARCHAR(50) DEFAULT 'collection',
    ADD COLUMN IF NOT EXISTS title VARCHAR(255),
    ADD COLUMN IF NOT EXISTS description TEXT,
    ADD COLUMN IF NOT EXISTS priority VARCHAR(20) DEFAULT 'medium',
    ADD COLUMN IF NOT EXISTS dependencies JSONB DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS attachments JSONB DEFAULT '[]'::jsonb;

-- Create indexes for new columns
CREATE INDEX IF NOT EXISTS idx_evidence_tasks_type ON evidence_collection_tasks(task_type);
CREATE INDEX IF NOT EXISTS idx_evidence_tasks_priority ON evidence_collection_tasks(priority);

-- Add CHECK constraints for valid values
ALTER TABLE evidence_collection_tasks
    DROP CONSTRAINT IF EXISTS check_task_type,
    ADD CONSTRAINT check_task_type
        CHECK (task_type IN ('feasibility', 'setup', 'collection', 'review', 'documentation', 'issue'));

ALTER TABLE evidence_collection_tasks
    DROP CONSTRAINT IF EXISTS check_priority,
    ADD CONSTRAINT check_priority
        CHECK (priority IN ('low', 'medium', 'high', 'critical'));

-- Update existing tasks to have titles
UPDATE evidence_collection_tasks
SET
    title = CONCAT('Collect Evidence: ', et.evidence_id),
    task_type = 'collection'
FROM evidence_tracking et
WHERE evidence_collection_tasks.evidence_tracking_id = et.id
  AND evidence_collection_tasks.title IS NULL;

-- Log completion
DO $$
BEGIN
    RAISE NOTICE 'Migration 002_enhance_tasks_system.sql completed successfully';
    RAISE NOTICE 'Added columns: task_type, title, description, priority, dependencies, attachments';
    RAISE NOTICE 'Updated existing tasks with titles and task_type=collection';
END $$;
