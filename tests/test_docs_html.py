"""Vérifications HTML exécutables sans les dépendances astronomiques."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'generate_docs_html', Path(__file__).resolve().parents[1] / 'bin/generate_docs_html.py'
)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class DocumentationHTMLTests(unittest.TestCase):
    def build(self, root, pages):
        source = root / 'source'
        for relative, content in pages.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        output = root / 'html'
        with patch('sys.argv', ['generate_docs_html.py', '--source-root', str(source), '--output-dir', str(output)]):
            self.assertEqual(generator.main(), 0)
        return output

    def test_same_home_and_nested_master_pages_as_markdown(self):
        with TemporaryDirectory() as directory:
            output = self.build(Path(directory), {
                'README.md': '# Projet\n\n[Documentation](docs/README.md)\n',
                'docs/README.md': '# Documentation\n\n[Scripts](scripts/README.md)\n',
                'docs/scripts/README.md': '# Scripts\n\n[Guide](sample/README.md)\n',
                'docs/scripts/sample/README.md': '# Guide\n\n[Scripts](../README.md)\n',
            })
            home = (output / 'index.html').read_text()
            self.assertEqual(home, (output / 'README.html').read_text())
            self.assertIn('href="docs/README.html"', home)
            self.assertNotIn('<details', home)
            master = (output / 'docs/README.html').read_text()
            self.assertIn('href="scripts/README.html"', master)
            nested = (output / 'docs/scripts/sample/README.html').read_text()
            self.assertIn('href="../README.html"', nested)
            self.assertIn('href="../../../assets/style.css"', nested)

    def test_other_sources_without_readme_keep_a_generated_index(self):
        with TemporaryDirectory() as directory:
            output = self.build(Path(directory), {'guide.md': '# Guide\n'})
            self.assertIn('href="guide.html"', (output / 'index.html').read_text())

    def test_mermaid_survives_rendering(self):
        body, _ = generator.render_markdown_document(
            '# Parcours\n\n```mermaid\nflowchart TD\n A --> B\n```\n', Path('README.md')
        )
        self.assertIn('<pre class="mermaid">', body)
        self.assertIn('A --&gt; B', body)


if __name__ == '__main__':
    unittest.main()
