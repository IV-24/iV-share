-- Fix approvals table schema
BEGIN;

-- Check if the action column exists, if not add it
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'approvals' 
        AND column_name = 'action'
    ) THEN
        EXECUTE 'ALTER TABLE approvals ADD COLUMN action TEXT';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'approvals' 
        AND column_name = 'requester'
    ) THEN
        EXECUTE 'ALTER TABLE approvals ADD COLUMN requester TEXT';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'approvals' 
        AND column_name = 'status'
    ) THEN
        EXECUTE 'ALTER TABLE approvals ADD COLUMN status TEXT DEFAULT ''pending''';
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'approvals' 
        AND column_name = 'created_at'
    ) THEN
        EXECUTE 'ALTER TABLE approvals ADD COLUMN created_at TIMESTAMPTZ DEFAULT NOW()';
    END IF;
END $$;

COMMIT;