"""
AlphaAbsolute — NotebookLM MCP Server
Wraps notebooklm-py as an MCP server for Claude Code.

Setup:
  pip install "notebooklm-py[browser]" mcp
  python -m notebooklm login

Add to Claude Code MCP config (.claude/settings.json):
  {
    "mcpServers": {
      "notebooklm": {
        "command": "python",
        "args": ["C:/Users/Pizza/OneDrive/Desktop/AlphaAbsolute/scripts/notebooklm_mcp.py"]
      }
    }
  }
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

try:
    import mcp.server.stdio
    import mcp.types as types
    from mcp.server import Server
    from mcp.server.models import InitializationOptions
except ImportError:
    print("ERROR: mcp package not found. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

try:
    from notebooklm import NotebookLMClient
except ImportError:
    print("ERROR: notebooklm package not found. Run: pip install 'notebooklm-py[browser]'", file=sys.stderr)
    sys.exit(1)

# ── Constants ──────────────────────────────────────────────────────────────────

NOTEBOOKS = {
    "PULSE framework": "AlphaAbsolute — PULSE framework",
    "megatrend themes": "AlphaAbsolute — Megatrend Themes",
    "investment lessons": "AlphaAbsolute — Investment Lessons",
    "thai market intelligence": "AlphaAbsolute — Thai Market Intelligence",
    "global macro database": "AlphaAbsolute — Global Macro Database",
}

INDEX_FILE = Path("C:/Users/Pizza/OneDrive/Desktop/AlphaAbsolute/memory/notebooklm_index.md")

# ── Helpers ───────────────────────────────────────────────────────────────────

def resolve_notebook_title(name: str) -> str:
    """Map short name or partial match to full notebook title."""
    name_lower = name.lower().strip()
    # Exact short-name match
    if name_lower in NOTEBOOKS:
        return NOTEBOOKS[name_lower]
    # Substring match against short keys
    for short, full in NOTEBOOKS.items():
        if short in name_lower or name_lower in short:
            return full
    # Return as-is (caller provided full title)
    return name


def update_index(notebook: str, label: str, summary: str) -> None:
    """Append a new entry to the local NotebookLM index file."""
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"| {date} | {notebook} | {label} | {summary[:80]} |\n"
    if not INDEX_FILE.exists():
        INDEX_FILE.write_text(
            "# NotebookLM Source Index\n\n"
            "| Date | Notebook | Label | Summary |\n"
            "|------|----------|-------|---------|\n",
            encoding="utf-8",
        )
    with INDEX_FILE.open("a", encoding="utf-8") as f:
        f.write(entry)


async def find_notebook(client: NotebookLMClient, title: str):
    """Return the first notebook whose title contains `title` (case-insensitive)."""
    notebooks = await client.notebooks.list()
    title_lower = title.lower()
    return next((n for n in notebooks if title_lower in n.title.lower()), None)


# ── MCP Server ────────────────────────────────────────────────────────────────

server = Server("notebooklm-alphaabsolute")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="notebooklm_query",
            description=(
                "Ask a question to a specific AlphaAbsolute NotebookLM notebook and get a "
                "grounded answer with source citations. Notebooks: PULSE framework, Megatrend Themes, "
                "Investment Lessons, Thai Market Intelligence, Global Macro Database."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "notebook": {
                        "type": "string",
                        "description": "Notebook name (e.g. 'PULSE framework', 'Investment Lessons')",
                    },
                    "question": {
                        "type": "string",
                        "description": "Question to ask the notebook",
                    },
                },
                "required": ["notebook", "question"],
            },
        ),
        types.Tool(
            name="notebooklm_add_source",
            description=(
                "Add a new source (research note, lesson, report) to a specific NotebookLM notebook. "
                "Label format: 'YYMMDD | Agent | Type | Topic'"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "notebook": {
                        "type": "string",
                        "description": "Target notebook name",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content to add as a source",
                    },
                    "label": {
                        "type": "string",
                        "description": "Source label: 'YYMMDD | Agent | Type | Topic'",
                    },
                },
                "required": ["notebook", "content", "label"],
            },
        ),
        types.Tool(
            name="notebooklm_list_sources",
            description="List all sources currently in a NotebookLM notebook.",
            inputSchema={
                "type": "object",
                "properties": {
                    "notebook": {
                        "type": "string",
                        "description": "Notebook name to inspect",
                    },
                },
                "required": ["notebook"],
            },
        ),
        types.Tool(
            name="notebooklm_list_notebooks",
            description="List all AlphaAbsolute NotebookLM notebooks and their source counts.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        async with await NotebookLMClient.from_storage() as client:
            notebook_title = resolve_notebook_title(arguments.get("notebook", ""))

            if name == "notebooklm_query":
                question = arguments["question"]
                target = await find_notebook(client, notebook_title)
                if not target:
                    notebooks = await client.notebooks.list()
                    titles = [n.title for n in notebooks]
                    return [types.TextContent(
                        type="text",
                        text=f"Notebook '{notebook_title}' not found.\nAvailable: {titles}",
                    )]
                result = await client.chat.ask(target.id, question)
                answer = result.answer if hasattr(result, "answer") else str(result)
                return [types.TextContent(
                    type="text",
                    text=f"**Notebook:** {target.title}\n**Q:** {question}\n\n**A:** {answer}",
                )]

            elif name == "notebooklm_add_source":
                content = arguments["content"]
                label = arguments["label"]
                target = await find_notebook(client, notebook_title)
                if not target:
                    # Create the notebook if it doesn't exist
                    target = await client.notebooks.create(title=notebook_title)
                await client.sources.add_text(target.id, title=label, content=content)
                update_index(notebook_title, label, content[:100])
                return [types.TextContent(
                    type="text",
                    text=f"✅ Source added to '{notebook_title}'\nLabel: {label}\nIndex updated: memory/notebooklm_index.md",
                )]

            elif name == "notebooklm_list_sources":
                target = await find_notebook(client, notebook_title)
                if not target:
                    return [types.TextContent(
                        type="text",
                        text=f"Notebook '{notebook_title}' not found.",
                    )]
                sources = await client.sources.list(target.id)
                if not sources:
                    return [types.TextContent(
                        type="text",
                        text=f"**{target.title}** — no sources yet.",
                    )]
                lines = [f"**Sources in '{target.title}' ({len(sources)} total):**"]
                for s in sources:
                    created = getattr(s, "created_at", None)
                    date_str = created.strftime("%Y-%m-%d") if created else "unknown"
                    lines.append(f"- {s.title} (added: {date_str})")
                return [types.TextContent(type="text", text="\n".join(lines))]

            elif name == "notebooklm_list_notebooks":
                notebooks = await client.notebooks.list()
                if not notebooks:
                    return [types.TextContent(type="text", text="No notebooks found.")]
                lines = ["**AlphaAbsolute NotebookLM Notebooks:**"]
                for nb in notebooks:
                    lines.append(f"- {nb.title} (id: {nb.id})")
                return [types.TextContent(type="text", text="\n".join(lines))]

            else:
                return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        error_msg = f"Error in {name}: {str(e)}\n{traceback.format_exc()}"
        return [types.TextContent(type="text", text=error_msg)]


async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="notebooklm-alphaabsolute",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=None,
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

