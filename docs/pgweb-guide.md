# pgweb guide

pgweb is a lightweight web-based PostgreSQL browser that provides a graphical interface for viewing and managing the nixx database. It's a single Go binary that runs as a local web server, giving you easy access to your data without heavy IDE tools.

## Access

**URL:** http://localhost:8081

**Service control:**

pgweb is part of `nixx.target` and starts automatically when the target is started.
Individual control:
```bash
sudo systemctl start nixx-pgweb      # start
sudo systemctl stop nixx-pgweb       # stop
sudo systemctl restart nixx-pgweb    # restart
sudo systemctl status nixx-pgweb     # check status
sudo journalctl -u nixx-pgweb -f     # view logs
```

To start the full stack (including pgweb):
```bash
sudo systemctl start nixx.target
```

**Manual start** (if not using systemd):
```bash
bash scripts/pgweb.sh          # default port 8081
bash scripts/pgweb.sh 9000     # custom port
```

## Interface overview

### Left sidebar

- **Structure** - list of all tables and their row counts
- **Query** - SQL query editor with syntax highlighting
- **History** - previous queries you've run
- **Connection** - database connection info
- **Activity** - current PostgreSQL connections and queries

### Main panel

Displays query results, table contents, or table schema depending on context.

**Table view:**
- Click any table name in Structure to view its contents
- Sortable columns (click header to sort)
- Pagination controls at bottom
- Row count displayed

**Query editor:**
- Syntax highlighting for SQL
- Execute with Cmd+Enter (Mac) or Ctrl+Enter (Linux/Windows)
- Results appear below the editor
- Export results as CSV, JSON, or XML

## Common tasks

### View table contents

1. Click **Structure** in the sidebar
2. Click the table name (buffer, sources, or memories)
3. Browse rows with pagination controls

### Run a query

1. Click **Query** in the sidebar
2. Type or paste SQL
3. Press **Ctrl+Enter** or click **Run Query**
4. Results appear below

**Shortcut:** Use the queries from [docs/queries.md](queries.md) - copy/paste them into the query editor.

### View table schema

1. Click **Structure** in sidebar
2. Click the **info icon** (ⓘ) next to the table name
3. See columns, types, constraints, indexes

### Export data

1. Run a query or view a table
2. Click **Export** button above results
3. Choose format: CSV, JSON, or XML
4. File downloads to your browser's download folder

### Search/filter table data

In the Query editor, use WHERE clauses:
```sql
-- Find sources by name
SELECT * FROM sources WHERE name LIKE '%readme%';

-- Recent buffer entries
SELECT * FROM buffer WHERE created_at > NOW() - INTERVAL '1 day';

-- Memories for a specific source
SELECT * FROM memories WHERE source_id = 5;
```

### Delete data

Execute DELETE statements in the Query editor:
```sql
-- Delete a source and its memories (use a transaction)
BEGIN;
DELETE FROM memories WHERE source_id = 5;
DELETE FROM sources WHERE id = 5;
COMMIT;
```

**Warning:** DELETE is permanent. Consider backing up first or exporting data before deleting.

## Nixx-specific operations

### View all sources with memory counts

```sql
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
```

### Find a specific conversation in the buffer

```sql
SELECT * FROM buffer 
WHERE content LIKE '%search term%' 
ORDER BY id;
```

### See what's in a buffer source

```sql
-- First, get the source's start/end IDs
SELECT id, name, start_id, end_id FROM sources WHERE id = 3;

-- Then view those buffer entries
SELECT * FROM buffer WHERE id >= 10 AND id <= 15 ORDER BY id;
```

### Find orphaned memories

```sql
-- Memories with no source (shouldn't normally exist)
SELECT id, LEFT(content, 100) as preview 
FROM memories 
WHERE source_id IS NULL;
```

### Check memory usage

