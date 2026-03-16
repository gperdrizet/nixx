"""Nixx chat TUI."""

import json

import httpx
from rich.markdown import Markdown as RichMarkdown
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.widgets import Footer, Header, Input, Static

from nixx.config import NixxConfig


class Message(Static):
    """A single chat message bubble."""

    DEFAULT_CSS = """
    Message {
        padding: 1 2;
        margin: 0 1;
    }
    Message.user {
        background: $primary-darken-2;
        color: $text;
        margin-left: 8;
    }
    Message.assistant {
        background: $surface;
        color: $text;
        margin-right: 8;
    }
    Message.system {
        color: $text-muted;
        text-style: italic;
        margin: 0 2;
    }
    Message:focus {
        border: tall $accent;
    }
    """

    BINDINGS = [
        Binding("enter", "edit", "Edit"),
        Binding("backspace", "rewind", "Rewind"),
    ]

    def __init__(self, role: str, content: str = "", history_index: int | None = None) -> None:
        super().__init__(content, classes=role)
        self._role = role
        self._content = content
        self._history_index = history_index
        if role in ("user", "assistant"):
            self.can_focus = True

    def append(self, text: str) -> None:
        self._content += text
        self.update(self._content)

    def render_markdown(self) -> None:
        """Re-render content as markdown (for assistant messages)."""
        if self._content:
            self.update(RichMarkdown(self._content))

    def action_edit(self) -> None:
        if self._role == "user" and self._history_index is not None:
            app: NixxApp = self.app  # type: ignore[assignment]
            app.enter_edit_mode(self)

    def action_rewind(self) -> None:
        if self._history_index is not None:
            app: NixxApp = self.app  # type: ignore[assignment]
            app.rewind_to(self)


