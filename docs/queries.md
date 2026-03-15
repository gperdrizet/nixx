# Useful database queries

Quick reference for common database operations. Use these with pgweb or psql.

## Overview

```sql
-- Row counts
SELECT 'buffer' as table_name, COUNT(*) as count FROM buffer
UNION ALL SELECT 'sources', COUNT(*) FROM sources
UNION ALL SELECT 'memories', COUNT(*) FROM memories;

-- Database size
SELECT pg_size_pretty(pg_database_size('nixx'));
```

## Sources

```sql
-- List all sources with memory counts
SELECT 
    s.id, 
    s.name, 
    s.type,
    s.created_at,
    COUNT(m.id) as memory_count
FROM sources s
LEFT JOIN memories m ON s.id = m.source_id
GROUP BY s.id, s.name, s.type, s.created_at
ORDER BY s.created_at DESC;

-- Show a specific source with full details
SELECT * FROM sources WHERE id = 5;

-- Sources by type
SELECT type, COUNT(*) as count 
FROM sources 
GROUP BY type;

-- Recent sources
SELECT id, name, type, created_at 
FROM sources 
ORDER BY created_at DESC 
LIMIT 10;
```

## Buffer

```sql
-- Recent messages
SELECT id, role, LEFT(content, 60) as content_preview, origin, created_at
FROM buffer
ORDER BY id DESC
LIMIT 20;

-- Messages in a specific source range
SELECT * FROM buffer 
WHERE id >= 10 AND id <= 20 
ORDER BY id ASC;

-- Message counts by role
SELECT role, COUNT(*) as count 
FROM buffer 
GROUP BY role;

-- Buffer size (total characters)
SELECT SUM(LENGTH(content)) as total_chars FROM buffer;
```

## Memories

```sql
-- Top 10 most recent memories
SELECT id, LEFT(content, 80) as content_preview, source_id, created_at
FROM memories
ORDER BY created_at DESC
LIMIT 10;

-- Orphaned memories (no source)
SELECT COUNT(*) as orphaned_count 
FROM memories 
WHERE source_id IS NULL;

-- Memories for a specific source
SELECT id, LEFT(content, 100) as preview, created_at
FROM memories
WHERE source_id = 5
ORDER BY created_at;

-- Memory distribution by source
SELECT 
    source_id,
    s.name as source_name,
    COUNT(*) as memory_count
FROM memories m
LEFT JOIN sources s ON m.source_id = s.id
GROUP BY source_id, s.name
ORDER BY COUNT(*) DESC;
```

## Cleanup operations

```sql
-- Delete a source and its memories (transaction)
BEGIN;
DELETE FROM memories WHERE source_id = 5;
DELETE FROM sources WHERE id = 5;
COMMIT;

-- Delete orphaned memories
DELETE FROM memories WHERE source_id IS NULL;

-- Delete old buffer entries (older than 30 days, not referenced by sources)
-- WARNING: This loses conversation history!
DELETE FROM buffer 
WHERE created_at < NOW() - INTERVAL '30 days'
AND id NOT IN (
    SELECT start_id FROM sources WHERE start_id IS NOT NULL
    UNION
    SELECT end_id FROM sources WHERE end_id IS NOT NULL
);
```

## Semantic search (manual)

```sql
-- Find memories similar to a query embedding
-- (You'd need to generate the embedding first via the LLM API)
-- This example shows the structure:
SELECT 
    id,
    content,
    source_id,
    1 - (embedding <=> '[... your 1024-d vector here ...]') AS similarity
FROM memories
ORDER BY embedding <=> '[... your 1024-d vector here ...]'
LIMIT 10;
```

## Schema inspection

```sql
-- List all tables
\dt

-- Show table structure
\d sources
\d buffer
\d memories

-- Show indexes
\di

-- Show foreign key constraints
SELECT
    tc.table_name, 
    kcu.column_name,
    ccu.table_name AS foreign_table_name,
    ccu.column_name AS foreign_column_name 
FROM information_schema.table_constraints AS tc 
JOIN information_schema.key_column_usage AS kcu
    ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage AS ccu
    ON ccu.constraint_name = tc.constraint_name
WHERE constraint_type = 'FOREIGN KEY';
```
