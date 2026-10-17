"""Self-contained HTML report with Mermaid diagrams (renders offline except CDN)."""

from __future__ import annotations

import html
import json
from typing import Any

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
  :root {{ --fg:#1f2430; --muted:#6b7280; --accent:#2563eb; --bg:#f8fafc; --card:#ffffff; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        color:var(--fg); background:var(--bg); line-height:1.55; }}
  header {{ background:var(--card); border-bottom:1px solid #e5e7eb; padding:24px 32px; }}
  header h1 {{ margin:0 0 4px; font-size:22px; }}
  header .meta {{ color:var(--muted); font-size:13px; }}
  main {{ max-width:1080px; margin:0 auto; padding:24px 32px 64px; }}
  section {{ background:var(--card); border:1px solid #e5e7eb; border-radius:12px;
            padding:20px 24px; margin:16px 0; }}
  h2 {{ font-size:17px; margin:0 0 12px; }}
  h3 {{ font-size:14px; margin:18px 0 8px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }}
  code, pre {{ font-family:SFMono-Regular, Consolas, "Liberation Mono", monospace; font-size:12.5px; }}
  pre {{ background:#0f172a; color:#e2e8f0; padding:14px 16px; border-radius:8px; overflow-x:auto; }}
  pre.mermaid {{ background:var(--card); color:var(--fg); border:1px solid #e5e7eb; text-align:center; }}
  table {{ border-collapse:collapse; width:100%; font-size:13.5px; }}
  th, td {{ text-align:left; padding:6px 10px; border-bottom:1px solid #eef1f5; vertical-align:top; }}
  th {{ color:var(--muted); font-weight:600; }}
  a {{ color:var(--accent); text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .pill {{ display:inline-block; background:#eef2ff; color:#3730a3; border-radius:999px;
          padding:2px 10px; font-size:12px; margin:0 6px 6px 0; }}
  .muted {{ color:var(--muted); font-size:13px; }}
  .warn {{ border-left:4px solid #f59e0b; background:#fffbeb; padding:10px 16px; border-radius:8px; }}
  dt {{ font-weight:600; margin-top:8px; }}
  dd {{ margin:2px 0 8px; }}
  ol.flow li, td ul, dl {{ margin:0; }}
  ol.flow {{ margin:4px 0 14px; padding-left:22px; }}
  td ul {{ padding-left:16px; margin:0 0 8px; }}
  td ul li {{ margin:1px 0; }}
  details {{ margin:6px 0; }}
  details summary {{ cursor:pointer; color:var(--accent); font-size:13.5px; }}
  details[open] summary {{ margin-bottom:8px; }}
  details[open] > table {{ margin-bottom:10px; }}
  .warn ul {{ margin:6px 0 0 18px; padding-left:10px; }}
  footer {{ color:var(--muted); font-size:12px; text-align:center; padding:20px; }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <div class="meta">{subtitle}</div>
</header>
<main>
{body}
</main>
<script>
  mermaid.initialize({{ startOnLoad:true, theme:"neutral", flowchart:{{ useMaxWidth:true }} }});
</script>
</body>
</html>
"""


def esc(text: Any) -> str:
    """HTML-escape any value for safe embedding."""
    return html.escape(str(text), quote=True)


def section(title: str, body: str) -> str:
    """One report section."""
    return f"<section><h2>{esc(title)}</h2>\n{body}\n</section>"


def html_table(headers: list[str], rows: list[list[Any]]) -> str:
    """Render a table (values escaped)."""
    if not rows:
        return '<p class="muted">none</p>'
    head = "".join(f"<th>{esc(cell)}</th>" for cell in headers)
    body_rows = "".join(
        "<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f"<table><tr>{head}</tr>{body_rows}</table>"


def mermaid_diagram(code: str) -> str:
    """Mermaid block; mermaid.js replaces content on load."""
    return f'<pre class="mermaid">\n{code}\n</pre>'


def render_html_report(title: str, subtitle: str, sections_html: list[str]) -> str:
    """Assemble the full HTML document."""
    body = "\n".join(sections_html)
    return _TEMPLATE.format(title=esc(title), subtitle=esc(subtitle), body=body)


def data_footnote(payload: dict[str, Any]) -> str:
    """Small JSON footnote section (optional for debugging)."""
    pretty = json.dumps(payload, indent=2, ensure_ascii=False)
    return section("Raw data", f"<details><summary>expanded data</summary><pre>{esc(pretty)}</pre></details>")


__all__ = ["data_footnote", "esc", "html_table", "mermaid_diagram", "render_html_report", "section"]