class NixxApp(App[None]):
    """Nixx chat terminal UI."""

    TITLE = "nixx"
    SUB_TITLE = "personal memory system"

    CSS = """
    Screen {
        layout: vertical;
    }
    #messages {
        height: 1fr;
        overflow-y: scroll;
        padding: 1 0;
    }
    #input-row {
        height: auto;
        padding: 0 1 1 1;
    }
    Input {
        width: 1fr;
    }
    #tag-row {
        height: auto;
        padding: 0 1 0 1;
        display: none;
    }
    #tag-input {
        width: 1fr;
        border: tall $warning;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear", "Clear"),
        Binding("escape", "cancel_edit", "Cancel", show=False),
    ]

    def __init__(self, config: NixxConfig) -> None:
        super().__init__()
        self._config = config
        self._base_url = f"http://{config.host}:{config.port}"
        self._history: list[dict[str, str]] = []
        self._editing_msg: Message | None = None
        self._skip_until: int | None = None
        self._summary_in_progress: bool = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield ScrollableContainer(id="messages", can_focus=False)
        with Vertical(id="tag-row"):
            yield Input(
                placeholder="Tags (e.g. langchain, memory, architecture) or Enter to skip\u2026",
                id="tag-input",
            )
        with Vertical(id="input-row"):
            yield Input(placeholder="Type a message and press Enter…", id="input")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#input", Input).focus()
        self._add_message("system", f"Connected to {self._base_url}")
        self.run_worker(self._restore_session(), exclusive=False, thread=False)

    def _add_message(
        self, role: str, content: str = "", history_index: int | None = None
    ) -> Message:
        msg = Message(role, content, history_index=history_index)
        if role == "assistant" and content:
            msg.render_markdown()
        container = self.query_one("#messages", ScrollableContainer)
        container.mount(msg)
        container.scroll_end(animate=False)
        return msg

    async def _restore_session(self) -> None:
        """Load the current session from the server buffer."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._base_url}/v1/buffer/session")
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            self._show_help()
            return
        entries = data.get("entries", [])
        if entries:
            for i, entry in enumerate(entries):
                self._history.append({"role": entry["role"], "content": entry["content"]})
                self._add_message(entry["role"], entry["content"], history_index=i)
            self._add_message(
                "system",
                f"Restored {len(entries)} messages. Use Ctrl+L to clear.",
            )
        else:
            self._show_help()

    def on_key(self, event: events.Key) -> None:
        # VS Code's integrated terminal uses the Kitty keyboard protocol, which
        # sends space as \x1b[32u. Textual parses this as key='space' but sets
        # character=None (sequence length > 1), so Input._on_key skips it as
        # non-printable. Intercept here and insert manually.
        if event.key == "space" and event.character is None:
            if isinstance(self.focused, Input):
                self.focused.insert_text_at_cursor(" ")
                event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "tag-input":
            self._handle_tag_submit(event)
            return
        text = event.value.strip()
        if not text:
            return
        event.input.clear()

        if self._editing_msg is not None:
            self._do_edit(text)
            event.input.placeholder = "Type a message and press Enter\u2026"
            event.input.focus()
            return

        if text.startswith("/"):
            if text == "/help":
                self._show_help()
            elif text == "/context":
                self.run_worker(self._show_context(), exclusive=False, thread=False)
            elif text.startswith("/source"):
                name = text[7:].strip().strip("\"'")
                if name:
                    self.run_worker(self._create_source(name), exclusive=False, thread=False)
                else:
                    self._add_message("system", 'Usage: /source "name"')
            elif text == "/sources":
                self.run_worker(self._list_sources(), exclusive=False, thread=False)
            elif text.startswith("/lookup"):
                arg = text[7:].strip().strip("\"'")
                if arg:
                    self.run_worker(self._lookup_source(arg), exclusive=False, thread=False)
                else:
                    self._add_message("system", 'Usage: /lookup "name" or /lookup <id>')
            elif text == "/summary":
                self._prompt_for_tags()
            elif text == "/summaries":
                self.run_worker(self._list_summaries(), exclusive=False, thread=False)
            elif text.startswith("/search"):
                query = text[7:].strip().strip("\"'")
                if query:
                    self.run_worker(self._search_episodic(query), exclusive=False, thread=False)
                else:
                    self._add_message("system", 'Usage: /search "query"')
            elif text == "/clear":
                self.action_clear()
            elif text.startswith("/interval"):
                arg = text[9:].strip()
                if arg:
                    self.run_worker(self._set_interval(arg), exclusive=False, thread=False)
                else:
                    self.run_worker(self._show_interval(), exclusive=False, thread=False)
            else:
                self._add_message("system", f"Unknown command: {text}")
            event.input.focus()
            return
        self._history.append({"role": "user", "content": text})
        self._add_message("user", text, history_index=len(self._history) - 1)
        assistant_msg = self._add_message("assistant")
        self.run_worker(
            self._stream_response(assistant_msg),
            exclusive=False,
            thread=False,
        )
        event.input.focus()

    def enter_edit_mode(self, msg: Message) -> None:
        """Load a user message into Input for editing."""
        if self._editing_msg is not None:
            return
        self._editing_msg = msg
        inp = self.query_one("#input", Input)
        inp.value = msg._content
        inp.placeholder = "Editing\u2026 Enter: save & regenerate | Escape: cancel"
        inp.focus()

    def action_cancel_edit(self) -> None:
        """Cancel edit mode and return focus to input."""
        if self._editing_msg is not None:
            self._editing_msg = None
            inp = self.query_one("#input", Input)
            inp.clear()
            inp.placeholder = "Type a message and press Enter\u2026"
        self.query_one("#input", Input).focus()

    def _do_edit(self, new_text: str) -> None:
        """Apply an edit to a user message and regenerate."""
        msg = self._editing_msg
        assert msg is not None and msg._history_index is not None
        idx = msg._history_index

        self._history[idx] = {"role": "user", "content": new_text}
        self._history = self._history[: idx + 1]

        msg._content = new_text
        msg.update(new_text)

        container = self.query_one("#messages", ScrollableContainer)
        children = list(container.children)
        try:
            widget_idx = children.index(msg)
        except ValueError:
            self._editing_msg = None
            return
        for child in children[widget_idx + 1 :]:
            child.remove()

        self._editing_msg = None

        assistant_msg = self._add_message("assistant")
        self.run_worker(
            self._stream_response(assistant_msg),
            exclusive=False,
            thread=False,
        )

    def rewind_to(self, msg: Message) -> None:
        """Remove a message and everything after it."""
        if msg._history_index is None:
            return
        self._history = self._history[: msg._history_index]

        container = self.query_one("#messages", ScrollableContainer)
        children = list(container.children)
        try:
            idx = children.index(msg)
        except ValueError:
            return
        for child in children[idx:]:
            child.remove()

        self._add_message("system", "Conversation rewound.")
        if self._editing_msg is not None:
            self.action_cancel_edit()
        else:
            self.query_one("#input", Input).focus()

    def _show_help(self) -> None:
        text = (
            "[b]Commands[/b]\n"
            "  /help                  Show this message\n"
            '  /source "name"         Create a semantic source from the conversation buffer\n'
            "  /sources               List all sources\n"
            '  /lookup "name" | <id>  Show full source content\n'
            "  /context               Show base prompt and recall hits\n"
            "  /summary               Create an episodic summary now\n"
            "  /summaries             List all episodic summaries\n"
            '  /search "query"        Search episodic memory\n'
            "  /clear                 Clear conversation\n"
            "  /interval [words]      Show or set summary word threshold\n"
            "\n"
            "[b]Keybindings[/b]\n"
            "  Ctrl+L                 Clear conversation\n"
            "  Ctrl+P                 Command palette\n"
            "  Tab / Shift+Tab        Focus messages\n"
            "  Enter  (on message)    Edit user message and regenerate\n"
            "  Backspace (on message) Rewind to before that message\n"
            "  Escape                 Cancel edit\n"
            "  Ctrl+C                 Quit"
        )
        self._add_message("system", text)

    async def _create_source(self, name: str) -> None:
        self._add_message("system", f"Creating source: [b]{name}[/b]…")
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/sources",
                    json={"name": name},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError:
            self._add_message("system", "[red]Cannot reach server.[/red]")
            return
        except Exception as exc:
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return
        self._add_message(
            "system",
            f"Source [b]{data['name']}[/b] created "
            f"(buffer {data['start_id']}\u2013{data['end_id']}, {data.get('chunks', '?')} chunks)\n"
            f"[dim]{data['summary']}[/dim]",
        )

    async def _list_sources(self) -> None:
        """List all sources."""
        self._add_message("system", "Fetching sources…")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._base_url}/v1/sources")
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError:
            self._add_message("system", "[red]Cannot reach server.[/red]")
            return
        except Exception as exc:
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return

        sources = data.get("sources", [])
        if not sources:
            self._add_message("system", "[dim]No sources yet.[/dim]")
            return

        text = f"[b]{data['count']} sources:[/b]\n\n"
        for s in sources:
            source_id = s["id"]
            name = s["name"]
            type_ = s["type"]
            summary = s["summary"][:80] + "…" if len(s["summary"]) > 80 else s["summary"]
            text += f"[cyan]{source_id}[/] [b]{name}[/] [dim]({type_})[/]\n"
            text += f"  [dim]{summary}[/dim]\n\n"
        self._add_message("system", text.strip())

    async def _lookup_source(self, arg: str) -> None:
        """Look up a source by ID or name and display its full content."""
        # Try to parse as integer ID first
        try:
            source_id = int(arg)
        except ValueError:
            # Not an ID, try name search
            self._add_message("system", f"Searching for source: [b]{arg}[/b]…")
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(f"{self._base_url}/v1/sources", params={"name": arg})
                    resp.raise_for_status()
                    data = resp.json()
            except httpx.ConnectError:
                self._add_message("system", "[red]Cannot reach server.[/red]")
                return
            except Exception as exc:
                self._add_message("system", f"[red]Error: {exc}[/red]")
                return

            sources = data.get("sources", [])
            if not sources:
                self._add_message("system", f"[red]No source found matching: {arg}[/red]")
                return
            elif len(sources) > 1:
                text = f"[yellow]Multiple matches found ({len(sources)}). Use ID instead:[/]\n\n"
                for s in sources:
                    text += f"[cyan]{s['id']}[/] [b]{s['name']}[/] [dim]({s['type']})[/]\n"
                self._add_message("system", text.strip())
                return
            source_id = sources[0]["id"]

        # Fetch full source content
        self._add_message("system", f"Loading source {source_id}…")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(f"{self._base_url}/v1/sources/{source_id}/content")
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError:
            self._add_message("system", "[red]Cannot reach server.[/red]")
            return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                self._add_message("system", f"[red]Source {source_id} not found.[/red]")
            else:
                self._add_message("system", f"[red]Error: {exc}[/red]")
            return
        except Exception as exc:
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return

        # Display source content
        name = data["source_name"]
        type_ = data["source_type"]
        chunks = data["chunks"]
        total = data["total_chunks"]

        text = f"[b]Source #{source_id}: {name}[/] [dim]({type_}, {total} chunks)[/]\n\n"
        for chunk in chunks:
            content = chunk["content"]
            text += f"{content}\n\n"
        self._add_message("system", text.strip())

    async def _show_context(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._base_url}/v1/debug/context")
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError:
            self._add_message("system", "[red]Cannot reach server.[/red]")
            return
        except Exception as exc:
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return
        base = data.get("base", "")
        memory_ctx = data.get("memory")
        hits = data.get("hits", [])
        text = f"[b]Base prompt:[/b]\n{base}"
        if hits:
            text += "\n\n[b]Recall hits:[/b]"
            for h in hits:
                score = h.get("similarity", 0)
                src = h.get("source_id")
                snippet = h.get("content", "")[:120].replace("\n", " ")
                src_tag = f" [dim](source {src})[/dim]" if src else ""
                bar = "█" * int(score * 10)
                text += f"\n  [{score:.3f}] {bar}{src_tag}\n  [dim]{snippet}[/dim]"
        else:
            text += "\n\n[dim](no recall hits)[/dim]"
        if memory_ctx:
            text += f"\n\n[b]Injected context:[/b]\n[dim]{memory_ctx}[/dim]"
        self._add_message("system", text)

    def _prompt_for_tags(self) -> None:
        """Show the tag input bar for an episodic summary."""
        self._add_message(
            "system",
            "[b][yellow]Time for a summary.[/yellow][/b] "
            "Enter comma-separated tags below, or press Enter with no tags to skip.",
        )
        tag_row = self.query_one("#tag-row")
        tag_row.styles.display = "block"
        self.query_one("#tag-input", Input).focus()

    def _hide_tag_input(self) -> None:
        """Hide the tag input bar and return focus to chat."""
        tag_input = self.query_one("#tag-input", Input)
        tag_input.clear()
        self.query_one("#tag-row").styles.display = "none"
        self.query_one("#input", Input).focus()

    def _handle_tag_submit(self, event: Input.Submitted) -> None:
        """Process submission from the tag input."""
        text = event.value.strip()
        event.input.clear()
        self._hide_tag_input()
        if not text:
            self._add_message("system", "Summary deferred.")
            self._defer_summary()
            return
        tags = [t.strip() for t in text.split(",") if t.strip()]
        self.run_worker(self._create_summary(tags), exclusive=False, thread=False)

    async def _create_summary(self, tags: list[str]) -> None:
        """Call the server to create an episodic summary."""
        self._summary_in_progress = True
        self._add_message("system", "Creating episodic summary\u2026")
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/episodic/summary",
                    json={"tags": tags},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError:
            self._summary_in_progress = False
            self._add_message("system", "[red]Cannot reach server.[/red]")
            return
        except Exception as exc:
            self._summary_in_progress = False
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return
        tag_str = ", ".join(data.get("tags", []))
        entities = data.get("entities", {})
        entity_parts = []
        for cat, items in entities.items():
            if items:
                entity_parts.append(f"{cat}: {', '.join(items)}")
        entity_str = "; ".join(entity_parts) if entity_parts else "none"
        self._add_message(
            "system",
            f"[b]Summary created[/b] "
            f"(buffer {data['start_buffer_id']}\u2013{data['end_buffer_id']})\n"
            f"[dim]Tags: {tag_str or 'none'}[/dim]\n"
            f"[dim]Entities: {entity_str}[/dim]\n\n"
            f"{data['content']}",
        )
        self._summary_in_progress = False

    def _defer_summary(self) -> None:
        """Record skip: don't re-prompt until interval more words."""
        wc = sum(len(m["content"].split()) for m in self._history)
        self._skip_until = wc + (self._config.summary_interval or 500)

    async def _set_interval(self, arg: str) -> None:
        """Set the summary word-count interval on the server."""
        try:
            val = int(arg)
        except ValueError:
            self._add_message("system", "[red]Usage: /interval <number>[/red]")
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/episodic/config",
                    json={"interval_words": val},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return
        self._add_message(
            "system", f"Summary interval set to [b]{data['interval_words']}[/b] words."
        )

    async def _show_interval(self) -> None:
        """Show the current summary word-count interval."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._base_url}/v1/episodic/status")
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return
        self._add_message("system", f"Summary interval: [b]{data['interval_words']}[/b] words.")

    async def _check_summary_due(self) -> None:
        """Check if a summary is due and prompt the user if so."""
        if self._summary_in_progress:
            return
        if self.query_one("#tag-row").styles.display != "none":
            return
        if self._skip_until is not None:
            wc = sum(len(m["content"].split()) for m in self._history)
            if wc < self._skip_until:
                return
            self._skip_until = None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._base_url}/v1/episodic/status")
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return
        if data.get("summary_due"):
            self._prompt_for_tags()

    async def _list_summaries(self) -> None:
        """List all episodic summaries."""
        self._add_message("system", "Fetching summaries\u2026")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._base_url}/v1/episodic/summaries")
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError:
            self._add_message("system", "[red]Cannot reach server.[/red]")
            return
        except Exception as exc:
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return

        summaries = data.get("summaries", [])
        if not summaries:
            self._add_message("system", "[dim]No summaries yet.[/dim]")
            return

        text = f"[b]{data['count']} episodic summaries:[/b]\n"
        for s in summaries:
            sid = s["id"]
            buf_range = f"{s['start_buffer_id']}\u2013{s['end_buffer_id']}"
            tags = ", ".join(s.get("tags", []))
            entities = s.get("entities", {})
            if isinstance(entities, str):
                try:
                    entities = json.loads(entities)
                except (json.JSONDecodeError, TypeError):
                    entities = {}
            entity_parts = []
            for cat, items in entities.items():
                if items:
                    entity_parts.append(f"{cat}: {', '.join(items)}")
            entity_str = "; ".join(entity_parts) if entity_parts else "none"
            snippet = s["content"][:120].replace("\n", " ")

            text += f"\n[cyan]#{sid}[/] [dim](buffer {buf_range})[/dim]\n"
            if tags:
                text += f"  [dim]tags: {tags}[/dim]\n"
            text += f"  [dim]entities: {entity_str}[/dim]\n"
            text += f"  {snippet}\u2026\n"
        self._add_message("system", text.strip())

    async def _search_episodic(self, query: str) -> None:
        """Search episodic memory and display results."""
        self._add_message("system", f"Searching episodic memory: [b]{query}[/b]\u2026")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/episodic/search",
                    json={"query": query},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError:
            self._add_message("system", "[red]Cannot reach server.[/red]")
            return
        except Exception as exc:
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return

        results = data.get("results", [])
        if not results:
            self._add_message("system", "[dim]No episodic hits.[/dim]")
            return

        text = f"[b]{len(results)} episodic hits:[/b]\n"
        for r in results:
            if r["type"] == "summary":
                score = r.get("similarity", 0)
                bar = "\u2588" * int(score * 10)
                tags = ", ".join(r.get("tags", []))
                buf_range = f"{r['start_buffer_id']}\u2013{r['end_buffer_id']}"
                text += (
                    f"\n[cyan]summary[/] [{score:.3f}] {bar} " f"[dim](buffer {buf_range})[/dim]\n"
                )
                if tags:
                    text += f"  [dim]tags: {tags}[/dim]\n"
                text += f"  {r['content'][:200]}\n"
            else:
                rank = r.get("rank", 0)
                role = r.get("role", "?")
                buf_id = r.get("buffer_id", "?")
                snippet = r["content"][:150].replace("\n", " ")
                text += (
                    f"\n[yellow]transcript[/] [rank {rank:.3f}] "
                    f"[dim]({role}, buffer #{buf_id})[/dim]\n"
                    f"  {snippet}\n"
                )
        self._add_message("system", text.strip())

    async def _stream_response(self, msg: Message) -> None:
        payload = {
            "messages": list(self._history),
            "stream": True,
        }
        accumulated = ""
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/v1/chat/completions",
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            break
                        try:
                            chunk = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if "error" in chunk:
                            msg.append(f"\n[red]{chunk['error']['message']}[/red]")
                            break
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        token = delta.get("content", "")
                        if token:
                            accumulated += token
                            msg.append(token)
                            self.query_one("#messages", ScrollableContainer).scroll_end(
                                animate=False
                            )
        except httpx.ConnectError:
            msg.update("[red]Cannot reach server. Is `nixx serve` running?[/red]")
            return
        except Exception as exc:
            msg.update(f"[red]Error: {exc}[/red]")
            return

        if accumulated:
            self._history.append({"role": "assistant", "content": accumulated})
            msg._history_index = len(self._history) - 1
            msg.render_markdown()
            # Check if episodic summary is due
            self.run_worker(self._check_summary_due(), exclusive=False, thread=False)

    def action_clear(self) -> None:
        self._history.clear()
        self._editing_msg = None
        self.run_worker(self._clear_session(), exclusive=False, thread=False)
        container = self.query_one("#messages", ScrollableContainer)
        container.remove_children()
        self._add_message("system", "Conversation cleared.")

    async def _clear_session(self) -> None:
        """Write a session marker to the server buffer."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(f"{self._base_url}/v1/buffer/clear")
        except Exception:
            pass
