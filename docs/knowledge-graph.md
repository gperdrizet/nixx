# Knowledge graph architecture

## Vision

Nixx is evolving from a three-tier memory system (buffer → sources → memories) into a unified **personal knowledge graph** that integrates with your existing tools and workflows.

The goal: one interface to all your knowledge - research papers, personal notes, code, documentation - with LLM-powered semantic search and retrieval.

## Current State (March 8, 2026)

**What works today:**
- Three-tier memory: buffer (raw conversation) → sources (meaningful units) → memories (embedded chunks)
- Conversation archival via `/source "name"` command
- Document ingestion: `nixx ingest file.md` or `nixx ingest https://url`
- Semantic recall: top-k cosine similarity search over all memory embeddings
- Direct lookup: `/lookup "name"` to retrieve full source content
- Source types: `buffer` (conversations), `document` (local files), `web` (URLs)

**Schema:**
```sql
buffer       -- all messages (user + assistant), append-only tape
sources      -- meaningful units: conversations, docs, web pages, papers, repos
memories     -- embedded chunks with metadata (chunk index, source_id FK)
```

## The Bigger Picture: Knowledge Graph

### Source Types

Each source type needs specialized handling:

#### 1. Conversations (buffer sources)
**Current:** ✅ Working
- Stored in `buffer` table as raw messages
- `/source "name"` creates a source spanning buffer range
- Transcript chunked and embedded verbatim (no LLM summarization)
- Use case: "What did we decide about the memory architecture?"

#### 2. Papers (planned)
**Status:** 🚧 Design phase
- **Source:** PDF files, managed in JabRef reference library
- **Storage:** 
  - Full PDF stored in filesystem (`data/papers/`)
  - Bibliographic metadata in `sources`: authors, year, journal, DOI, abstract
  - Full text extracted and chunked → `memories`
  - Summary/abstract embedded separately for quick recall
- **Bidirectional sync:** 
  - Papers added to JabRef → auto-ingested to nixx
  - Papers referenced in conversation → optionally add to JabRef
- **Retrieval modes:**
  - Semantic: "papers about transformer attention mechanisms"
  - Metadata: "papers by Vaswani from 2017"
  - Citation graph: "papers citing Attention Is All You Need"
- **Ingestion:**
  - `nixx ingest paper.pdf --source=jabref --doi=10.xxxx`
  - Extract text via `pypdf` or `pdfplumber`
  - Parse citations via `anystyle` or `grobid`
  - Store full-text searchable (PostgreSQL tsvector) + embeddings

#### 3. Personal Notes (planned)
**Status:** 🚧 Design phase
- **Source:** Logseq markdown graph (`~/logseq/`)
- **Storage:**
  - Watch filesystem for changes (inotify)
  - Parse markdown + Logseq-specific syntax (properties, queries, links)
  - Preserve graph structure: `[[page links]]`, block references `((uuid))`
  - Each page/journal entry → one source
  - Blocks embedded individually with context (parent page, hierarchy)
- **Bidirectional sync:**
  - Logseq changes → auto-update nixx
  - Conversation outcomes → optionally create Logseq page/journal entry
- **Graph preservation:**
  - Store page links in `sources` metadata or separate `links` table
  - Enable queries like "all notes linking to project X"
  - Visualize knowledge graph (optional, via graphviz or d3.js)
- **Ingestion:**
  - Initial: `nixx ingest logseq ~/logseq/`
  - Continuous: filesystem watcher updates on save
  - Respect `.gitignore` and Logseq config

#### 4. Code (planned)
**Status:** 🚧 Design phase
- **Source:** GitHub repositories (owned + referenced)
- **Storage:**
  - Clone to `data/repos/<owner>/<repo>`
  - README, CONTRIBUTING, docs/ → embedded in `memories`
  - Code files → indexed but **not fully embedded** (too large, context changes fast)
  - Use AST parsing + symbol extraction for semantic code search
  - Track commit history, branches, issues metadata
- **Two categories:**
  - **My repos:** bidirectional (push changes, create issues)
  - **Referenced repos:** read-only (dependencies, examples, inspiration)
- **Retrieval modes:**
  - Semantic: "how does FastAPI handle dependency injection?"
  - Symbol: "find all usages of create_source function"
  - Git history: "when did we add pgvector support?"
