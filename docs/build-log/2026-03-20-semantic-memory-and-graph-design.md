# 2026-03-20: semantic memory and graph design

## What got built

Schema groundwork for semantic memory projects and a knowledge graph layer. No
application logic yet - this session was mostly design, ending in schema changes
that lay the foundation for what comes next.

## Design discussion

### What semantic memory is today

The `sources` + `memories` schema already exists and works. `nixx ingest` can pull
in files and web pages. What's missing is: project-scoping, recall injection per
turn, keyword search, and any graph structure.

### The graph question

Spent a long time on this. The key insight: **similarity-based graph edges add
nothing** - that's just slower vector search. The graph only adds value when edges
encode relationships that aren't visible in content similarity at all.

Example: a project's documentation and a Wikipedia article on Hebbian learning
may share almost no vocabulary, but if that project implements Hebbian weighting,
there's a real functional connection. Vector search will never surface it. A typed
edge can.

So the two recall signals are orthogonal:

- **Vector search** - finds semantically similar content via embedding cosine distance
- **Graph traversal** - finds functionally/logically related sources via typed edges

### Edge types

Structural edges, created at ingest by LLM extraction:
- `uses` - this code/project uses that library
- `cites` - this paper/note references that work
- `implements` - this code implements that algorithm/paper
- `contradicts` - this source disagrees with that one
- `derived_from` - this is based on / forked from that
- `part_of` - this is a section/chapter of that

Hebbian edges, created by co-activation in recall:
- `associated_with` - float weight, decays over time

### Hebbian weighting

Hebb's postulate applied: when two sources are co-recalled in the same query,
the edge between them strengthens. When they're never co-recalled, it decays.

Weight update on co-activation:
```
new_weight = old_weight + η * (1 - old_weight)
```

Effective weight on read (decay without a clock):
```
effective_weight = weight * e^(-λ * days_since_activation)
```

This means the graph self-organizes around actual usage patterns, not pre-computed
structural similarity. Early on it's sparse - mostly structural edges from ingest.
Over time, Hebbian edges fill in the relationships your workflow considers important,
which may be completely non-obvious from text content alone.

Initial Hebbian edges: threshold is low (Hebbian pruning handles the rest). An
`associated_with` edge is created the first time two sources are co-recalled, then
strengthened with each co-activation. Unused edges decay below the visibility
threshold and are effectively pruned.

Cosine similarity does **not** seed Hebbian edges. The graph starts sparse.

### What's still unsettled

The graph design isn't finished. A key realization from this session: **a lot of
graph building will have to be manual**. Automated extraction handles the obvious
structural cases (citations, imports, explicit references). But many important
connections require a human brain to recognize - the kind of "this is related to
that" judgment that emerges from domain understanding, not text overlap.

So the priority for graph tooling should be: **make it easy for the user to create
and annotate edges**. The automated parts (Hebbian co-activation, LLM extraction at
ingest) are a convenience on top of manual curation, not a replacement for it.

Still open:
- Entity resolution strategy (how to match "numpy" in an import to the numpy source)
  - Embedding the extracted name and matching against source names/summaries is the
    current candidate, but fuzzy lookup may be needed for code
- Whether to store unresolved references and fill them in later
- TUI/API interface for manual edge creation

## What got built: schema

Added two new tables to the database schema:

### `source_projects` (many-to-many: sources ↔ projects)

A source can belong to multiple projects. A project is just a string label - no
separate projects table, keeping it lightweight. Recall can be filtered by project.

### `source_edges` (knowledge graph edges)

Composite primary key on `(from_id, to_id)` - one edge per pair, updated in-place
on Hebbian activation. Columns:
- `relation` - edge type string (see above)
- `weight` - float, Hebbian weight (starts low)
- `activations` - integer count of co-recall events
- `last_activated` - timestamp for decay calculation

Undirected edges are stored as directed pairs (both directions) to simplify
traversal queries. Structural edges from LLM extraction are inserted bidirectionally
at ingest. Hebbian edges are inserted bidirectionally on first co-activation.

## What didn't get built

No application logic yet. The agenda for follow-on sessions:
1. Semantic recall injection per turn (alongside episodic)
2. Keyword search on sources/memories (FTS on `sources.name` + `memories.content`)
3. Ingest-time LLM extraction of structural edges
4. Hebbian co-activation in the recall path
5. Manual edge creation (TUI command + API endpoint)
6. `/recall` showing both vector hits and graph neighbours

## Tests

Schema migration runs on existing database without data loss - `CREATE TABLE IF
NOT EXISTS` and `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` throughout.
