#!/usr/bin/env python3
"""Generate a browsable HTML documentation tree from repository Markdown files."""

import argparse
import html
import os
import re
import shutil
import sys
from pathlib import Path


def add_project_venv_to_path() -> None:
    """Make project .venv packages available when the script is launched directly."""
    repo_root = Path(__file__).resolve().parents[1]
    venv_lib = repo_root / ".venv" / "lib"
    if not venv_lib.exists():
        return
    for site_packages in sorted(venv_lib.glob("python*/site-packages"), reverse=True):
        sys.path.insert(0, str(site_packages))
        return


add_project_venv_to_path()

try:
    import markdown as markdown_lib
except Exception:
    markdown_lib = None

try:
    from pygments.formatters import HtmlFormatter
except Exception:
    HtmlFormatter = None


DEFAULT_OUTPUT_DIR = "generated_doc"
MARKDOWN_EXTENSIONS = {".md", ".markdown"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate static HTML documentation from Markdown files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--source-root",
        default=".",
        help="Repository/source root to scan for Markdown files.",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where the generated HTML tree will be written.",
    )
    parser.add_argument(
        "--title",
        default="SirilProcessing Documentation",
        help="Title used for the generated HTML index.",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="Also generate a single PDF if WeasyPrint is installed.",
    )
    parser.add_argument(
        "--pdf-output",
        default="documentation.pdf",
        help="PDF filename inside the output directory when --pdf is used.",
    )
    return parser.parse_args()


def slugify(text: str, sep: str = "-") -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"[^A-Za-z0-9_-]+", sep, text.strip().lower())
    return text.strip(sep) or "section"


def markdown_path_to_html(path: Path) -> Path:
    return path.with_suffix(".html")


def escape_text(text: str) -> str:
    return html.escape(text, quote=False)


def render_inline(text: str, current_rel: Path) -> str:
    """Render a small subset of inline Markdown."""
    placeholders: list[str] = []

    def stash(value: str) -> str:
        placeholders.append(value)
        return f"\x00{len(placeholders) - 1}\x00"

    def render_link(match: re.Match[str]) -> str:
        label = render_inline(match.group(1), current_rel)
        target = match.group(2).strip()
        href = target
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) and not target.startswith("#"):
            if target.endswith(tuple(MARKDOWN_EXTENSIONS)):
                href = str(markdown_path_to_html(Path(target)))
            elif ".md#" in target:
                md_path, anchor = target.split("#", 1)
                href = f"{markdown_path_to_html(Path(md_path))}#{anchor}"
        return stash(f'<a href="{html.escape(href, quote=True)}">{label}</a>')

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", render_link, text)
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", lambda m: stash(f"<code>{html.escape(m.group(1))}</code>"), escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)

    for idx, value in enumerate(placeholders):
        escaped = escaped.replace(f"\x00{idx}\x00", value)
    return escaped