- **Ingestion:**
  - `nixx ingest github yourusername/repo` (my repo)
  - `nixx ingest github fastapi/fastapi --readonly` (reference)
  - Periodic `git pull` for updates
- **Code-specific indexing:**
  - Use tree-sitter for language-agnostic AST parsing
  - Extract functions, classes, imports → searchable symbols table
  - README/docs embedded, code stays in filesystem with grep/ripgrep access

#### 5. Documentation (planned improvements)
**Current:** ✅ Basic ingestion works (`nixx ingest https://url`)
**Improvements needed:**
- **Site mapping:** Crawl entire doc site (e.g., all of docs.pydantic.dev)
- **Update tracking:** Re-ingest on changes (check etag/last-modified)
- **Link preservation:** Store internal links between doc pages
- **Navigation:** "Show me the FastAPI tutorial, section 3"
- **Ingestion:**
  - `nixx ingest docs https://docs.pydantic.dev --recursive` (crawl site)
  - `nixx ingest docs https://textual.textualize.io --depth=2` (limit depth)

### Schema Extensions

Current schema supports basic source types. Extensions needed:

#### Sources table additions
```sql
ALTER TABLE sources ADD COLUMN url TEXT;              -- for web/docs
ALTER TABLE sources ADD COLUMN filepath TEXT;         -- for local files
ALTER TABLE sources ADD COLUMN doi TEXT;              -- for papers
ALTER TABLE sources ADD COLUMN metadata JSONB DEFAULT '{}';  
-- metadata examples:
--   papers: {authors: [], year: int, journal: str, cited_by_count: int}
--   repos: {owner: str, stars: int, language: str, last_commit: timestamp}
--   notes: {links: [page names], tags: [str]}
```

#### New tables for specialized data

**papers:**
```sql
CREATE TABLE papers (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT REFERENCES sources(id) ON DELETE CASCADE,
    doi TEXT UNIQUE,
    arxiv_id TEXT,
    title TEXT NOT NULL,
    authors JSONB NOT NULL,  -- [{name: str, affiliation: str}]
    year INTEGER,
    journal TEXT,
    abstract TEXT,
    pdf_path TEXT,
    citations JSONB,  -- [{doi: str, title: str}] extracted from PDF
    cited_by_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX papers_doi_idx ON papers(doi);
CREATE INDEX papers_year_idx ON papers(year);
```

**repos:**
```sql
CREATE TABLE repos (
   id BIGSERIAL PRIMARY KEY,
    source_id BIGINT REFERENCES sources(id) ON DELETE CASCADE,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    clone_path TEXT,  -- local filesystem path
    is_owned BOOLEAN DEFAULT FALSE,
    language TEXT,
    stars INTEGER,
    last_commit TIMESTAMPTZ,
    last_synced TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(owner, name)
);

-- Code symbols (functions, classes) extracted via AST parsing
CREATE TABLE symbols (
    id BIGSERIAL PRIMARY KEY,
    repo_id BIGINT REFERENCES repos(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,  -- function, class, method, variable
    file_path TEXT NOT NULL,
    line_number INTEGER,
    signature TEXT,  -- full function/class signature
    docstring TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX symbols_name_idx ON symbols(name);
CREATE INDEX symbols_repo_file_idx ON symbols(repo_id, file_path);
```

**notes (Logseq):**
```sql
CREATE TABLE notes (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT REFERENCES sources(id) ON DELETE CASCADE,
    page_title TEXT NOT NULL UNIQUE,
    filepath TEXT NOT NULL,
    links JSONB,  -- [page titles] this note links to
    tags JSONB,  -- [tag names]
    properties JSONB,  -- Logseq page properties
    last_modified TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX notes_page_title_idx ON notes(page_title);

-- Page/block links for graph traversal
CREATE TABLE note_links (
    from_note_id BIGINT REFERENCES notes(id) ON DELETE CASCADE,
    to_note_id BIGINT REFERENCES notes(id) ON DELETE CASCADE,
    PRIMARY KEY (from_note_id, to_note_id)
);
```

### Projects: The Grouping Layer

**Concept:** A project is a logical grouping of related sources and conversations.

Example project: "nixx development"
- Sources: nixx README, architecture docs, FastAPI docs, Textual docs, pydantic-settings docs
- Conversations: All nixx-related chat sessions
- Repos: gperdrizet/nixx, related libraries
- Papers: Retrieval-augmented generation papers, vector database research

