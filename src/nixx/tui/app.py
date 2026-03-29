"""Nixx chat TUI."""

import json

import httpx
from rich.markdown import Markdown as RichMarkdown
from rich.markup import escape as escape_markup
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.message import Message as TextualMessage
from textual.widgets import Footer, Header, Static, Switch, TextArea

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
        Binding("y", "yank", "Copy"),
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
        self.update(escape_markup(self._content))

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

    def action_yank(self) -> None:
        self.app.copy_to_clipboard(self._content)
        self.app.notify("Copied to clipboard", timeout=2)


class ContextBar(Static):
    """Visual indicator of context window fill level."""

    DEFAULT_CSS = """
    ContextBar {
        height: auto;
        max-height: 1;
        width: auto;
        padding: 0 1 0 0;
        color: $text-muted;
    }
    """

    def __init__(self, id: str | None = None) -> None:
        super().__init__("", id=id)

    def on_mount(self) -> None:
        self.styles.display = "none"

    def set_usage(self, prompt_tokens: int, context_length: int) -> None:
        if context_length <= 0:
            return
        pct = min(prompt_tokens / context_length, 1.0)
        filled = int(pct * 20)
        bar = "\u2588" * filled + "\u2591" * (20 - filled)
        if pct < 0.5:
            color = "green"
        elif pct < 0.8:
            color = "yellow"
        else:
            color = "red"
        self.update(
            f"[dim]context[/dim] [{color}]{bar}[/] {pct:.0%}"
            f" [dim]({prompt_tokens:,} / {context_length:,} tokens)[/dim]"
        )
        self.styles.display = "block"

    def clear_usage(self) -> None:
        self.styles.display = "none"


class SummaryBar(Static):
    """Visual indicator of progress toward next episodic summary."""

    DEFAULT_CSS = """
    SummaryBar {
        height: auto;
        max-height: 1;
        width: auto;
        padding: 0 1 0 0;
        color: $text-muted;
    }
    """

    def __init__(self, id: str | None = None) -> None:
        super().__init__("", id=id)

    def on_mount(self) -> None:
        self.styles.display = "none"

    def set_progress(self, current_words: int, interval_words: int) -> None:
        if interval_words <= 0:
            return
        if current_words <= 0:
            self.styles.display = "none"
            return
        pct = min(current_words / interval_words, 1.0)
        filled = int(pct * 20)
        bar = "\u2588" * filled + "\u2591" * (20 - filled)
        if pct < 0.5:
            color = "dim"
        elif pct < 0.9:
            color = "cyan"
        else:
            color = "magenta"
        self.update(
            f"[dim]summary[/dim] [{color}]{bar}[/] {pct:.0%}"
            f" [dim]({current_words:,} / {interval_words:,} words)[/dim]"
        )
        self.styles.display = "block"

    def clear_progress(self) -> None:
        self.styles.display = "none"


