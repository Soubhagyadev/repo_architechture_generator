"""Interactive multi-turn grounded Q&A session over one analyzed repository."""

from __future__ import annotations

import rich.console
from rich.markdown import Markdown
from rich.panel import Panel

from repo_analyzer.analyzer import AnalyzedRepository
from repo_analyzer.config import AppConfig
from repo_analyzer.features import qa as qa_feature

_EXITS = {"exit", "quit", "q"}


def _link(result: AnalyzedRepository, path: str, line: int | None) -> str:
    """Terminal hyperlink to the file (falls back to plain text)."""
    try:
        href = result.link_for(path, line)
    except Exception:
        href = None
    label = f"{path}:{line}" if line else path
    if href:
        return f"[link={href}]{label}[/link]"
    return label


def _print_answer(result: AnalyzedRepository, answer: qa_feature.Answer) -> None:
    """Render one Answer with citations and confidence."""
    rich_console = rich.console.Console()
    body = answer.answer
    cites = [
        f"{_link(result, cit.file, cit.line)}" + (f" ({cit.note})" if cit.note else "")
        for cit in answer.citations
    ]
    if cites:
        body += "\n\n**citations:** " + " · ".join(cites)
    rich_console.print(Panel(
        Markdown(body),
        title=f"answer ({answer.confidence}, {'llm' if answer.used_llm else 'deterministic'})",
        border_style="cyan" if answer.used_llm else "yellow",
    ))


def run_chat(
    result: AnalyzedRepository,
    config: AppConfig,
    llm,  # type: LLMService | None
) -> None:
    """Run the interactive session; 'exit', empty line, or Ctrl+C ends it."""
    rich_console = rich.console.Console()
    rich_console.print(
        f"[bold]repo-chat[/bold] · {result.full_name} @ {result.ref} · "
        "type a question; 'exit' or empty line to leave"
    )
    history: list[str] = []
    while True:
        try:
            question = rich_console.input("[bold cyan]you › [/bold cyan]").strip()
        except (KeyboardInterrupt, EOFError):
            rich_console.print("[dim]bye[/dim]")
            return
        if not question or question.lower() in _EXITS:
            rich_console.print("[dim]bye[/dim]")
            return
        extended = question
        if history:
            extended = f"{question}\n(previous topic: {history[-1][:160]})"
        answer = qa_feature.ask(result, extended, config, llm)
        _print_answer(result, answer)
        history.append(question)


__all__ = ["run_chat"]