**Schema:**
```sql
CREATE TABLE projects (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE project_sources (
    project_id BIGINT REFERENCES projects(id) ON DELETE CASCADE,
    source_id BIGINT REFERENCES sources(id) ON DELETE CASCADE,
    PRIMARY KEY (project_id, source_id)
);
```

**Usage:**
```bash
# Create project
nixx project create "nixx development" --desc="Building the personal knowledge graph"

# Add sources to project
nixx project add-source "nixx development" --source-id=3
nixx project add-source "nixx development" --name="FastAPI docs"

# Conversation in project context
nixx chat --project="nixx development"  # recall scoped to project sources

# List project sources
nixx project show "nixx development"
```

### Retrieval: Hybrid Search

Combine multiple search strategies for better results:

1. **Semantic (vector search)** - current implementation
   - Embed query → cosine similarity against `memories.embedding`
   - Good for fuzzy concept matching
   - Works across all source types

2. **Keyword (full-text search)** - planned
   - PostgreSQL `tsvector` + `ts_rank`
   - Add `ALTER TABLE memories ADD COLUMN content_tsvector tsvector;`
   - Update trigger to keep tsvector synced with content
   - Query: `SELECT * FROM memories WHERE content_tsvector @@ to_tsquery('pydantic & settings')`

3. **Metadata filters** - partially implemented
   - Source type: `sources.type IN ('paper', 'web')`
   - Date range: `sources.created_at > '2026-01-01'`
   - Project: `sources.id IN (SELECT source_id FROM project_sources WHERE project_id = ?)`
   - Paper year: `papers.year BETWEEN 2020 AND 2025`

4. **Structured queries** - planned
   - Papers: by author, DOI, citation count
   - Repos: by language, stars, owner
   - Notes: by tags, page links
   - Code: by symbol name, file path

**Hybrid ranking:**
```python
async def hybrid_search(query: str, filters: dict, top_k: int = 10):
    # Semantic score (0-1, cosine similarity)
    semantic_results = await vector_search(query, top_k=50)
    
    # Keyword score (0-1, normalized ts_rank)
    keyword_results = await fulltext_search(query, top_k=50)
    
    # Combine with weighted sum
    combined = {}
    for r in semantic_results:
        combined[r.id] = {"semantic": r.score, "keyword": 0, "content": r.content}
    for r in keyword_results:
        if r.id in combined:
            combined[r.id]["keyword"] = r.score
        else:
            combined[r.id] = {"semantic": 0, "keyword": r.score, "content": r.content}
    
    # Weighted ranking: 60% semantic, 40% keyword
    for item in combined.values():
        item["score"] = 0.6 * item["semantic"] + 0.4 * item["keyword"]
    
    # Sort and return top-k
    ranked = sorted(combined.items(), key=lambda x: x[1]["score"], reverse=True)
    return ranked[:top_k]
```

### Tool Integration

#### JabRef (bibliography management)
- **Export BibTeX:** JabRef can auto-export to `.bib` file on save
- **Watch file:** Nixx monitors `~/Documents/library.bib` for changes
- **Parse BibTeX:** Python `bibtexparser` library
- **PDF linking:** JabRef stores PDF path in `file` field → nixx ingests PDF
- **Sync flow:**
  1. Add paper to JabRef with PDF
  2. JabRef exports updated `.bib`
  3. Nixx detects change, parses new entry
  4. Nixx ingests PDF, stores metadata, embeds chunks
  5. Paper now searchable in nixx

#### Logseq (note-taking)
- **Filesystem sync:** Logseq stores everything as markdown in `~/logseq/`
- **Watch directory:** Python `watchdog` library for inotify events
- **Parse markdown:** `markdown-it-py` + custom Logseq syntax parser
- **Graph preservation:** Extract `[[page links]]` and `((block refs))`
- **Sync flow:**
  1. Edit page in Logseq, save
  2. Nixx detects file change
  3. Parse markdown, update `notes` table
  4. Update `note_links` graph
  5. Re-embed changed blocks in `memories`