def render_markdown(markdown: str, current_rel: Path) -> tuple[str, list[tuple[int, str, str]]]:
    lines = markdown.splitlines()
    out: list[str] = []
    toc: list[tuple[int, str, str]] = []
    paragraph: list[str] = []
    list_open = False
    code_open = False
    code_lang = ""
    table_buffer: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            text = " ".join(line.strip() for line in paragraph)
            out.append(f"<p>{render_inline(text, current_rel)}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            out.append("</ul>")
            list_open = False

    def flush_table() -> None:
        nonlocal table_buffer
        if not table_buffer:
            return
        out.append("<pre class=\"markdown-table\"><code>")
        out.append(html.escape("\n".join(table_buffer)))
        out.append("</code></pre>")
        table_buffer = []

    for raw_line in lines:
        line = raw_line.rstrip()

        fence = re.match(r"^```(\w+)?\s*$", line)
        if fence:
            flush_paragraph()
            close_list()
            flush_table()
            if not code_open:
                code_open = True
                code_lang = (fence.group(1) or "").lower()
                cls = f"language-{html.escape(code_lang)}" if code_lang else ""
                if code_lang == "mermaid":
                    out.append('<pre class="mermaid">')
                else:
                    out.append(f'<pre><code class="{cls}">')
            else:
                if code_lang == "mermaid":
                    out.append("</pre>")
                else:
                    out.append("</code></pre>")
                code_open = False
                code_lang = ""
            continue

        if code_open:
            out.append(html.escape(line))
            continue

        if not line.strip():
            flush_paragraph()
            close_list()
            flush_table()
            continue

        if "|" in line and re.match(r"^\s*\|?.+\|.+\|?\s*$", line):
            flush_paragraph()
            close_list()
            table_buffer.append(line)
            continue
        flush_table()

        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            text = heading.group(2).strip()
            anchor = slugify(text)
            toc.append((level, text, anchor))
            out.append(f'<h{level} id="{anchor}">{render_inline(text, current_rel)}</h{level}>')
            continue

        item = re.match(r"^\s*[-*]\s+(.+)$", line)
        if item:
            flush_paragraph()
            if not list_open:
                out.append("<ul>")
                list_open = True
            out.append(f"<li>{render_inline(item.group(1), current_rel)}</li>")
            continue

        paragraph.append(line)

    flush_paragraph()
    close_list()
    flush_table()
    if code_open:
        out.append("</code></pre>")
    return "\n".join(out), toc


def extract_toc(markdown: str) -> list[tuple[int, str, str]]:
    toc: list[tuple[int, str, str]] = []
    seen: dict[str, int] = {}
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        level = len(match.group(1))
        text = match.group(2).strip()
        anchor = slugify(text)
        if anchor in seen:
            seen[anchor] += 1
            anchor = f"{anchor}_{seen[anchor]}"
        else:
            seen[anchor] = 0
        toc.append((level, text, anchor))
    return toc


def rewrite_markdown_links(rendered: str) -> str:
    """Rewrite links to Markdown files so they target generated HTML files."""
    def repl(match: re.Match[str]) -> str:
        quote = match.group(1)
        href = html.unescape(match.group(2))
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href) or href.startswith("#"):
            return match.group(0)
        if ".md#" in href:
            md_path, anchor = href.split("#", 1)
            href = f"{markdown_path_to_html(Path(md_path))}#{anchor}"
        elif href.endswith(tuple(MARKDOWN_EXTENSIONS)):
            href = str(markdown_path_to_html(Path(href)))
        return f'href={quote}{html.escape(href, quote=True)}{quote}'

    return re.sub(r"href=(['\"])([^'\"]+)\1", repl, rendered)


def convert_mermaid_blocks(rendered: str) -> str:
    """Convert Python-Markdown fenced mermaid blocks to Mermaid containers."""
    pattern = re.compile(
        r'<pre><code class="(?:language-)?mermaid">(?P<body>.*?)</code></pre>',
        re.DOTALL,
    )
    return pattern.sub(lambda m: f'<pre class="mermaid">{m.group("body")}</pre>', rendered)


def preprocess_mermaid_blocks(markdown: str) -> str:
    """Keep Mermaid fenced blocks as raw HTML so code highlighting does not eat them."""
    pattern = re.compile(r"```mermaid\s*\n(?P<body>.*?)\n```", re.DOTALL)
    return pattern.sub(
        lambda m: f'<pre class="mermaid">\n{html.escape(m.group("body"))}\n</pre>',
        markdown,
    )


def render_markdown_document(markdown: str, current_rel: Path) -> tuple[str, list[tuple[int, str, str]]]:
    """Render Markdown using Python-Markdown when available, fallback otherwise."""
    if markdown_lib is None:
        return render_markdown(markdown, current_rel)

    md = markdown_lib.Markdown(
        extensions=[
            "extra",
            "toc",
            "sane_lists",
            "smarty",
            "codehilite",
        ],
        extension_configs={
            "toc": {"permalink": True, "slugify": slugify},
            "codehilite": {"guess_lang": False, "use_pygments": True},
        },
        output_format="html5",
    )
    rendered = md.convert(preprocess_mermaid_blocks(markdown))
    rendered = convert_mermaid_blocks(rendered)
    rendered = rewrite_markdown_links(rendered)
    toc = extract_toc(markdown)
    return rendered, toc


def html_page(title: str, body: str, toc: list[tuple[int, str, str]], rel_to_root: str) -> str:
    toc_html = ""
    if toc:
        links = [
            f'<li class="toc-level-{level}"><a href="#{anchor}">{html.escape(text)}</a></li>'
            for level, text, anchor in toc
            if level <= 3
        ]
        toc_html = f"<nav class=\"toc\"><h2>Sommaire</h2><ul>{''.join(links)}</ul></nav>"

    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="{rel_to_root}assets/style.css">
  <script type="module">
    import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
    mermaid.initialize({{ startOnLoad: true, theme: 'default' }});
  </script>
