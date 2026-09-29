-- Add archived status to projects table
BEGIN;

-- Add archived status option via check constraint
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM pg_constraint 
        WHERE conname = 'projects_status_check'
    ) THEN
        EXECUTE 'ALTER TABLE projects ADD CONSTRAINT projects_status_check CHECK (status IN (''active'', ''archived''))';
    END IF;
END $$;

-- Update existing projects to archived if they're duplicates
UPDATE projects 
SET status = 'archived'
WHERE id IN (
    SELECT id 
    FROM projects 
    WHERE name = 'iV Phase 1'
    AND created_at < (SELECT MAX(created_at) FROM projects WHERE name = 'iV Phase 1')
);

COMMIT;