#### GitHub (code hosting)
- **API access:** PyGithub or direct REST calls
- **Clone repos:** `git clone` to `data/repos/`
- **Update check:** `git fetch` periodically, compare commit SHAs
- **Sync flow:**
  1. `nixx ingest github owner/repo`
  2. Clone to local filesystem
  3. Extract README → embed in memories
  4. Parse code files via tree-sitter → symbols table
  5. Track last commit SHA
  6. Periodic update: `git pull`, re-index changed files

## Implementation Roadmap

### Phase 1: Foundation (complete)
- ✅ Three-tier memory system
- ✅ Conversation sources
- ✅ Document/web ingestion
- ✅ Semantic recall
- ✅ Direct source lookup
- ✅ TUI commands: `/source`, `/sources`, `/lookup`

### Phase 2: Paper Support (next)
1. Add `papers` table schema
2. PDF text extraction (pypdf or pdfplumber)
3. BibTeX parser
4. JabRef `.bib` file watcher
5. Ingestion: `nixx ingest paper path/to/paper.pdf --bibtex=entry.bib`
6. Metadata-based search: author, year, DOI
7. TUI command: `/papers` (list), `/paper <doi>` (lookup)

### Phase 3: Logseq Integration
1. Add `notes` and `note_links` tables
2. Markdown parser with Logseq syntax support
3. Filesystem watcher for `~/logseq/`
4. Graph structure preservation
5. Bidirectional sync (optional): conversation → create Logseq page
6. TUI command: `/notes` (list), `/note "page title"` (lookup)

### Phase 4: GitHub Integration
1. Add `repos` and `symbols` tables
2. Git clone + periodic pull
3. Tree-sitter for AST parsing
4. Symbol extraction (functions, classes)
5. README embedding, code indexing (not embedding)
6. TUI commands: `/repos`, `/code <symbol>`, `/repo <owner/name>`

### Phase 5: Hybrid Search
1. Add `content_tsvector` to memories
2. Implement full-text search (ts_query)
3. Hybrid ranking (combine vector + keyword)
4. Advanced filters (type, date, project, metadata)
5. TUI: Enhanced `/recall` with filters

### Phase 6: Projects
1. Add `projects` and `project_sources` tables
2. CLI: `nixx project create/add-source/show`
3. Scoped recall: `nixx chat --project="name"`
4. Project-aware source listing

### Phase 7: Advanced Features
- Citation graph visualization (papers citing papers)
- Note graph visualization (Logseq page links)
- Smart suggestions: "You're working on X, these papers/notes might help"
- Tool calling: Nixx can autonomously look up sources mid-conversation
- Self-modification: Nixx writes code, creates PRs to her own repo

## Design Principles

1. **Local-first:** All data stored locally, no cloud dependencies
2. **Incremental:** Build on the working foundation, don't rewrite
3. **Tool-agnostic interface:** JabRef/Logseq/GitHub are plugins, not core dependencies
4. **Bidirectional when useful:** Sync from tools → nixx (always), nixx → tools (optional)
5. **Preserve source truth:** Don't modify original files (JabRef library, Logseq notes, GitHub repos)
6. **Semantic + structured:** Vector search for discovery, SQL for precise lookup
7. **Scale-aware:** Embeddings for summaries/abstracts, not all code; use targeted indexing
8. **Testable:** Every integration lives in `nixx/integrations/<tool>/`, mocked in tests

## Open Questions

1. **Storage:** PostgreSQL + filesystem, or separate document store (e.g., Meilisearch)?
2. **PDF extraction:** pypdf vs pdfplumber vs commercial API (GROBID)?
3. **Code embeddings:** Skip entirely, or embed docstrings/comments only?
4. **Logseq sync frequency:** Real-time (inotify) or periodic (every N minutes)?
5. **Citation parsing:** Extract from PDFs (anystyle) or require DOI lookup (Crossref API)?
6. **Graph storage:** SQL (current approach) or dedicated graph DB (Neo4j, dgraph)?
7. **Update strategy:** Full re-index on change, or incremental updates?

## References

- [Zotero architecture](https://www.zotero.org/support/dev/client_coding/architecture) - similar problem (papers + notes)
- [Obsidian Dataview](https://blacksmithgu.github.io/obsidian-dataview/) - note graph queries
- [Sourcegraph](https://sourcegraph.com/) - code search at scale
- [Semantic Scholar API](https://api.semanticscholar.org/) - paper metadata + citations
- [tree-sitter](https://tree-sitter.github.io/tree-sitter/) - AST parsing for code indexing
