"""Nixx chat TUI."""

import json

import httpx
from textual import events
from textual.app import App, ComposeResult
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
    """

    def __init__(self, role: str, content: str = "") -> None:
        super().__init__(content, classes=role)
        self._role = role
        self._content = content

    def append(self, text: str) -> None:
        self._content += text
        self.update(self._content)


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
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear", "Clear"),
    ]

    def __init__(self, config: NixxConfig) -> None:
        super().__init__()
        self._config = config
        self._base_url = f"http://{config.host}:{config.port}"
        self._history: list[dict[str, str]] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield ScrollableContainer(id="messages", can_focus=False)
        with Vertical(id="input-row"):
            yield Input(placeholder="Type a message and press Enter…", id="input")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self._add_message("system", f"Connected to {self._base_url}")

    def _add_message(self, role: str, content: str = "") -> Message:
        msg = Message(role, content)
        container = self.query_one("#messages", ScrollableContainer)
        container.mount(msg)
        container.scroll_end(animate=False)
        return msg

    def on_key(self, event: events.Key) -> None:
        # VS Code's integrated terminal uses the Kitty keyboard protocol, which
        # sends space as \x1b[32u. Textual parses this as key='space' but sets
        # character=None (sequence length > 1), so Input._on_key skips it as
        # non-printable. Intercept here and insert manually.
        if event.key == "space" and event.character is None:
            inp = self.query_one("#input", Input)
            if self.focused is inp:
                inp.insert_text_at_cursor(" ")
                event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.clear()
        if text.startswith("/"):
            if text == "/context":
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
            else:
                self._add_message("system", f"Unknown command: {text}")
            event.input.focus()
            return
        self._history.append({"role": "user", "content": text})
        self._add_message("user", text)
        assistant_msg = self._add_message("assistant")
        self.run_worker(
            self._stream_response(assistant_msg),
            exclusive=False,
            thread=False,
        )
        event.input.focus()

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

    def action_clear(self) -> None:
        self._history.clear()
        container = self.query_one("#messages", ScrollableContainer)
        container.remove_children()
        self._add_message("system", "Conversation cleared.")