```sql
-- Total characters stored
SELECT 
    SUM(LENGTH(content)) as buffer_chars,
    (SELECT SUM(LENGTH(content)) FROM memories) as memory_chars,
    (SELECT SUM(LENGTH(summary)) FROM sources) as source_chars;

-- Database size
SELECT pg_size_pretty(pg_database_size('nixx'));
```

### View memory distribution

```sql
-- How many memory embeddings per source?
SELECT 
    s.name,
    s.type,
    COUNT(m.id) as chunk_count
FROM sources s
LEFT JOIN memories m ON s.id = m.source_id
GROUP BY s.name, s.type
ORDER BY COUNT(m.id) DESC;
```

## Tips and tricks

**Keyboard shortcuts:**
- `Ctrl+Enter` or `Cmd+Enter` - execute query
- `Ctrl+L` - clear query editor

**SQL helpers:**
- Use `LIMIT` to preview large tables: `SELECT * FROM buffer LIMIT 10;`
- Use `ORDER BY id DESC` to see newest entries first
- Use `LEFT(content, 100)` to preview long text fields

**Transaction safety:**
When deleting or modifying data, wrap in a transaction so you can rollback if needed:
```sql
BEGIN;
-- your DELETE or UPDATE here
-- Check the results first!
SELECT * FROM sources;
-- If it looks wrong: ROLLBACK;
-- If it looks right: COMMIT;
```

**Connection info:**
pgweb reads the connection string from `.env` (`NIXX_DATABASE_URL`). If you change it, restart the service:
```bash
sudo systemctl restart nixx-pgweb
```

## Limitations

pgweb is read-mostly optimized. It's great for:
- Exploring data
- Running ad-hoc queries
- Quick cleanup operations
- Exporting datasets

It's **not** designed for:
- Bulk data imports (use `psql` or the nixx API instead)
- Schema migrations (use nixx's `init_schema()` or `psql`)
- Complex transactions (use `psql` for those)
- Production data modification (always test in dev first)

## Troubleshooting

**pgweb won't start:**
```bash
# Check if port 8081 is already in use
sudo lsof -i :8081

# Check service logs
sudo journalctl -u nixx-pgweb -n 50

# Verify .env file exists and has NIXX_DATABASE_URL
cat /home/siderealyear/nixx/.env | grep NIXX_DATABASE_URL
```

**Can't connect to database:**
- Verify PostgreSQL is running: `sudo systemctl status postgresql`
- Test connection directly: `source .env && psql "$NIXX_DATABASE_URL" -c "SELECT 1;"`
- Check firewall if using remote PostgreSQL

**Query returns no results:**
- Check table actually has data: `SELECT COUNT(*) FROM tablename;`
- Verify WHERE clause logic
- Check for typos in column names (use Structure tab to see exact names)

## Security notes

pgweb binds to `0.0.0.0:8081` by default, meaning it's accessible from any network interface. In the systemd service, this allows access from:
- localhost (127.0.0.1)
- Your local network IP
- Tailscale VPN (once configured)

**Production considerations:**
- pgweb has no authentication - anyone who can reach port 8081 has full database access
- If exposing remotely, put it behind a reverse proxy with auth (nginx + basic auth, or Tailscale)
- Or bind to localhost only: change `--bind=0.0.0.0` to `--bind=127.0.0.1` in the service file

**For remote access via Tailscale:**
Port 8081 will be accessible on your Tailscale IP once Tailscale is set up. All traffic goes through the encrypted Tailscale network, so no additional authentication is strictly needed, but consider your threat model.

## Alternative: psql

For command-line work, `psql` is often faster:
```bash
source .env

# Interactive shell
psql "$NIXX_DATABASE_URL"

# One-off query
psql "$NIXX_DATABASE_URL" -c "SELECT * FROM sources;"

# Run a query file
psql "$NIXX_DATABASE_URL" -f queries.sql

# Output as CSV
psql "$NIXX_DATABASE_URL" -c "SELECT * FROM sources;" --csv > sources.csv
```

Both tools are useful - pgweb for exploration, psql for scripting and automation.
