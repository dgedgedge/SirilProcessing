"""Contrôles du site MkDocs et des exports, sans navigateur ni dépendances Siril."""

import base64
import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "bin/build_documentation.py"
spec = importlib.util.spec_from_file_location("documentation_builder", SCRIPT)
builder = importlib.util.module_from_spec(spec)
with patch("sys.path", [str(SCRIPT.parent), *sys.path]):
    spec.loader.exec_module(builder)


class DocumentationSiteTests(unittest.TestCase):
    """Vérifie les liens autonomes et la protection des sources."""

    def test_metadata_links_survive_github_pages_archiving(self) -> None:
        """Les références .github ne dépendent pas d’un dossier exclu par Pages."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".github").mkdir()
            (root / ".github/instructions.md").write_text("# Consignes\n")
            (root / ".copilot").mkdir()
            (root / ".copilot/config.json").write_text("{}")
            (root / "AGENTS.md").write_text(
                "[Consignes](.github/instructions.md)\n"
                "[Configuration](.copilot/config.json)\n"
                "[Source](https://github.com/example/.github/file.md)\n"
            )
            builder.DocumentationBuilder._stage_metadata(root)
            self.assertFalse((root / ".github").exists())
            self.assertTrue((root / "github_meta/instructions.md").is_file())
            self.assertTrue((root / "copilot_meta/config.json").is_file())
            result = (root / "AGENTS.md").read_text()
            self.assertIn("(github_meta/instructions.md)", result)
            self.assertIn("(copilot_meta/config.json)", result)
            self.assertIn("https://github.com/example/.github/file.md", result)

    def test_chapter_links_and_duplicate_ids_are_namespaced(self) -> None:
        """Les mêmes titres dans deux chapitres gardent des ancres distinctes."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "README.md", root / "docs/README.md"
            chapters = {first: "chapter-0", second: "chapter-1"}
            parser = builder.SingleFileLinks(root, first, chapters)
            parser.feed(
                '<h2 id="intro">Titre</h2><a href="#intro">Ici</a>'
                '<a href="docs/README.html#intro">Autre</a>'
            )
            result = "".join(parser.parts)
            self.assertIn('id="chapter-0-intro"', result)
            self.assertIn('href="#chapter-0-intro"', result)
            self.assertIn('href="#chapter-1-intro"', result)

    def test_sources_are_embedded_but_models_and_external_files_are_not(self) -> None:
        """Le monofichier inclut ses références textuelles et exclut les poids."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "README.md"
            source = root / "example.py"
            source.write_text("VALUE = 1\n")
            (root / "weights.onnx").write_bytes(b"model")
            parser = builder.SingleFileLinks(root, document, {document: "chapter-0"})
            parser.feed(
                '<a href="example.py">Code</a><a href="weights.onnx">Poids</a>'
                '<a href="https://example.org/guide">Externe</a>'
            )
            result = "".join(parser.parts)
            self.assertIn(base64.b64encode(source.read_bytes()).decode("ascii"), result)
            self.assertIn('href="weights.onnx"', result)
            self.assertIn('download="example.py"', result)
            self.assertIn('href="https://example.org/guide"', result)
            self.assertNotIn(base64.b64encode(b"model").decode("ascii"), result)

    def test_output_cannot_replace_sources_or_build_cache(self) -> None:
        """Une configuration dangereuse est refusée avant toute suppression."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (".", "docs", "docs/generated", "lib", "tmp/documentation"):
                with self.subTest(output=relative):
                    with self.assertRaises(ValueError):
                        builder.DocumentationBuilder(root, root / relative)
            self.assertEqual(
                builder.DocumentationBuilder(root, root / "out").output, root / "out"
            )

    def test_generated_directories_do_not_become_documentation_sources(self) -> None:
        """Les caches et sites précédents ne polluent pas la génération suivante."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                "README.md",
                "docs/guide.md",
                "tmp/build/copy.md",
                "out/copy.md",
                "node_modules/copy.md",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# Document\n")
            self.assertEqual(
                builder.renderer.discover_markdown_files(root, root / "out"),
                [Path("README.md"), Path("docs/guide.md")],
            )


if __name__ == "__main__":
    unittest.main()