class ChatInput(TextArea):
    """Multi-line chat input. Enter sends the message, Shift+Enter inserts a newline."""

    BINDINGS = [
        Binding("enter", "submit", "Send", priority=True),
        Binding("shift+enter", "newline", "New line"),
    ]

    class Submitted(TextualMessage):
        """Posted when the user submits input."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def action_submit(self) -> None:
        self.post_message(self.Submitted(self.text))

    def action_newline(self) -> None:
        self.insert("\n")


class IntentBar(Static):
    """Displays the current intent."""

    def set_intent(self, intent: str | None) -> None:
        if intent:
            # Truncate to single line - avoid wrapping
            truncated = intent.replace("\n", " ")
            if len(truncated) > 120:
                truncated = truncated[:117] + "..."
            self.update(f"intent: {truncated}")
        else:
            self.update("intent: -")


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
    ChatInput {
        width: 1fr;
        height: auto;
        min-height: 3;
        max-height: 10;
    }
    #intent-bar {
        display: block;
        height: 1;
        color: $text-muted;
    }
    #status-area {
        height: auto;
        padding: 0 2;
    }
    #context-row, #summary-row {
        height: 1;
    }
    #toggles-row {
        height: 1;
    }
    #recall-label, #intent-label {
        width: auto;
        padding: 0 1 0 0;
    }
    #recall-switch, #intent-switch {
        width: auto;
        height: auto;
        min-height: 1;
        padding: 0;
        margin-right: 2;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear", "Clear"),
        ("ctrl+r", "toggle_recall", "Recall"),
        ("ctrl+i", "toggle_intent", "Intent"),
        Binding("escape", "cancel_edit", "Cancel", show=False),
    ]

    def __init__(self, config: NixxConfig) -> None:
        super().__init__()
        self._config = config
        self._base_url = f"http://{config.host}:{config.port}"
        self._history: list[dict[str, str]] = []
        self._editing_msg: Message | None = None
        self._summary_in_progress: bool = False
        self._intent_bar_visible: bool = True

    def compose(self) -> ComposeResult:
        yield Header()
        yield ScrollableContainer(id="messages", can_focus=False)
        with Vertical(id="status-area"):
            with Horizontal(id="context-row"):
                yield ContextBar(id="context-bar")
            with Horizontal(id="summary-row"):
                yield SummaryBar(id="summary-bar")
            yield IntentBar(id="intent-bar")
            with Horizontal(id="toggles-row"):
                yield Static("recall", id="recall-label")
                yield Switch(value=True, id="recall-switch")
                yield Static("intent", id="intent-label")
                yield Switch(value=True, id="intent-switch")
        with Vertical(id="input-row"):
            yield ChatInput(id="input")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#input", ChatInput).focus()
        self._add_message("system", f"Connected to {self._base_url}")
        self.run_worker(self._restore_session(), exclusive=False, thread=False)
        self.run_worker(self._update_summary_bar(), exclusive=False, thread=False)
        self.run_worker(self._fetch_and_show_intent_bar(), exclusive=False, thread=False)

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
            # Update summary bar and trigger a summary if the threshold was
            # already exceeded when the session ended.
            await self._update_summary_bar()
            await self._check_summary_due()
        else:
            self._show_help()

    def on_key(self, event: events.Key) -> None:
        # VS Code's integrated terminal uses the Kitty keyboard protocol, which
        # sends space as \x1b[32u. Textual parses this as key='space' but sets
        # character=None (sequence length > 1), so Input._on_key skips it as
        # non-printable. Intercept here and insert manually.
        if event.key == "space" and event.character is None:
            focused = self.focused
            if isinstance(focused, ChatInput):
                focused.insert(" ")
                event.stop()

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        text = event.text.strip()
        if not text:
            return
        chat_input = self.query_one("#input", ChatInput)
        chat_input.load_text("")

        if self._editing_msg is not None:
            self._do_edit(text)
            chat_input.focus()
            return

        if text.startswith("/"):
            if text == "/help":
                self._show_help()
            elif text == "/context":
                self.run_worker(self._show_context(), exclusive=False, thread=False)
            elif text == "/summary":
                self.run_worker(self._create_summary(), exclusive=False, thread=False)
            elif text.startswith("/search"):
                query = text[7:].strip().strip("\"'")
                if query:
                    self.run_worker(self._search_episodic(query), exclusive=False, thread=False)
                else:
                    self._add_message("system", 'Usage: /search "query"')
            elif text.startswith("/transcript"):
                args = text[11:].strip().split()
                if args:
                    start_id = args[0]
                    end_id = args[1] if len(args) > 1 else None
                    self.run_worker(
                        self._view_transcript(start_id, end_id), exclusive=False, thread=False
                    )
                else:
                    self._add_message("system", "Usage: /transcript <id> [end_id]")
            elif text == "/clear":
                self.action_clear()
            elif text.startswith("/interval"):
                arg = text[9:].strip()
                if arg:
                    self.run_worker(self._set_interval(arg), exclusive=False, thread=False)
                else:
                    self.run_worker(self._show_interval(), exclusive=False, thread=False)
            elif text == "/recall":
                self.action_toggle_recall()
            elif text == "/intent-toggle":
                self.action_toggle_intent()
            elif text.startswith("/threshold"):
                arg = text[10:].strip()
                if arg:
                    self.run_worker(self._set_threshold(arg), exclusive=False, thread=False)
                else:
                    self.run_worker(self._show_threshold(), exclusive=False, thread=False)
            elif text == "/intent-bar":
                self._toggle_intent_bar()
            elif text.startswith("/intent"):
                arg = text[7:].strip()
                if arg:
                    self.run_worker(self._set_intent(arg), exclusive=False, thread=False)
                else:
                    self.run_worker(self._show_intent(), exclusive=False, thread=False)
            elif text.startswith("/grant"):
                arg = text[6:].strip()
                if arg:
                    self.run_worker(self._grant_dir(arg), exclusive=False, thread=False)
                else:
                    self.run_worker(self._list_dirs(), exclusive=False, thread=False)
            elif text.startswith("/revoke"):
                arg = text[7:].strip()
                if arg:
                    self.run_worker(self._revoke_dir(arg), exclusive=False, thread=False)
                else:
                    self._add_message("system", "Usage: /revoke <directory>")
            else:
                self._add_message("system", f"Unknown command: {text}")
            chat_input.focus()
            return
        self._history.append({"role": "user", "content": text})
        self._add_message("user", text, history_index=len(self._history) - 1)
        assistant_msg = self._add_message("assistant")
        self.run_worker(
            self._stream_response(assistant_msg),
            exclusive=False,
            thread=False,
        )
        chat_input.focus()

    def enter_edit_mode(self, msg: Message) -> None:
        """Load a user message into ChatInput for editing."""
        if self._editing_msg is not None:
            return
        self._editing_msg = msg
        inp = self.query_one("#input", ChatInput)
        inp.load_text(msg._content)
        inp.move_cursor(inp.document.end)
        inp.focus()

    def action_cancel_edit(self) -> None:
        """Cancel edit mode and return focus to input."""
        if self._editing_msg is not None:
            self._editing_msg = None
            inp = self.query_one("#input", ChatInput)
            inp.load_text("")
        self.query_one("#input", ChatInput).focus()

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
            self.query_one("#input", ChatInput).focus()

    def _show_help(self) -> None:
        text = (
            "[b]Commands[/b]\n"
            "  /help                   Show this message\n"
            "  /context                Show base prompt and recall hits\n"
            "  /summary                Create an episodic summary now\n"
            '  /search "query"         Search transcript (keyword)\n'
            "  /transcript <id> \\[end]  View transcript messages\n"
            "  /clear                  Clear conversation\n"
            "  /recall                 Toggle episodic recall on/off\n"
            "  /intent-toggle          Toggle intent injection on/off\n"
            "  /interval \\[words]       Show or set summary word threshold\n"
            "  /threshold \\[0.0-1.0]    Show or set recall similarity threshold\n"
            "  /intent \\[text]          Show or set current intent\n"
            "  /intent-bar             Toggle intent bar visibility\n"
            "  /grant \\[dir]            List or grant directory access\n"
            "  /revoke <dir>           Revoke directory access\n"
            "\n"
            "[b]Keybindings[/b]\n"
            "  Ctrl+L                  Clear conversation\n"
            "  Ctrl+R                  Toggle episodic recall\n"
            "  Ctrl+I                  Toggle intent injection\n"
            "  Ctrl+P                  Command palette\n"
            "  Shift+Enter             New line in input\n"
            "  Tab / Shift+Tab         Focus messages\n"
            "  Enter (on message)      Edit user message and regenerate\n"
            "  Backspace (on message)  Rewind to before that message\n"
            "  y (on message)          Copy message to clipboard\n"
            "  Ctrl+Shift+V            Paste from OS clipboard (terminal-dependent)\n"
            "  Escape                  Cancel edit\n"
            "  Ctrl+C                  Quit"
        )
        self._add_message("system", text)

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

    async def _create_summary(self) -> None:
        """Call the server to create an episodic summary."""
        self._summary_in_progress = True
        self._add_message("system", "Creating episodic summary\u2026")
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/episodic/summary",
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
        self.run_worker(self._update_summary_bar(), exclusive=False, thread=False)

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
        self.run_worker(self._update_summary_bar(), exclusive=False, thread=False)

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

    async def _set_intent(self, intent: str) -> None:
        """Set the current intent/motivation."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/intent",
                    json={"intent": intent},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return
        self._add_message("system", f"Intent set: [b]{data['intent']}[/b]")
        self._refresh_intent_bar(data.get("intent"))

    async def _show_intent(self) -> None:
        """Show the current intent/motivation."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._base_url}/v1/intent")
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return
        intent = data.get("intent")
        msg_count = data.get("messages_since_derivation", 0)
        if intent:
            self._add_message(
                "system",
                f"Current intent: [b]{intent}[/b]\n"
                f"[dim]({msg_count} messages since last derivation)[/dim]",
            )
        else:
            self._add_message("system", "[dim]No intent set[/dim]")
        self._refresh_intent_bar(intent)

    async def _grant_dir(self, directory: str) -> None:
        """Grant nixx access to a directory."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/permissions/grant",
                    json={"directory": directory},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.json().get("detail", str(exc))
            self._add_message("system", f"[red]{detail}[/red]")
            return
        except Exception as exc:
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return
        self._add_message(
            "system",
            f"Granted: [b]{data['granted']}[/b]\n"
            f"[dim]Allowed dirs: {', '.join(data['allowed_dirs']) or 'none'}[/dim]",
        )

    async def _revoke_dir(self, directory: str) -> None:
        """Revoke nixx's access to a directory."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/permissions/revoke",
                    json={"directory": directory},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return
        self._add_message(
            "system",
            f"Revoked: [b]{data['revoked']}[/b]\n"
            f"[dim]Allowed dirs: {', '.join(data['allowed_dirs']) or 'none'}[/dim]",
        )

    async def _list_dirs(self) -> None:
        """List allowed directories."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._base_url}/v1/permissions/dirs")
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return
        scratch = data.get("scratch_dir", "?")
        dirs = data.get("allowed_dirs", [])
        text = f"[b]Scratch dir:[/b] {scratch}"
        if dirs:
            text += "\n[b]Granted dirs:[/b]\n" + "\n".join(f"  {d}" for d in dirs)
        else:
            text += "\n[dim]No additional directories granted[/dim]"
        self._add_message("system", text)

    async def _update_context_bar(self) -> None:
        """Fetch token usage from the server and update the context gauge."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base_url}/v1/debug/context")
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return
        usage = data.get("token_usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        context_length = usage.get("context_length", 0)
        if context_length > 0:
            self.query_one(ContextBar).set_usage(prompt_tokens, context_length)

    def _refresh_intent_bar(self, intent: str | None) -> None:
        """Update the intent bar with the current intent."""
        bar = self.query_one("#intent-bar", IntentBar)
        if self._intent_bar_visible:
            bar.styles.display = "block"
            bar.set_intent(intent)

    def _toggle_intent_bar(self) -> None:
        """Show or hide the intent bar."""
        self._intent_bar_visible = not self._intent_bar_visible
        bar = self.query_one("#intent-bar", IntentBar)
        if self._intent_bar_visible:
            self.run_worker(self._fetch_and_show_intent_bar(), exclusive=False, thread=False)
        else:
            bar.styles.display = "none"
        state = "shown" if self._intent_bar_visible else "hidden"
        self._add_message("system", f"Intent bar {state}.")

    async def _fetch_and_show_intent_bar(self) -> None:
        """Fetch current intent from server and show in bar."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base_url}/v1/intent")
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return
        self._refresh_intent_bar(data.get("intent"))

    async def _set_threshold(self, arg: str) -> None:
        """Set the recall similarity threshold on the server."""
        try:
            val = float(arg)
            if not 0.0 <= val <= 1.0:
                raise ValueError
        except ValueError:
            self._add_message("system", "[red]Usage: /threshold <0.0\u20131.0>[/red]")
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/episodic/config",
                    json={"recall_threshold": val},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return
        self._add_message(
            "system", f"Recall threshold set to [b]{data['recall_threshold']:.2f}[/b]."
        )

    async def _show_threshold(self) -> None:
        """Show the current recall similarity threshold."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._base_url}/v1/episodic/status")
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return
        self._add_message(
            "system", f"Recall threshold: [b]{data.get('recall_threshold', 0.4):.2f}[/b]"
        )

    async def _update_summary_bar(self) -> None:
        """Fetch summary progress from the server and update the gauge."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base_url}/v1/episodic/status")
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return
        current = data.get("current_words", 0)
        interval = data.get("interval_words", 500)
        self.query_one(SummaryBar).set_progress(current, interval)
        # Sync switch states with server truth
        recall_on = data.get("recall_enabled", True)
        intent_on = data.get("intent_enabled", True)
        recall_switch = self.query_one("#recall-switch", Switch)
        if recall_switch.value != recall_on:
            recall_switch.value = recall_on
        intent_switch = self.query_one("#intent-switch", Switch)
        if intent_switch.value != intent_on:
            intent_switch.value = intent_on
        self._update_toggle_labels(recall_on, intent_on)

    async def _check_summary_due(self) -> None:
        """Check if a summary is due and trigger one automatically if so."""
        if self._summary_in_progress:
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._base_url}/v1/episodic/status")
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return
        if data.get("summary_due"):
            self.run_worker(self._create_summary(), exclusive=False, thread=False)

    async def _search_episodic(self, query: str) -> None:
        """Search episodic memory and display results."""
        self._add_message("system", f"Searching transcript: [b]{query}[/b]\u2026")
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
            self._add_message("system", "[dim]No transcript hits.[/dim]")
            return

        text = f"[b]{len(results)} transcript hits:[/b]\n"
        for r in results:
            rank = r.get("rank", 0)
            role = r.get("role", "?")
            buf_id = r.get("buffer_id", "?")
            snippet = r["content"][:150].replace("\n", " ")
            text += (
                f"\n[cyan]#{buf_id}[/] [{role}] " f"[dim](rank {rank:.3f})[/dim]\n" f"  {snippet}\n"
            )
        text += "\n[dim]Use /transcript <id> to view context[/dim]"
        self._add_message("system", text.strip())

    async def _view_transcript(self, start_id: str, end_id: str | None = None) -> None:
        """Fetch and display a range of transcript messages."""
        try:
            sid = int(start_id)
        except ValueError:
            self._add_message("system", "[red]Invalid start ID[/red]")
            return
        eid = None
        if end_id is not None:
            try:
                eid = int(end_id)
            except ValueError:
                self._add_message("system", "[red]Invalid end ID[/red]")
                return

        params: dict[str, int] = {"start_id": sid}
        if eid is not None:
            params["end_id"] = eid

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{self._base_url}/v1/episodic/transcript",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError:
            self._add_message("system", "[red]Cannot reach server.[/red]")
            return
        except Exception as exc:
            self._add_message("system", f"[red]Error: {exc}[/red]")
            return

        entries = data.get("entries", [])
        if not entries:
            self._add_message("system", "[dim]No transcript entries found.[/dim]")
            return

        text = f"[b]Transcript #{sid}"
        if eid is not None:
            text += f"\u2013{eid}"
        text += f" ({len(entries)} messages):[/b]\n"
        for e in entries:
            role = e.get("role", "?")
            content = e.get("content", "")
            buf_id = e.get("id", "?")
            # Color by role
            if role == "user":
                role_tag = "[green]user[/]"
            elif role == "assistant":
                role_tag = "[yellow]assistant[/]"
            else:
                role_tag = f"[dim]{role}[/]"
            text += f"\n[dim]#{buf_id}[/] {role_tag}\n{content}\n"
        self._add_message("system", text.strip())

    async def _stream_response(self, msg: Message) -> None:
        payload = {
            "messages": list(self._history),
            "stream": True,
        }
        accumulated = ""
        try:
            # Long read timeout: nixx-server won't send the first chunk until after LLM prefill,
            # which can take several minutes for large prompts. Short connect/write are fine.
            _stream_timeout = httpx.Timeout(connect=10.0, read=660.0, write=30.0, pool=5.0)
            async with httpx.AsyncClient(timeout=_stream_timeout) as client:
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
                            err = chunk["error"]
                            err_msg = (
                                (err.get("message") or err.get("type") or repr(err))
                                if isinstance(err, dict)
                                else str(err)
                            )
                            msg.append(f"\n[red]Error: {escape_markup(err_msg)}[/red]")
                            break
                        if "tool_call" in chunk:
                            tool_name = chunk["tool_call"].get("name", "?")
                            msg.append(f"\n[dim]▸ {escape_markup(tool_name)}[/dim]\n")
                            self.query_one("#messages", ScrollableContainer).scroll_end(
                                animate=False
                            )
                            continue
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        token = delta.get("content", "")
                        if token:
                            accumulated += token
                            msg.append(escape_markup(token))
                            self.query_one("#messages", ScrollableContainer).scroll_end(
                                animate=False
                            )
        except httpx.ConnectError:
            msg.update("[red]Cannot reach server. Is `nixx serve` running?[/red]")
            return
        except Exception as exc:
            detail = str(exc) or f"({type(exc).__name__})"
            msg.update(f"[red]Error: {escape_markup(detail)}[/red]")
            return

        if accumulated:
            self._history.append({"role": "assistant", "content": accumulated})
            msg._history_index = len(self._history) - 1
            msg.render_markdown()
            # Check if episodic summary is due
            self.run_worker(self._check_summary_due(), exclusive=False, thread=False)
            self.run_worker(self._update_context_bar(), exclusive=False, thread=False)
            self.run_worker(self._update_summary_bar(), exclusive=False, thread=False)
            self.run_worker(self._fetch_and_show_intent_bar(), exclusive=False, thread=False)

    def action_clear(self) -> None:
        self._history.clear()
        self._editing_msg = None
        self.run_worker(self._clear_session(), exclusive=False, thread=False)
        container = self.query_one("#messages", ScrollableContainer)
        container.remove_children()
        self._add_message("system", "Conversation cleared.")
        self.query_one(ContextBar).clear_usage()
        self.query_one(SummaryBar).clear_progress()

    def action_toggle_recall(self) -> None:
        """Toggle episodic recall on/off via the server."""
        switch = self.query_one("#recall-switch", Switch)
        switch.toggle()

    def action_toggle_intent(self) -> None:
        """Toggle intent injection on/off via the server."""
        switch = self.query_one("#intent-switch", Switch)
        switch.toggle()

    def _update_toggle_labels(self, recall_on: bool, intent_on: bool) -> None:
        recall_markup = "[green]recall[/green]" if recall_on else "[red]recall[/red]"
        intent_markup = "[green]intent[/green]" if intent_on else "[red]intent[/red]"
        self.query_one("#recall-label", Static).update(recall_markup)
        self.query_one("#intent-label", Static).update(intent_markup)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        recall_on = self.query_one("#recall-switch", Switch).value
        intent_on = self.query_one("#intent-switch", Switch).value
        self._update_toggle_labels(recall_on, intent_on)
        if event.switch.id == "recall-switch":
            self.run_worker(self._set_recall(event.value), exclusive=False, thread=False)
        elif event.switch.id == "intent-switch":
            self.run_worker(self._set_intent_enabled(event.value), exclusive=False, thread=False)

    async def _set_recall(self, enabled: bool) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/episodic/config",
                    json={"recall_enabled": enabled},
                )
                resp.raise_for_status()
        except Exception as exc:
            self._add_message("system", f"[red]Error toggling recall: {exc}[/red]")
            return
        state = "[green]on[/green]" if enabled else "[red]off[/red]"
        self._add_message("system", f"Episodic recall: {state}")

    async def _set_intent_enabled(self, enabled: bool) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/episodic/config",
                    json={"intent_enabled": enabled},
                )
                resp.raise_for_status()
        except Exception as exc:
            self._add_message("system", f"[red]Error toggling intent: {exc}[/red]")
            return
        state = "[green]on[/green]" if enabled else "[red]off[/red]"
        self._add_message("system", f"Intent injection: {state}")

    async def _clear_session(self) -> None:
        """Write a session marker to the server buffer."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(f"{self._base_url}/v1/buffer/clear")
        except Exception:
            pass
