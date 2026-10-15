#!/usr/bin/env python3
"""Construit MkDocs et ses exports autonomes HTML/PDF, sans Siril."""

from __future__ import annotations

import argparse
import base64
import html
import json
from html.parser import HTMLParser
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

import generate_docs_html as renderer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MERMAID_VERSION = "10.9.5"
MERMAID_URL = (
    f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/dist/mermaid.min.js"
)


class SingleFileLinks(HTMLParser):
    """Réécrit liens/ancres et embarque les sources et images dans un chapitre."""

    def __init__(self, root: Path, document: Path, chapters: dict[Path, str]) -> None:
        """Configure la racine autorisée et les identifiants uniques des chapitres."""
        super().__init__(convert_charrefs=False)
        self.root, self.document, self.chapters = root, document, chapters
        self.prefix = chapters[document]
        self.parts: list[str] = []

    def _target(self, value: str) -> str:
        """Convertit un lien local vers un chapitre ou une ressource embarquée."""
        link = urlsplit(html.unescape(value))
        if link.scheme or link.netloc:
            return value
        path = (
            (self.document.parent / unquote(link.path)).resolve()
            if link.path
            else self.document
        )
        if path.suffix == ".html":
            path = path.with_suffix(".md")
        if path in self.chapters:
            suffix = f"-{unquote(link.fragment)}" if link.fragment else ""
            return f"#{self.chapters[path]}{suffix}"
        allowed = renderer.DOCUMENT_SOURCE_EXTENSIONS | {
            ".png",
            ".jpg",
            ".jpeg",
            ".svg",
            ".gif",
        }
        if path.is_relative_to(self.root) and path.suffix in allowed and path.is_file():
            media = mimetypes.guess_type(path.name)[0] or "text/plain"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:{media};base64,{encoded}"
        return value

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Conserve les éléments HTML en rendant leurs références autonomes."""
        attributes = []
        for name, value in attrs:
            if value is None:
                attributes.append(name)
                continue
            if name == "id":
                value = f"{self.prefix}-{value}"
            elif name in {"href", "src"}:
                value = self._target(value)
            attributes.append(f'{name}="{html.escape(value, quote=True)}"')
        suffix = " " + " ".join(attributes) if attributes else ""
        if tag == "a":
            target = dict(attrs).get("href")
            if target and self._target(target).startswith("data:"):
                filename = Path(unquote(urlsplit(target).path)).name
                suffix += f' download="{html.escape(filename, quote=True)}"'
        self.parts.append(f"<{tag}{suffix}>")

    def handle_endtag(self, tag: str) -> None:
        """Conserve les fermetures des éléments HTML."""
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        """Conserve texte et styles déjà échappés par le moteur Markdown."""
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        """Conserve une entité HTML nommée sans double échappement."""
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        """Conserve une entité HTML numérique."""
        self.parts.append(f"&#{name};")


class DocumentationBuilder:
    """Produit le site et deux exports à partir des mêmes Markdown du dépôt."""

    def __init__(self, source_root: Path, output_dir: Path, pdf: bool = True) -> None:
        """Configure les chemins ; les caches techniques restent dans tmp/."""
        self.root = source_root.resolve()
        self.output = output_dir.resolve()
        self.pdf = pdf
        if self.output == self.root or self.root.is_relative_to(self.output):
            raise ValueError("Le dossier de sortie ne doit pas contenir les sources")
        self.cache = self.root / "tmp/documentation"
        protected = [
            self.root / name
            for name in ("docs", "bin", "lib", "tests", ".git", "imagesExample")
        ]
        if any(
            self.output.is_relative_to(path) or path.is_relative_to(self.output)
            for path in protected
        ):
            raise ValueError("Le dossier de sortie ne doit pas remplacer les sources")
        if self.cache.is_relative_to(self.output) or self.output.is_relative_to(
            self.cache
        ):
            raise ValueError("Le dossier de sortie doit être distinct du cache")

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Déclare les sorties et le mode HTML seul, explicitement sans PDF."""
        parser.add_argument("--source-root", type=Path, default=PROJECT_ROOT)
        parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "out")
        parser.add_argument(
            "--no-pdf",
            action="store_true",
            help="Construire le site et le HTML autonome sans PDF",
        )

    def _mermaid(self) -> Path:
        """Met en cache la version JS épinglée ; un téléchargement échoué est bloquant."""
        path = self.cache / f"mermaid-{MERMAID_VERSION}.min.js"
        if not path.is_file():
            with urlopen(MERMAID_URL, timeout=60) as response:
                data = response.read()
            temporary = path.with_suffix(".part")
            temporary.write_bytes(data)
            temporary.replace(path)
        return path

    def _exports(
        self, sources: list[Path], staging: Path, work: Path, mermaid: Path
    ) -> None:
        """Assemble les chapitres, rend Mermaid en SVG et imprime le même HTML en PDF."""
        from playwright.sync_api import sync_playwright

        chapters = {
            (self.root / rel).resolve(): f"chapter-{index}"
            for index, rel in enumerate(sources)
        }
        renderer.write_assets(work)
        css = (work / "assets/style.css").read_text()
        css += "\n.pdf-section {break-before: page;} pre {white-space: pre-wrap;} svg {max-width:100%;height:auto;} @page {size:A4; margin:15mm;}"
        parts = [
            f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>SirilProcessing</title><style>{css}</style></head><body><main class="page"><article><h1>SirilProcessing</h1><nav><ul>'
        ]
        bodies = []
        for rel in sources:
            path = (self.root / rel).resolve()
            markdown = path.read_text(encoding="utf-8")
            title = renderer.title_from_markdown(markdown, rel)
            parts.append(
                f'<li><a href="#{chapters[path]}">{html.escape(title)}</a></li>'
            )
            body, _ = renderer.render_markdown_document(markdown, rel)
            links = SingleFileLinks(self.root, path, chapters)
            links.feed(body)
            bodies.append(
                f'<section class="pdf-section" id="{chapters[path]}">{"".join(links.parts)}</section>'
            )
        parts.extend(["</ul></nav>", *bodies, "</article></main></body></html>"])
        source = work / "combined.html"
        source.write_text("".join(parts), encoding="utf-8")
        downloads = staging / "downloads"
        downloads.mkdir()
        os.environ.setdefault(
            "PLAYWRIGHT_BROWSERS_PATH", str(self.root / "tmp/docs-browsers")
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(source.as_uri())
                page.add_script_tag(path=str(mermaid))
                page.evaluate(
                    "async () => {mermaid.initialize({startOnLoad:false}); await mermaid.run({querySelector:'.mermaid'});}"
                )
                page.evaluate(
                    "() => document.querySelectorAll('script').forEach(node => node.remove())"
                )
                (downloads / "documentation.html").write_text(
                    page.content(), encoding="utf-8"
                )
                if self.pdf:
                    page.pdf(
                        path=str(downloads / "documentation.pdf"),
                        format="A4",
                        print_background=True,
                        tagged=True,
                        outline=True,
                    )
            finally:
                browser.close()

    def build(self) -> None:
        """Construit les exports puis MkDocs ; toute erreur empêche une publication CI."""
        self.cache.mkdir(parents=True, exist_ok=True)
        sources = renderer.discover_markdown_files(self.root, self.output)
        sources.sort(
            key=lambda rel: (
                (
                    0
                    if rel == Path("README.md")
                    else (
                        1
                        if rel == Path("docs/README.md")
                        else 2 if rel.parts[0] == "docs" else 3
                    )
                ),
                str(rel),
            )
        )
        with TemporaryDirectory(prefix="build-", dir=self.cache) as directory:
            work = Path(directory)
            staging = work / "source"
            staging.mkdir()
            for rel in sources:
                destination = staging / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                markdown = (self.root / rel).read_text(encoding="utf-8")
                destination.write_text(markdown, encoding="utf-8")
                body, _ = renderer.render_markdown_document(markdown, rel)
                renderer.copy_referenced_sources(self.root, staging, rel, body)
            self._stage_metadata(staging)
            mermaid = self._mermaid()
            self._exports(sources, staging, work, mermaid)
            home = staging / "README.md"
            links = "\n\n## Télécharger la documentation\n\n- [HTML autonome](downloads/documentation.html)\n"
            if self.pdf:
                links += "- [Documentation PDF](downloads/documentation.pdf)\n"
            home.write_text(home.read_text(encoding="utf-8") + links, encoding="utf-8")
            assets = staging / "assets"
            assets.mkdir(exist_ok=True)
            shutil.copyfile(mermaid, assets / "mermaid.min.js")
            (assets / "documentation.js").write_text(
                "document.addEventListener('DOMContentLoaded', () => {mermaid.initialize({startOnLoad:true});});\n"
            )
            config = work / "mkdocs.yml"
            config.write_text(
                f"INHERIT: {json.dumps(str(self.root / 'mkdocs.yml'))}\ndocs_dir: {json.dumps(str(staging))}\nsite_dir: {json.dumps(str(self.output))}\n",
                encoding="utf-8",
            )
            # MkDocs préserve certains fichiers cachés des constructions précédentes.
            if self.output.exists():
                shutil.rmtree(self.output)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mkdocs",
                    "build",
                    "--strict",
                    "-f",
                    str(config),
                ],
                check=True,
            )
        print(f"Site MkDocs : {self.output / 'index.html'}")
        print(f"HTML autonome : {self.output / 'downloads/documentation.html'}")
        if self.pdf:
            print(f"PDF : {self.output / 'downloads/documentation.pdf'}")

    @staticmethod
    def _stage_metadata(staging: Path) -> None:
        """Expose les références cachées sous des noms acceptés par l’archive Pages.

        upload-pages-artifact exclut .github. Les guides et JSON liés gardent
        leur contenu, mais leurs chemins temporaires deviennent github_meta et
        copilot_meta. Les Markdown du dépôt ne sont pas modifiés.
        """
        for name in ("github", "copilot"):
            source = staging / f".{name}"
            if source.exists():
                source.rename(staging / f"{name}_meta")
        for path in staging.rglob("*.md"):
            text = path.read_text(encoding="utf-8")

            def link(match: re.Match[str]) -> str:
                """Adapte seulement les cibles locales des liens Markdown."""
                target = match.group(1)
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc:
                    return match.group(0)
                target = target.replace(".github/", "github_meta/")
                target = target.replace(".copilot/", "copilot_meta/")
                return f"]({target})"

            text = re.sub(r"\]\(([^)\n]+)\)", link, text)
            if path.is_relative_to(staging / "github_meta"):
                text = "---\nedit_url: null\n---\n" + text
            path.write_text(text, encoding="utf-8")


def main() -> int:
    """Résout les arguments puis construit tous les formats demandés."""
    parser = argparse.ArgumentParser(description=__doc__)
    DocumentationBuilder.add_arguments(parser)
    args = parser.parse_args()
    DocumentationBuilder(args.source_root, args.output_dir, not args.no_pdf).build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