</head>
<body>
  <header class="site-header">
    <a class="home" href="{rel_to_root}index.html">SirilProcessing Docs</a>
    <span>{html.escape(title)}</span>
  </header>
  <main class="page">
    <article>
      {toc_html}
      {body}
    </article>
  </main>
</body>
</html>
"""


def write_assets(output_dir: Path) -> None:
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    pygments_css = HtmlFormatter(style="friendly").get_style_defs(".codehilite") if HtmlFormatter else ""
    (assets_dir / "style.css").write_text(
        f""":root {{
  --bg: #eef3f8;
  --paper: #ffffff;
  --ink: #17202a;
  --muted: #637083;
  --line: #d7e0ea;
  --accent: #11698e;
  --accent-soft: #e4f3f7;
  --code-bg: #102a43;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  color: var(--ink);
  background:
    radial-gradient(circle at top left, rgba(17, 105, 142, 0.12), transparent 32rem),
    var(--bg);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 16px;
  line-height: 1.65;
}}
.site-header {{
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  background: #102a43;
  color: white;
  padding: 14px 30px;
  box-shadow: 0 8px 28px rgba(16, 42, 67, 0.22);
}}
.site-header span {{ color: #bcccdc; font-size: 0.95rem; }}
.site-header a {{ color: white; text-decoration: none; font-weight: 800; }}
.page {{
  max-width: 1180px;
  margin: 0 auto;
  padding: 32px;
}}
article {{
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: clamp(26px, 4vw, 54px);
  box-shadow: 0 18px 55px rgba(16, 42, 67, 0.12);
}}
h1, h2, h3, h4 {{
  color: #102a43;
  line-height: 1.25;
  letter-spacing: 0;
}}
h1 {{
  margin-top: 0;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
  font-size: clamp(2rem, 4vw, 3rem);
}}
h2 {{ margin-top: 2.1em; }}
h3 {{ margin-top: 1.7em; color: #243b53; }}
a {{ color: var(--accent); text-underline-offset: 3px; }}
p, li {{ max-width: 86ch; }}
blockquote {{
  margin: 1.4rem 0;
  padding: 0.8rem 1.1rem;
  border-left: 4px solid var(--accent);
  background: var(--accent-soft);
  color: #243b53;
}}
code {{
  background: #edf2f7;
  border-radius: 4px;
  padding: 0.1em 0.3em;
}}
pre {{
  overflow-x: auto;
  background: var(--code-bg);
  color: #f0f4f8;
  border-radius: 8px;
  padding: 16px;
  border: 1px solid rgba(16, 42, 67, 0.15);
}}
pre code {{ background: transparent; padding: 0; }}
table {{
  border-collapse: collapse;
  width: 100%;
  margin: 1.2rem 0;
  font-size: 0.95rem;
}}
th, td {{
  border: 1px solid var(--line);
  padding: 9px 11px;
  vertical-align: top;
}}
th {{ background: #f0f4f8; color: #102a43; }}
tr:nth-child(even) td {{ background: #fbfdff; }}
pre.mermaid {{
  background: #ffffff;
  color: var(--ink);
  border: 1px solid var(--line);
}}
.toc {{
  border-left: 4px solid var(--accent);
  background: #f0f4f8;
  padding: 16px 18px;
  margin-bottom: 34px;
  border-radius: 0 8px 8px 0;
}}
.toc h2 {{ margin-top: 0; font-size: 1rem; }}
.toc ul {{ margin: 0; padding-left: 20px; columns: 2; }}
.toc li {{ break-inside: avoid; }}
.toc-level-3 {{ margin-left: 18px; }}
.doc-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 14px;
  padding: 0;
  list-style: none;
}}
.doc-grid li {{
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
  background: #f8fafc;
  transition: transform 120ms ease, box-shadow 120ms ease;
}}
.doc-grid li:hover {{
  transform: translateY(-1px);
  box-shadow: 0 10px 22px rgba(16, 42, 67, 0.10);
}}
small {{ color: var(--muted); }}
.headerlink {{
  color: #9fb3c8;
  margin-left: 0.35rem;
  text-decoration: none;
}}
.codehilite {{
  border-radius: 8px;
  overflow-x: auto;
}}
{pygments_css}
@media (max-width: 760px) {{
  .site-header {{ align-items: flex-start; flex-direction: column; gap: 4px; }}
  .page {{ padding: 16px; }}
  article {{ padding: 22px; }}
  .toc ul {{ columns: 1; }}
}}
@media print {{
  body {{ background: white; }}
  .site-header {{ position: static; box-shadow: none; }}
  .page {{ max-width: none; padding: 0; }}
  article {{ border: 0; box-shadow: none; }}
  a {{ color: #102a43; }}
  pre, blockquote {{ break-inside: avoid; }}
}}
""",
        encoding="utf-8",
    )


def discover_markdown_files(source_root: Path, output_dir: Path) -> list[Path]:
    ignored_parts = {
        ".git",
        ".pytest_cache",
        ".venv",
        "__pycache__",
        "ENV",
        "env",
        "venv",
        output_dir.name,
    }
    files: list[Path] = []
    for path in source_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in MARKDOWN_EXTENSIONS:
            continue
        rel = path.relative_to(source_root)
        if any(part in ignored_parts for part in rel.parts):
            continue
        files.append(rel)
    return sorted(files)


def title_from_markdown(markdown: str, rel: Path) -> str:
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return re.sub(r"`", "", match.group(1).strip())
    return rel.stem.replace("_", " ").replace("-", " ").title()


def build_index(title: str, docs: list[tuple[Path, str]]) -> str:
    items = []
    for rel, doc_title in docs:
        href = markdown_path_to_html(rel)
        items.append(
            f'<li><a href="{html.escape(str(href), quote=True)}">{html.escape(doc_title)}</a>'
            f'<br><small>{html.escape(str(rel))}</small></li>'
        )
    body = f"<h1>{html.escape(title)}</h1>\n<ul class=\"doc-grid\">{''.join(items)}</ul>"
    return html_page(title, body, [], "")


def build_single_page(title: str, docs: list[tuple[Path, str, str]]) -> str:
    sections = [f"<h1>{html.escape(title)}</h1>"]
    for rel, doc_title, body in docs:
        sections.append(
            f'<section class="pdf-section"><h1>{html.escape(doc_title)}</h1>'
            f'<p><small>Source: {html.escape(str(rel))}</small></p>{body}</section>'
        )
    return html_page(title, "\n".join(sections), [], "")


def maybe_write_pdf(output_dir: Path, html_path: Path, pdf_name: str) -> Path | None:
    cache_dir = output_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_dir))

    try:
        from weasyprint import HTML
    except Exception:
        print("PDF skipped: WeasyPrint is not installed. Install it with: pip install WeasyPrint")
        return None

    pdf_path = output_dir / pdf_name
    HTML(filename=str(html_path)).write_pdf(str(pdf_path))
    return pdf_path


def main() -> int:
    args = parse_args()
    source_root = Path(args.source_root).resolve()
    output_dir = Path(args.output_dir).resolve()

    if not source_root.exists():
        raise FileNotFoundError(f"source root does not exist: {source_root}")
    if output_dir == source_root:
        raise ValueError("output-dir cannot be the source root")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    write_assets(output_dir)

    markdown_files = discover_markdown_files(source_root, output_dir)
    generated_docs: list[tuple[Path, str]] = []
    single_page_docs: list[tuple[Path, str, str]] = []

    for rel in markdown_files:
        source_path = source_root / rel
        markdown = source_path.read_text(encoding="utf-8")
        doc_title = title_from_markdown(markdown, rel)
        body, toc = render_markdown_document(markdown, rel)

        output_rel = markdown_path_to_html(rel)
        output_path = output_dir / output_rel
        output_path.parent.mkdir(parents=True, exist_ok=True)
        rel_to_root = "../" * (len(output_rel.parts) - 1)
        output_path.write_text(html_page(doc_title, body, toc, rel_to_root), encoding="utf-8")
        generated_docs.append((output_rel, doc_title))
        single_page_docs.append((rel, doc_title, body))

    (output_dir / "index.html").write_text(build_index(args.title, generated_docs), encoding="utf-8")
    single_page_path = output_dir / "all_docs.html"
    single_page_path.write_text(build_single_page(args.title, single_page_docs), encoding="utf-8")
    print(f"Generated {len(generated_docs)} HTML document(s) in {output_dir}")
    print(f"Open {output_dir / 'index.html'}")
    print(f"Single-page HTML: {single_page_path}")
    if args.pdf:
        pdf_path = maybe_write_pdf(output_dir, single_page_path, args.pdf_output)
        if pdf_path is not None:
            print(f"PDF: {pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
