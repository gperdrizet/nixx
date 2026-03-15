# Build log: March 15, 2026 - LLM backend and TUI overhaul

## What got built

Big infrastructure day. Swapped the LLM backend, split the embedding server, fixed two
conversation handling bugs, and overhauled the TUI with edit, rewind, and persistent history.

### LLM backend: Ollama to llama.cpp

Removed Ollama entirely. The remote server at `gpt.perdrizet.org` runs llama.cpp with
`gpt-oss-20b`, and that's the only chat backend now.

What got deleted:
- `OllamaClient` class and its tests
- `ollama.service` systemd unit
- `create_llm_client` factory function and `LLMClient` type alias
- `llm_provider` and `memory_provider` config fields

All callers now construct `OpenAIClient` directly - no factory, no abstraction layer. The
config gained `extra="ignore"` so stale `.env` files with old provider fields don't break on
startup.

### Split embedding server

After the Ollama removal, the remote llama.cpp server returned 501 on `/v1/embeddings` -
it only serves chat completions. Since the remote box is shared with students, running a
second model there wasn't an option.

Solution: local llama.cpp instance dedicated to embeddings.

- New config field: `embedding_base_url` (default `http://localhost:8082`)
- `MemoryStore` and `IngestPipeline` now hold two `OpenAIClient` instances: `_llm` for chat,
  `_embedder` for embeddings
- New systemd unit: `nixx-embed.service` runs `llama-server` with `mxbai-embed-large-v1-f16.gguf`
  on port 8082 (`--embedding --ctx-size 512`)
- `nixx-server.service` depends on `nixx-embed.service`

Port 8081 was taken by pgweb, so embeddings landed on 8082. The model file lives in
`models/mxbai-embed-large-v1-f16.gguf` (~670 MB), excluded from git via `.gitignore`.

### LangChain discussion

Considered whether to adopt LangChain or smolagents. Decided against both. The reasoning:

- Nixx is a single-user local system with one LLM, one embedding model, one database.
  LangChain's abstractions (chains, agents, memory classes) solve multi-provider orchestration
  problems we don't have.
- The codebase is already simple - `OpenAIClient` is ~100 lines, `MemoryStore` is ~80 lines.
  Wrapping these in framework abstractions would add complexity without reducing it.
- Targeted small libraries (httpx, pgvector, pydantic) give us exactly what we need without
  pulling in a dependency tree.

The "LangChain is declining" framing that shows up in some discussions is editorial synthesis,
not established fact. The real argument is simpler: for this project's scope, the framework
doesn't earn its weight.

### Conversation handling fixes

Found two bugs in the chat completions handler:

1. **Diluted semantic recall** - the recall query joined all user messages into one string.
   As conversations grew, early messages dominated the embedding and recall quality degraded.
   Fixed by extracting only the last user message for recall.

2. **Quadratic buffer growth** - `user_text = " ".join(all_user_messages)` was written to the
   buffer on every exchange. A 10-message conversation would write the full concatenated history
   each time. Fixed by the same change - `last_user` captures only the current turn.

### TUI improvements

Four changes to `nixx chat`:

**Edit and regenerate** - focus a user message (Tab to navigate, or click), press Enter. The
message text loads into the input field for editing. Press Enter again to save the edit,
truncate history after that point, and re-stream the response. Escape cancels. This is the
ChatGPT-style edit flow.

**Rewind** - focus any user or assistant message, press Backspace. Removes that message and
everything after it from both the UI and history. Shows "Conversation rewound." in the chat.

**Persistent history** - conversation history saves to `data/memory/chat_history.json` after
every exchange, edit, and rewind. On startup, if a history file exists, messages are restored
into the chat. Ctrl+L clears history and deletes the file.

**/help command** - prints all available slash commands and keybindings as a system message.
Shown automatically on first launch (no existing history). Available anytime via `/help`.

## Current state

Services running on pyrite:
- `nixx-server` (port 8000) - FastAPI, depends on postgres + embed
- `nixx-embed` (port 8082) - llama.cpp with mxbai-embed-large, embeddings only
- `student-postgres` (port 5432) - PostgreSQL + pgvector
- `nixx-pgweb` (port 8081) - database browser
- Remote: `gpt.perdrizet.org` - llama.cpp with gpt-oss-20b, chat only

51 tests passing. ruff, black, mypy all clean.
