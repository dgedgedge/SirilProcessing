"""Contrôles documentaires, avec cadrage optionnel sous Chromium, sans Siril."""

import base64
import importlib.util
import json
import os
import re
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Browser

SCRIPT = Path(__file__).resolve().parents[1] / "bin/build_documentation.py"
spec = importlib.util.spec_from_file_location("documentation_builder", SCRIPT)
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
with patch("sys.path", [str(SCRIPT.parent), *sys.path]):
    spec.loader.exec_module(builder)


class DocumentationSiteTests(unittest.TestCase):
    """Vérifie les liens autonomes et la protection des sources."""

    def _assert_all_diagram_crossings(self, browser: "Browser", script: str) -> None:
        """Contrôle les intersections transverses sur tous les diagrammes sources.

        Parameters
        ----------
        browser : Browser
            Navigateur Chromium utilisé pour calculer les courbes SVG réelles.
        script : str
            Bibliothèque Mermaid adaptée à charger avant chaque rendu.

        Notes
        -----
        Le détecteur échantillonne les courbes ; il ignore les extrémités et
        les trajets colinéaires. Les jonctions légitimes sont ainsi préservées.
        Les cas synthétiques vérifient aussi les transformations des groupes.
        """
        root = SCRIPT.parents[1]
        detector = (root / "tests/fixtures/documentation_crossings.js").read_text()
        page = browser.new_page()
        try:
            for second, transform, expected in (
                ("M0 100 L100 0", "translate(10 0)", [["a", "b"]]),
                ("M0 100 L100 100", "translate(0 0)", []),
                ("M0 10 L100 110", "translate(10 0)", []),
                ("M0 100 L100 0", "translate(200 0)", []),
            ):
                page.set_content(
                    '<svg width="200" height="200">'
                    '<path class="flowchart-link" id="a" d="M0 0 L100 100"/>'
                    f'<g transform="{transform}"><path class="flowchart-link" '
                    f'id="b" d="{second}"/></g></svg>'
                )
                with self.subTest(synthetic_path=second, transform=transform):
                    self.assertEqual(page.locator("svg").evaluate(detector), expected)
            count = 0
            for source in sorted((root / "docs").rglob("*.md")):
                diagrams = re.findall(
                    r"```mermaid\s*\n(.*?)```", source.read_text(), re.S,
                )
                for index, diagram in enumerate(diagrams, 1):
                    count += 1
                    page.set_content('<pre class="mermaid"></pre>')
                    page.locator("pre").evaluate("(n,s)=>n.textContent=s", diagram)
                    page.add_script_tag(content=script)
                    page.evaluate(builder.DocumentationBuilder._diagram_renderer())
                    with self.subTest(source=str(source.relative_to(root)), diagram=index):
                        self.assertEqual(page.locator("svg").evaluate(detector), [])
            self.assertGreater(count, 0)
        finally:
            page.close()

    def _assert_calibration_branches(self, browser: "Browser", script: str) -> None:
        """Contrôle les sorties du choix de reprise dans le vrai diagramme.

        Parameters
        ----------
        browser : Browser
            Navigateur de rendu ; les mesures utilisent ses coordonnées écran.
        script : str
            Bibliothèque Mermaid adaptée, identique à celle des exports.
        """
        source = SCRIPT.parents[1] / "docs/scripts/lightProcess/treatments/README.md"
        diagrams = re.findall(r"```mermaid\s*\n(.*?)```", source.read_text(), re.S)
        page = browser.new_page()
        try:
            page.set_content('<pre class="mermaid"></pre>')
            page.locator("pre").evaluate("(n,s)=>n.textContent=s", diagrams[1])
            page.add_script_tag(content=script)
            page.evaluate(builder.DocumentationBuilder._diagram_renderer())
            center = page.locator("g.node[id*='CALIBRATED_CACHE']").evaluate(
                "n=>{const b=n.getBoundingClientRect();return b.x+b.width/2;}"
            )
            for destination, side in (("REUSE_GROUP", 1), ("PREPARE_LIGHTS", -1)):
                start = page.locator(
                    f"path.LS-CALIBRATED_CACHE.LE-{destination}"
                ).evaluate(
                    "n=>new DOMPoint(n.getPointAtLength(0).x,"
                    "n.getPointAtLength(0).y).matrixTransform(n.getScreenCTM()).x"
                )
                with self.subTest(destination=destination):
                    self.assertGreater(side * (start - center), 0)
        finally:
            page.close()

    def _assert_automatic_layout(self, browser: "Browser", script: str) -> None:
        """Vérifie le recalcul compact après ajout de textes, branches et boucles.

        Parameters
        ----------
        browser : Browser
            Navigateur Chromium déjà lancé pour les tests de rendu.
        script : str
            Source Mermaid adaptée à charger dans chaque page indépendante.
        """
        for evolution in (
            "B --> C[Sortie]",
            'B --> C["Nouveau traitement avec un libellé plus long<br/>sur plusieurs lignes"]'
            '\nB --> D[Autre branche]\nD --> A\nC --> E[Bilan]\nD --> E',
        ):
            diagram = (
                "flowchart TD\nsubgraph OUTER[Session]\n"
                "subgraph INNER[Traitement]\nA[Entrée] --> B{Choix ?}\n"
                + evolution + "\nend\nend"
            )
            metrics = []
            for render in (
                "async()=>{mermaid.initialize({startOnLoad:false});await mermaid.run()}",
                builder.DocumentationBuilder._diagram_renderer(),
            ):
                page = browser.new_page()
                page.set_content('<pre class="mermaid"></pre>')
                page.locator("pre").evaluate("(n,s)=>n.textContent=s", diagram)
                page.add_script_tag(content=script)
                page.evaluate(render)
                metrics.append(page.locator("svg").evaluate(
                    "n => {const nodes=[...n.querySelectorAll('g.node')];"
                    "const boxes=nodes.map(n=>n.getBoundingClientRect());"
                    "let overlaps=0;"
                    "for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){"
                    "const a=boxes[i],b=boxes[j];"
                    "if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>1 && "
                    "Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1)overlaps++;}"
                    "return {height:n.viewBox.baseVal.height,overlaps,"
                    "labels:nodes.map(n=>n.textContent)};}"
                ))
                page.close()
            before, compact = metrics
            with self.subTest(evolution=evolution):
                self.assertLess(compact["height"], before["height"])
                self.assertEqual(compact["labels"], before["labels"])
                self.assertEqual(compact["overlaps"], 0)

    def test_wide_decisions_reject_incompatible_mermaid(self) -> None:
        """Une dépendance vide, modifiée ou déjà adaptée bloque la publication."""
        for source in ("", "const changedMermaid = {};", "SUe=async()=>{};"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, "Mermaid incompatible"):
                    builder.DocumentationBuilder._adapt_mermaid(source)

    def test_wide_decisions_preserve_labels(self) -> None:
        """Les choix verticaux et horizontaux gardent leurs textes à la même taille."""
        cached = SCRIPT.parents[1] / (
            f"tmp/documentation/mermaid-{builder.MERMAID_VERSION}.min.js"
        )
        if not cached.is_file():
            self.skipTest("Cache Mermaid absent : construire la documentation d’abord")
        try:
            from playwright.sync_api import Error, sync_playwright
        except ImportError:
            self.skipTest("Playwright absent")
        original = cached.read_text(encoding="utf-8")
        adapted = builder.DocumentationBuilder._adapt_mermaid(original)
        with self.assertRaisesRegex(ValueError, "Mermaid incompatible"):
            builder.DocumentationBuilder._adapt_mermaid(adapted)
        with patch.dict(os.environ, {
            "PLAYWRIGHT_BROWSERS_PATH": os.environ.get(
                "PLAYWRIGHT_BROWSERS_PATH",
                str(SCRIPT.parents[1] / "tmp/docs-browsers"),
            ),
        }), sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Error as error:
                if "Executable doesn't exist" in str(error):
                    self.skipTest(f"Chromium absent : {error}")
                raise
            try:
                self._assert_automatic_layout(browser, adapted)
                self._assert_calibration_branches(browser, adapted)
                self._assert_all_diagram_crossings(browser, adapted)
                for direction in ("TD", "LR"):
                    for question in (
                        "Choix ?", "Candidat accepté avec score positif ?",
                        "Première ligne<br/>Deuxième ligne<br/>Troisième ligne ?",
                    ):
                        metrics = []
                        for script in (original, adapted):
                            page = browser.new_page()
                            page.set_content(
                                f'<pre class="mermaid">flowchart {direction}\n'
                                f'A[Entrée] --> B{{"{question}"}}\n'
                                'B -->|Oui| C[Sortie]</pre>'
                            )
                            page.add_script_tag(content=script)
                            page.evaluate(
                                "engine => mermaid.initialize({startOnLoad:false,"
                                "flowchart:{defaultRenderer:engine}})", "dagre-wrapper",
                            )
                            page.evaluate("() => mermaid.run()")
                            metrics.append(page.locator("g.node polygon").evaluate(
                                "n => {const b=n.getBBox();"
                                "const l=n.parentNode.querySelector('.label').getBBox();"
                                "return {width:b.width,height:b.height,"
                                "labelWidth:l.width,labelHeight:l.height};}"
                            ))
                            page.close()
                        before, after = metrics
                        with self.subTest(direction=direction, question=question):
                            self.assertGreater(after["width"], before["width"])
                            self.assertLess(after["height"], before["height"])
                            for key in ("labelWidth", "labelHeight"):
                                self.assertAlmostEqual(after[key], before[key], delta=1)
                            self.assertLessEqual(
                                after["labelWidth"] / after["width"]
                                + after["labelHeight"] / after["height"], 1,
                            )
            finally:
                browser.close()

    def test_a4_figures_fit_printable_area(self) -> None:
        """Cadre les figures A4 et préserve les lignes des libellés HTML dans les SVG."""
        try:
            from playwright.sync_api import Error, sync_playwright
        except ImportError:
            self.skipTest("Playwright absent")
        with TemporaryDirectory() as directory, patch.dict(os.environ, {
            "PLAYWRIGHT_BROWSERS_PATH": os.environ.get(
                "PLAYWRIGHT_BROWSERS_PATH",
                str(SCRIPT.parents[1] / "tmp/docs-browsers"),
            ),
        }):
            root = Path(directory)
            builder.renderer.write_assets(root)
            css = (root / "assets/style.css").read_text()
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch()
                except Error as error:
                    if "Executable doesn't exist" in str(error):
                        self.skipTest(f"Chromium absent : {error}")
                    raise
                try:
                    page = browser.new_page(viewport={"width": 680, "height": 1010})
                    page.emulate_media(media="print")
                    page.set_content(
                        f"<style>{css}</style><main class='page'><article>"
                        "<pre class='mermaid'><svg viewBox='0 0 2000 200' "
                        "style='max-width:2000px' width='2000'>"
                        "<foreignObject width='400' height='150'>"
                        "<div xmlns='http://www.w3.org/1999/xhtml'>"
                        "<span>Première ligne</span><br><span>Deuxième ligne</span>"
                        "</div></foreignObject></svg></pre>"
                        "<pre class='mermaid'><svg viewBox='0 0 200 4000' "
                        "style='max-width:200px' width='200'></svg></pre>"
                        "<img width='2000' height='4000' "
                        "src='data:image/svg+xml,%3Csvg xmlns=\"http://www.w3.org/2000/svg\"/>'>"
                        "</article></main>"
                    )
                    figures = page.locator("svg, img").evaluate_all(
                        "nodes => nodes.map(n => {const r=n.getBoundingClientRect();"
                        "return {width:r.width,height:r.height};})"
                    )
                    for figure in figures:
                        self.assertGreater(figure["height"], 0)
                        self.assertLessEqual(figure["width"], 680)
                        self.assertLessEqual(figure["height"], 247 * 96 / 25.4 + 1)
                    self.assertEqual(
                        page.locator("pre.mermaid").first.evaluate(
                            "n => getComputedStyle(n).breakInside"
                        ), "avoid",
                    )
                    label_metrics = {}
                    for medium in ("screen", "print"):
                        page.emulate_media(media=medium)
                        label_metrics[medium] = page.locator(
                            "foreignObject div"
                        ).evaluate(
                            "n => {const lines=n.querySelectorAll('span');"
                            "return {height:n.offsetHeight,"
                            "spacing:lines[1].offsetTop-lines[0].offsetTop};}"
                        )
                    for metric in ("height", "spacing"):
                        self.assertGreater(label_metrics["print"][metric], 0)
                        self.assertAlmostEqual(
                            label_metrics["screen"][metric],
                            label_metrics["print"][metric],
                            delta=0.1 * label_metrics["screen"][metric],
                        )
                finally:
                    browser.close()

    def test_navigation_groups_scripts_and_does_not_repeat_option_chapters(self) -> None:
        """Site et exports partagent l’ordre, les groupes et les liens vers les options."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [Path(path) for path in (
                "AGENTS.md", "README.md", "docs/README.md",
                "docs/sample/OPTIONS.md", "docs/sample/README.md",
            )]
            for path in sources:
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text("# Page\n", encoding="utf-8")
            navigation = [
                {"Accueil": "README.md"},
                {"2. Scripts <outils>": [{"2.1. Exemple": [
                    {"Manuel": "docs/sample/README.md"},
                    {"Options": "docs/sample/OPTIONS.md"},
                    {"Raccourci des options": "docs/sample/OPTIONS.md"},
                ]}]},
            ]
            (root / "mkdocs.yml").write_text(
                "site_name: Test\nnav: " + json.dumps(navigation), encoding="utf-8",
            )
            document = builder.DocumentationBuilder(root, root / "out")
            ordered, tree = document._navigation(sources)
            self.assertEqual(ordered[:3], [
                Path("README.md"), Path("docs/sample/README.md"),
                Path("docs/sample/OPTIONS.md"),
            ])
            self.assertEqual(len(ordered), len(set(ordered)))
            self.assertEqual(tree[-1].label, "Annexes")
            chapters = {root / path: f"chapter-{i}" for i, path in enumerate(ordered)}
            contents = document._contents(tree, chapters)
            self.assertIn("<strong>2. Scripts &lt;outils&gt;</strong><ul>", contents)
            self.assertIn("<strong>2.1. Exemple</strong><ul>", contents)
            self.assertEqual(contents.count('href="#chapter-2"'), 2)

    def test_navigation_rejects_missing_or_external_source(self) -> None:
        """Un lien hors des sources ne disparaît pas silencieusement des exports."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "README.md").write_text("# Projet\n")
            for target, message in (
                ("docs/missing.md", "Page absente"),
                ("../outside.md", "Page absente"),
                ("https://example.org/doc.md", "Page absente"),
                ("README.md#section", "page sans ancre"),
            ):
                with self.subTest(target=target):
                    (root / "mkdocs.yml").write_text(
                        "site_name: Test\nnav: " + json.dumps([{"Page": target}]),
                    )
                    document = builder.DocumentationBuilder(root, root / "out")
                    with self.assertRaisesRegex(ValueError, message):
                        document._navigation([Path("README.md")])

    def test_navigation_without_configuration_keeps_source_order(self) -> None:
        """Le constructeur reste utilisable sur un petit arbre sans configuration."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [Path("second.md"), Path("first.md")]
            for path in sources:
                (root / path).write_text("# " + path.stem + "\n")
            document = builder.DocumentationBuilder(root, root / "out")
            ordered, tree = document._navigation(sources)
            self.assertEqual(ordered, sources)
            self.assertEqual([section.source for section in tree], sources)

    def test_project_navigation_targets_and_anchors_exist(self) -> None:
        """Les accès directs aux manuels et options restent valides après numérotation."""
        root = SCRIPT.parents[1]
        document = builder.DocumentationBuilder(root, root / "tmp/nav-test-output")
        sources = builder.renderer.discover_markdown_files(root, root / "out")
        _, tree = document._navigation(sources)

        def check(sections: tuple[builder.DocumentationSection, ...]) -> None:
            """Parcourt aussi les liens à ancre conservés pour les anciens accès."""
            for section in sections:
                if section.source is not None:
                    self.assertTrue((root / section.source).is_file(), section.source)
                check(section.children)

        check(tree)
        for relative, fragment in (
            ("lightProcess/GUIDE.md", "options-par-étape"),
            ("postProcess/README.md", "configuration-et-mémorisation"),
            ("solarEclipseGif/GUIDE.md", "parametres"),
            ("kstarsAnalyze/README.md", "options-de-ligne-de-commande"),
            ("guideLogAnalyze/README.md", "options-de-ligne-de-commande"),
            ("generate_docs_html/README.md", "options-du-constructeur"),
        ):
            with self.subTest(page=relative):
                source = Path("docs/scripts") / relative
                body, _ = builder.renderer.render_markdown_document(
                    (root / source).read_text(encoding="utf-8"), source,
                )
                self.assertIn(f'id="{fragment}"', body)
                self.assertIn("OPTIONS.html", body)

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
