"""Sanificazione dei doppioni: passo gratuito e passo approfondito con client finto."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from master import Campaign, GlossaryEntry  # noqa: E402
from master.sanitize import Sanitizer, best_title, norm_title  # noqa: E402

LONG = "La Camarilla e' la setta piu' grande e organizzata dei Fratelli, fondata per imporre la Masquerade a tutti i vampiri."


def _block(**kw):
    return SimpleNamespace(**kw)


class FakeGroupClient:
    """Raggruppa 'Tremere' con 'Clan Tremere'; per il consolidamento restituisce le sezioni ripulite."""

    def __init__(self, groups: list[dict] | None = None) -> None:
        self.calls: list[dict] = []
        self.groups = groups
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        user = kwargs["messages"][0]["content"]
        usage = SimpleNamespace(input_tokens=50, output_tokens=20)
        if user.startswith("Sezioni di `"):
            body = user.split(":\n\n", 1)[1]
            return SimpleNamespace(stop_reason="end_turn", usage=usage, content=[_block(type="text", text=body + "\n\nRipulito.")])
        groups = self.groups if self.groups is not None else [{"titolo": "Clan Tremere", "unisci": ["Tremere", "Clan Tremere"]}]
        return SimpleNamespace(stop_reason="end_turn", usage=usage, content=[_block(type="text", text=json.dumps({"gruppi": groups}))])


class TestSanitize(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.camp = Campaign.create(self.tmp / "camp")
        self.mondo = self.camp.file("ambientazione/mondo.md")
        self.mondo.write(
            "# Mondo\n\n## Camarilla\n\n" + LONG + "\n\nNasce nel XV secolo.\n\n"
            "## Tremere\n\nStregoni del sangue, clan giovane.\n\n"
            "## La Camarilla\n\n" + LONG + "\n\nIl Principe governa ogni citta'.\n\n"
            "## Clan Tremere\n\nUsurpatori nati da maghi mortali, custodi della Stregoneria.\n\n"
            "## Anarchici\n\nRibelli contro i Principi.\n\n## Sabbat\n\nSetta rivale.\n\n## Prima Inquisizione\n\nMedioevo.\n"
        )
        g = self.camp.glossary
        g.upsert(GlossaryEntry("La Fame", "regola", "Il bisogno di sangue.", "meccanica/regole.md"))
        g.upsert(GlossaryEntry("Fame", "altro", "Il bisogno di sangue del vampiro, da 0 a 5, che alimenta la Bestia.", "meccanica/regole.md"))
        g.upsert(GlossaryEntry("Camarilla", "fazione", "Setta dei Fratelli.", "ambientazione/mondo.md"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_title_normalization(self) -> None:
        self.assertEqual(norm_title("La Seconda Inquisizione"), norm_title("seconda inquisizione"))
        self.assertEqual(norm_title("L'Entità"), norm_title("Entità"))
        self.assertNotEqual(norm_title("Tremere"), norm_title("Clan Tremere"))
        self.assertEqual(best_title(["La Camarilla", "Camarilla"]), "Camarilla")

    def test_free_pass_merges_equivalent_titles_and_drops_repeated_blocks(self) -> None:
        report = Sanitizer(self.camp).run_free()
        outline = self.mondo.outline()
        self.assertEqual(outline.count("Camarilla"), 1)
        self.assertNotIn("La Camarilla", outline)
        self.assertEqual(outline[0], "Camarilla")  # resta al posto della prima
        body = self.mondo.get_section("Camarilla")
        self.assertEqual(body.count(LONG), 1)  # il blocco identico resta una volta sola
        self.assertIn("Nasce nel XV secolo.", body)
        self.assertIn("Il Principe governa ogni citta'.", body)
        self.assertEqual(report.removed_blocks, 1)
        self.assertEqual(report.touched, {"ambientazione/mondo.md": ["camarilla"]})
        # il soggetto uguale con nome diverso NON e' compito del passo gratuito
        self.assertIn("Tremere", outline)
        self.assertIn("Clan Tremere", outline)
        # glossario: una voce sola, senza articolo, con la descrizione piu' completa e il tipo vero
        fame = self.camp.glossary.get("Fame")
        self.assertIsNone(self.camp.glossary.get("La Fame"))
        self.assertIn("alimenta la Bestia", fame.description)
        self.assertEqual(fame.kind, "regola")
        # ripetibile: una seconda passata non trova nulla
        again = Sanitizer(self.camp).run_free()
        self.assertEqual((again.merged_sections, again.removed_blocks, again.merged_glossary), ([], 0, []))

    def test_deep_pass_sends_only_titles_and_merges_same_subject(self) -> None:
        client = FakeGroupClient()
        lines: list[str] = []
        report = Sanitizer(self.camp, client=client, model="claude-opus-5", cache_root=self.tmp / "cache").run(deep=True, progress=lines.append)
        outline = self.mondo.outline()
        self.assertIn("Clan Tremere", outline)
        self.assertNotIn("Tremere", outline)
        body = self.mondo.get_section("Clan Tremere")
        self.assertIn("Stregoni del sangue", body)
        self.assertIn("Usurpatori nati da maghi mortali", body)
        self.assertIn("Ripulito.", body)  # le sezioni fuse passano dal consolidamento
        # la chiamata sui titoli non contiene i testi delle sezioni
        title_calls = [c for c in client.calls if not c["messages"][0]["content"].startswith("Sezioni di `")]
        self.assertTrue(title_calls)
        self.assertNotIn("Stregoni del sangue", title_calls[0]["messages"][0]["content"])
        self.assertEqual(title_calls[0]["output_config"]["format"]["type"], "json_schema")
        self.assertTrue(any("Clan Tremere" in g for g in report.deep_groups))
        self.assertTrue(any("stima consolidamento" in l for l in lines))

    def test_deep_pass_rejects_invalid_groups(self) -> None:
        client = FakeGroupClient(groups=[
            {"titolo": "Inventato", "unisci": ["Anarchici", "Sabbat"]},          # titolo non nel gruppo
            {"titolo": "Sabbat", "unisci": ["Sabbat"]},                            # un solo membro
            {"titolo": "Anarchici", "unisci": ["Anarchici", "Non Esiste"]},        # membro inesistente -> resta uno solo
        ])
        from master.sanitize import SanitizeReport

        before = self.mondo.read()
        Sanitizer(self.camp, client=client, model="claude-opus-5", cache_root=self.tmp / "cache").run_deep(
            SanitizeReport(), progress=lambda s: None)
        # solo il passo approfondito e' stato eseguito qui: nessuna fusione accettata
        self.assertEqual(self.mondo.read(), before)

    def test_preparation_runs_the_free_pass(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_prepare import FakePrepClient, make_pdf
        from master.prepare import Preparer

        pdf = self.tmp / "m.pdf"
        make_pdf(pdf, ["Pagina uno con regole", "Pagina due con regole"])
        lines: list[str] = []
        Preparer(self.camp, client=FakePrepClient(), cache_root=self.tmp / "cache").run(str(pdf), tidy=False, progress=lines.append)
        self.assertEqual(self.mondo.outline().count("Camarilla"), 1)
        self.assertTrue(any("sanificazione:" in l for l in lines))


if __name__ == "__main__":
    unittest.main()
