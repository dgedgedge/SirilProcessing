"""Vérifications HTML exécutables sans les dépendances astronomiques."""
import importlib.util
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'generate_docs_html', Path(__file__).resolve().parents[1] / 'bin/generate_docs_html.py'
)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class HeadingParser(HTMLParser):
    """Collecte les titres h1 et leurs ancres pour vérifier les exports réels."""

    def __init__(self) -> None:
        """Initialise la liste des attributs des titres rencontrés."""
        super().__init__()
        self.headings: list[dict[str, str | None]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        """Collecte uniquement les vraies balises de titre principal."""
        if tag == 'h1':
            self.headings.append(dict(attrs))


class DocumentationHTMLTests(unittest.TestCase):
    def test_single_page_keeps_original_titles_and_anchors_once(self) -> None:
        """Le monofichier garde un seul h1 par document avec les deux moteurs."""
        for markdown_module in (generator.markdown_lib, None):
            with self.subTest(markdown=markdown_module), TemporaryDirectory() as directory:
                with patch.object(generator, 'markdown_lib', markdown_module):
                    output = self.build(Path(directory), {
                        'README.md': '# Projet\n',
                        '.github/copilot-instructions.md': (
                            '# Instructions de travail — SirilProcessing\n\n'
                            '## Consignes\n\nUn paragraphe.\n'
                        ),
                    })
                single = (output / 'all_docs.html').read_text()
                headings = HeadingParser()
                headings.feed(single)
                self.assertEqual(len(headings.headings), 3)  # titre global + deux sources
                self.assertEqual(
                    sum(item.get('id') == 'instructions-de-travail-sirilprocessing'
                        for item in headings.headings), 1,
                )
                self.assertIn('Source: .github/copilot-instructions.md', single)
                self.assertIn('Un paragraphe.', single)
                individual = HeadingParser()
                individual.feed((output / '.github/copilot-instructions.html').read_text())
                self.assertEqual(len(individual.headings), 1)

    def test_single_page_adds_fallback_only_without_real_h1(self) -> None:
        """Les h2, commentaires et faux titres de code ne remplacent pas le h1."""
        for body in (
            '<p>Sans titre</p>',
            '<h2 id="section">Section</h2>',
            '<!-- <h1>Commentaire</h1> --><p>Texte</p>',
            '<pre><code>&lt;h1&gt;Exemple&lt;/h1&gt;</code></pre>',
        ):
            with self.subTest(body=body):
                page = generator.build_single_page(
                    'Documentation', [(Path('guide.md'), 'Guide <test>', body)],
                )
                headings = HeadingParser()
                headings.feed(page)
                self.assertEqual(len(headings.headings), 2)
                self.assertIn('<h1>Guide &lt;test&gt;</h1>', page)
                self.assertIn(body, page)

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

    def test_toc_ignores_comments_in_code_blocks(self) -> None:
        """Les commentaires shell ne créent pas d’ancres inexistantes au sommaire."""
        document = '# Guide\n\n```bash\n# Faux titre\necho ok\n```\n\n## Section\n'
        for markdown_module in (generator.markdown_lib, None):
            with self.subTest(markdown=markdown_module):
                with patch.object(generator, 'markdown_lib', markdown_module):
                    body, toc = generator.render_markdown_document(document, Path('guide.md'))
                self.assertEqual([title for _, title, _ in toc], ['Guide', 'Section'])
                for _, _, anchor in toc:
                    self.assertIn(f'id="{anchor}"', body)

    def test_linked_project_sources_are_available_offline(self) -> None:
        """L’export copie les fichiers référencés et conserve les chemins relatifs."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = self.build(root, {
                'README.md': '[Module](lib/model%20manager.py)\n',
                'lib/model manager.py': 'MODEL_VERSION = "3.0.2"\n',
                '.copilot/config.json': '{"instructions": []}\n',
                'bin/run.sh': '#!/bin/sh\nexit 0\n',
                'docs/guide.md': (
                    '[Configuration](../.copilot/config.json)\n'
                    '[Lanceur](../bin/run.sh)\n'
                    '[Module](../lib/model%20manager.py#L1)\n'
                ),
            })
            for relative in (
                'lib/model manager.py', '.copilot/config.json', 'bin/run.sh',
            ):
                self.assertEqual(
                    (output / relative).read_bytes(),
                    (root / 'source' / relative).read_bytes(),
                )
            self.assertIn(
                'href="../bin/run.sh"', (output / 'docs/guide.html').read_text(),
            )

    def test_export_excludes_weights_and_files_outside_project(self) -> None:
        """Les poids, liens externes et cibles hors dépôt ne sont pas embarqués."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            outside = root / 'outside.py'
            outside.write_text('PRIVATE = True\n')
            (source / 'outside-link.py').symlink_to(outside)
            (source / 'model.onnx').write_bytes(b'weights')
            (source / 'model.pth').write_bytes(b'weights')
            output = self.build(root, {
                'README.md': (
                    '[Hors dépôt](../outside.py)\n'
                    '[Alias hors dépôt](outside-link.py)\n'
                    f'[Absolu]({outside})\n'
                    '[Distant](https://example.invalid/remote.py)\n'
                    '[Poids ONNX](model.onnx)\n'
                    '[Poids PyTorch](model.pth)\n'
                ),
            })
            self.assertFalse((output / 'outside-link.py').exists())
            self.assertFalse((output / 'model.onnx').exists())
            self.assertFalse((output / 'model.pth').exists())
            self.assertFalse((output / 'remote.py').exists())
            self.assertEqual(outside.read_text(), 'PRIVATE = True\n')


if __name__ == '__main__':
    unittest.main()
