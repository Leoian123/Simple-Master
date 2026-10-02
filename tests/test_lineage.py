"""Test della paternita': la campagna si compone dai manuali in ordine canonico, ogni dato conosce i
suoi genitori, e cio' che la preparazione non ha scritto non viene mai riscritto. Tutto senza rete."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from golden import apply_staged, apply_today, load_manual, snapshot  # noqa: E402
from master import Campaign  # noqa: E402
from master.glossary import GlossaryEntry  # noqa: E402
from master.lineage import CAMPAIGN_ORIGIN, Lineage, fingerprint  # noqa: E402
from master.prepare import Preparer, PrepReport  # noqa: E402
from test_prepare import FakePrepClient, make_pdf  # noqa: E402

RULES = "meccanica/regole.md"
WORLD = "ambientazione/mondo.md"


def block(sections: list[tuple[str, str, str]], glossary: list[tuple[str, str, str, str]] = ()) -> dict:
    return {"riassunto": "x", "sezioni": [{"file": f, "titolo": t, "testo": b} for f, t, b in sections],
            "glossario": [{"term": a, "kind": k, "description": d, "file": f} for a, k, d, f in glossary]}


class LineageCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.camp = Campaign.create(self.tmp / "camp")
        self.cache = self.tmp / "cache"
        self.cache.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def feed(self, key: str, blocks: list[dict], year: int | None = None, variant: str = "v", first: int = 1,
             campaign: Campaign | None = None) -> None:
        named = [(f"blocco_{i:03d}_p{i}-{i}.json", f"pagine {i}-{i}", data) for i, data in enumerate(blocks, start=first)]
        Preparer(campaign or self.camp, cache_root=self.cache).assimilate(
            key, key + ".pdf", named, PrepReport(source=key), meta={"anno": year}, variant=variant)

    def section(self, file: str, title: str) -> str | None:
        return self.camp.file(file).get_section(title)


class TestComposition(LineageCase):
    def test_order_of_preparation_does_not_matter(self) -> None:
        base, expansion = load_manual("base"), load_manual("espansione")
        other = Campaign.create(self.tmp / "inverso")
        for manual in (base, expansion):
            apply_staged(self.camp, manual)
        for manual in (expansion, base):
            apply_staged(other, manual)
        self.assertEqual(snapshot(self.camp), snapshot(other))
        self.assertEqual(Lineage(self.camp).picture(), Lineage(other).picture())
        # e la fusione di prima, invece, dipendeva dall'ordine: e' il difetto che la paternita' toglie
        old_a, old_b = Campaign.create(self.tmp / "a"), Campaign.create(self.tmp / "b")
        for manual in (base, expansion):
            apply_today(old_a, manual)
        for manual in (expansion, base):
            apply_today(old_b, manual)
        self.assertNotEqual(snapshot(old_a), snapshot(old_b))

    def test_every_section_knows_its_parents(self) -> None:
        apply_staged(self.camp, load_manual("espansione"))
        apply_staged(self.camp, load_manual("base"))
        lineage = Lineage(self.camp)
        parents = lineage.parents(RULES, "Iniziativa")
        self.assertEqual([p["manuale"] for p in parents], ["golden-base", "golden-espansione"])  # dal piu' vecchio
        self.assertEqual([p["pagine"] for p in parents], [["pagine 1-12"], ["pagine 1-10"]])
        body = self.section(RULES, "Iniziativa")
        self.assertLess(body.index("1d6 + Riflessi"), body.index("2d6 + Riflessi"))
        self.assertEqual(lineage.data["sezioni"][RULES]["iniziativa"]["impronta"], fingerprint(body))
        self.assertEqual([p["manuale"] for p in lineage.parents(RULES, "Vapori tossici")], ["golden-espansione"])
        self.assertEqual(lineage.parents(RULES, "Sistema"), [{"origine": CAMPAIGN_ORIGIN}])  # era nel modello di campagna
        self.assertEqual(lineage.data["manuali"]["golden-base"]["anno"], 2019)

    def test_word_for_word_reprise_is_recorded_not_repeated(self) -> None:
        base, expansion = load_manual("base"), load_manual("espansione")
        apply_staged(self.camp, base)
        before = self.section(RULES, "Prove")
        apply_staged(self.camp, expansion)
        self.assertEqual(self.section(RULES, "Prove"), before)
        reprise = Lineage(self.camp).parents(RULES, "Prove")[1]
        self.assertEqual((reprise["manuale"], reprise.get("ripresa")), ("golden-espansione", True))

    def test_rerun_changes_nothing(self) -> None:
        base, expansion = load_manual("base"), load_manual("espansione")
        for manual in (base, expansion):
            apply_staged(self.camp, manual)
        before, picture = snapshot(self.camp), Lineage(self.camp).picture()
        raw = {f.name: f.read() for f in self.camp.files()}
        for manual in (expansion, base, expansion):
            apply_staged(self.camp, manual)
        self.assertEqual(snapshot(self.camp), before)
        self.assertEqual(Lineage(self.camp).picture(), picture)
        self.assertEqual({f.name: f.read() for f in self.camp.files()}, raw)

    def test_manual_prepared_in_two_page_ranges(self) -> None:
        first = block([(RULES, "Combattimento", "Iniziativa: d20 + Agilita'.")])
        second = block([(RULES, "Combattimento", "Danni: arma + Forza."), (WORLD, "Velmora", "Un porto.")])
        self.feed("m", [first])
        self.feed("m", [second], first=2)
        self.assertEqual(self.section(RULES, "Combattimento"), "Iniziativa: d20 + Agilita'.\n\nDanni: arma + Forza.")
        self.assertEqual(self.section(WORLD, "Velmora"), "Un porto.")
        self.assertEqual(Lineage(self.camp).parents(RULES, "Combattimento")[0]["pagine"], ["pagine 1-1", "pagine 2-2"])

    def test_another_variant_replaces_the_manual(self) -> None:
        """Cambiano i prompt o la modalita': i blocchi sono altri, e non devono accodarsi ai vecchi."""
        self.feed("m", [block([(RULES, "Combattimento", "Testo estratto in modalita' testo.")])], variant="v2-testo")
        self.feed("m", [block([(RULES, "Combattimento", "Testo estratto dalle pagine originali.")])], variant="v2-pdf")
        self.assertEqual(self.section(RULES, "Combattimento"), "Testo estratto dalle pagine originali.")
        self.assertEqual(Lineage(self.camp).data["manuali"]["m"]["variante"], "v2-pdf")

    def test_equivalent_titles_across_manuals_are_one_section(self) -> None:
        self.feed("a", [block([(WORLD, "Camarilla", "La setta dei principi.")])], year=2018)
        self.feed("b", [block([(WORLD, "La Camarilla", "Nuovi principati.")])], year=2021)
        self.assertEqual([t for t in self.camp.file(WORLD).outline() if "amarilla" in t], ["Camarilla"])
        self.assertEqual(self.section(WORLD, "Camarilla"), "La setta dei principi.\n\nNuovi principati.")


class TestTextFromOutside(LineageCase):
    """Cio' che c'era gia', o che cambia in gioco o a mano, e' una parte come le altre: la prima."""

    def test_existing_section_is_kept_first_in_any_order(self) -> None:
        other = Campaign.create(self.tmp / "inverso")
        a = [block([(WORLD, "Velmora", "Dal manuale base: un porto.")])]
        b = [block([(WORLD, "Velmora", "Dall'espansione: le Docce.")])]
        for campaign, order in ((self.camp, (("a", a, 2018), ("b", b, 2021))), (other, (("b", b, 2021), ("a", a, 2018)))):
            campaign.file(WORLD).set_section("Velmora", "Scritto dal giocatore prima di ogni manuale.")
            for key, blocks, year in order:
                self.feed(key, blocks, year=year, campaign=campaign)
        expected = "Scritto dal giocatore prima di ogni manuale.\n\nDal manuale base: un porto.\n\nDall'espansione: le Docce."
        self.assertEqual(self.section(WORLD, "Velmora"), expected)
        self.assertEqual(other.file(WORLD).get_section("Velmora"), expected)
        parents = Lineage(self.camp).parents(WORLD, "Velmora")
        self.assertEqual([p.get("origine") or p["manuale"] for p in parents], [CAMPAIGN_ORIGIN, "a", "b"])

    def test_edit_made_in_play_is_never_rewritten(self) -> None:
        a = [block([(WORLD, "Velmora", "Un porto sul mare grigio.")])]
        self.feed("a", a, year=2018)
        self.camp.file(WORLD).set_section("Velmora", "Un porto sul mare grigio. Il faro e' crollato nella sessione 3.")
        self.feed("a", a, year=2018)  # rilanciare il manuale non riporta il testo originale ne' lo accoda
        self.assertEqual(self.section(WORLD, "Velmora"), "Un porto sul mare grigio. Il faro e' crollato nella sessione 3.")
        self.feed("b", [block([(WORLD, "Velmora", "Le Docce, quartiere nuovo.")])], year=2021)
        self.assertEqual(self.section(WORLD, "Velmora"),
                         "Un porto sul mare grigio. Il faro e' crollato nella sessione 3.\n\nLe Docce, quartiere nuovo.")
        parents = Lineage(self.camp).parents(WORLD, "Velmora")
        self.assertEqual(parents[0]["origine"], CAMPAIGN_ORIGIN)
        self.assertEqual([(p["manuale"], p.get("assorbito", False)) for p in parents[1:]], [("a", True), ("b", False)])

    def test_manual_that_grows_adds_only_the_new_part_to_an_edited_section(self) -> None:
        self.feed("a", [block([(WORLD, "Velmora", "Un porto sul mare grigio, con un faro.")])])
        self.camp.file(WORLD).set_section("Velmora", "Un porto sul mare grigio, con un faro.\n\nNota del master: il faro e' spento.")
        self.feed("a", [block([(WORLD, "Velmora", "Sotto il porto corrono le gallerie dei contrabbandieri.")])], first=2)
        self.assertEqual(self.section(WORLD, "Velmora"),
                         "Un porto sul mare grigio, con un faro.\n\nNota del master: il faro e' spento.\n\n"
                         "Sotto il porto corrono le gallerie dei contrabbandieri.")

    def test_removed_section_is_not_resurrected(self) -> None:
        a = [block([(WORLD, "Clan Tremere", "Stregoni del sangue."), (WORLD, "Tremere", "Vedi Clan Tremere.")])]
        self.feed("a", a, year=2018)
        self.camp.file(WORLD).remove_section("Clan Tremere")  # fusa in un'altra dalla sanificazione, o tolta a mano
        self.feed("a", a, year=2018)
        self.assertNotIn("Clan Tremere", self.camp.file(WORLD).outline())
        self.feed("b", [block([(WORLD, "Clan Tremere", "La Piramide dopo Vienna.")])], year=2021)
        self.assertEqual(self.section(WORLD, "Clan Tremere"), "La Piramide dopo Vienna.")  # materiale nuovo si', il vecchio no

    def test_glossary_keeps_the_oldest_manual_and_what_was_there(self) -> None:
        self.camp.glossary.upsert(GlossaryEntry("Kael", "png", "Il personaggio di casa", "ambientazione/npc.md"))
        base = [block([], [("Irma", "png", "Maestra della Gilda", "ambientazione/npc.md"), ("Kael", "png", "Un omonimo", WORLD)])]
        expansion = [block([], [("Irma", "png", "Citata altrove", WORLD), ("Docce", "luogo", "Quartiere", WORLD)])]
        self.feed("b", expansion, year=2022)
        self.assertEqual(self.camp.glossary.get("Irma").description, "Citata altrove")
        self.feed("a", base, year=2019)
        irma = self.camp.glossary.get("Irma")
        self.assertEqual((irma.description, irma.file), ("Maestra della Gilda", "ambientazione/npc.md"))
        self.assertEqual(self.camp.glossary.get("Kael").description, "Il personaggio di casa")
        self.assertEqual(Lineage(self.camp).data["glossario"]["irma"]["manuale"], "a")
        self.assertEqual(Lineage(self.camp).data["glossario"]["kael"]["origine"], CAMPAIGN_ORIGIN)
        # una voce tolta (dal riordino o a mano) non rientra da sola; una corretta in gioco non viene riscritta
        self.camp.glossary.remove("Docce")
        self.camp.glossary.upsert(GlossaryEntry("Irma", "png", "Maestra della Gilda, ora in esilio", "ambientazione/npc.md"))
        self.feed("b", expansion, year=2022)
        self.feed("a", base, year=2019)
        self.assertIsNone(self.camp.glossary.get("Docce"))
        self.assertEqual(self.camp.glossary.get("Irma").description, "Maestra della Gilda, ora in esilio")


class TestAdoption(LineageCase):
    """Una campagna preparata prima della paternita': i manuali gia' dentro si riconoscono dalla cache."""

    def setUp(self) -> None:
        super().setUp()
        self.base, self.expansion = load_manual("base"), load_manual("espansione")
        apply_today(self.camp, self.base)
        self.camp.file("fonte-ottone-e-cenere.md").write("# Fonte: Ottone e Cenere.pdf\n\nIndice per pagine.\n")
        self.key = "0123456789abcdef-Ottone_e_Cenere"
        folder = self.cache / self.key / "v2-testo-c36000-p12"
        folder.mkdir(parents=True)
        (self.cache / "consolidati").mkdir()
        for i, data in enumerate(self.base["blocchi"], start=1):
            a, b = data["pagine"]
            (folder / f"blocco_{i:03d}_p{a}-{b}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def staged(self, manual: dict, key: str | None = None) -> None:
        meta = manual["meta"]
        blocks = [(f"blocco_{i:03d}_p{b['pagine'][0]}-{b['pagine'][1]}.json", f"pagine {b['pagine'][0]}-{b['pagine'][1]}", b)
                  for i, b in enumerate(manual["blocchi"], start=1)]
        Preparer(self.camp, cache_root=self.cache).assimilate(key or meta["chiave"], manual["nome"] + ".pdf", blocks,
                                                              PrepReport(source="x"), meta=meta)

    def test_manual_already_in_the_campaign_is_recognised_from_the_cache(self) -> None:
        before = self.section(RULES, "Iniziativa")
        self.staged(self.expansion)
        lineage = Lineage(self.camp)
        self.assertTrue(lineage.data["manuali"][self.key]["adottato"])
        parents = lineage.parents(RULES, "Iniziativa")
        self.assertEqual(parents[0]["origine"], CAMPAIGN_ORIGIN)
        self.assertEqual((parents[1]["manuale"], parents[1]["pagine"], parents[1]["assorbito"]), (self.key, ["pagine 1-12"], True))
        self.assertEqual(parents[2]["manuale"], "golden-espansione")
        self.assertTrue(self.section(RULES, "Iniziativa").startswith(before))
        # una sezione che l'espansione non tocca resta senza registro, ma la sua origine si legge lo stesso
        self.assertEqual(lineage.parents("meccanica/creature.md", "Ratto delle condotte")[0]["manuale"], self.key)

    def test_preparing_the_adopted_manual_again_does_not_duplicate_it(self) -> None:
        self.staged(self.expansion)
        raw = {f.name: f.read() for f in self.camp.files()}
        self.staged(self.base, key=self.key)
        self.assertEqual({f.name: f.read() for f in self.camp.files()}, raw)
        self.assertEqual(self.section(RULES, "Iniziativa").count("1d6 + Riflessi"), 1)

    def test_no_cache_no_guess(self) -> None:
        shutil.rmtree(self.cache / self.key)
        self.staged(self.expansion)
        lineage = Lineage(self.camp)
        self.assertNotIn(self.key, lineage.data["manuali"])
        self.assertEqual(lineage.parents("meccanica/creature.md", "Ratto delle condotte"), [{"origine": CAMPAIGN_ORIGIN}])


class TestPreparerWithLineage(unittest.TestCase):
    """La preparazione vera (PDF, client finto): interruzioni, consolidamento, profilo dei dadi."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "camp"
        shutil.copytree(ROOT / "campaigns" / "esempio", self.root, ignore=shutil.ignore_patterns("preparazione", "sessioni"))
        self.camp = Campaign(self.root)
        self.cache = self.tmp / "cache"
        self.pdf = self.tmp / "manuale.pdf"
        make_pdf(self.pdf, [f"Pagina {i} con regole di combattimento e luoghi" for i in range(1, 5)])

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_prep(self, client, pdf: Path | None = None, **kw):
        import master.prepare as mod

        orig = mod.load_chunks
        mod.load_chunks = lambda path, **k: orig(path, chars_per_chunk=60, **{a: b for a, b in k.items() if a != "chars_per_chunk"})
        try:
            return Preparer(self.camp, client=client, cache_root=self.cache, batch=False).run(
                str(pdf or self.pdf), tidy=False, progress=lambda s: None, **kw)
        finally:
            mod.load_chunks = orig

    @staticmethod
    def dice_calls(client) -> int:
        return sum(1 for c in client.calls if "PROFILO DEI DADI" in str(c.get("system")))

    def test_interrupted_run_leaves_the_campaign_untouched(self) -> None:
        class Breaking(FakePrepClient):
            def _create(self, **kwargs):
                if len(self.calls) >= 1:
                    raise ConnectionError("rete giu'")
                return super()._create(**kwargs)

        before = {f.name: f.read() for f in self.camp.files()}
        with self.assertRaises(ConnectionError):
            self.run_prep(Breaking(), consolidate=False)
        self.assertEqual({f.name: f.read() for f in self.camp.files()}, before)
        self.run_prep(FakePrepClient(), consolidate=False)  # il primo blocco e' in cache e nello spazio del manuale
        self.assertEqual(self.camp.file(RULES).get_section("Combattimento"), "Iniziativa: d20 + Agilita'.\n\nDanni: arma + Forza.")

    def test_consolidated_sections_are_not_sent_again(self) -> None:
        client = FakePrepClient()
        self.run_prep(client, consolidate=True)
        self.assertEqual(client.consolidation_calls(), 1)
        text = self.camp.file(RULES).read()
        self.run_prep(client, consolidate=True)
        self.assertEqual(client.consolidation_calls(), 1)  # gia' ripulita e non piu' toccata: niente modello
        self.assertEqual(self.camp.file(RULES).read(), text)
        key = next(iter(Lineage(self.camp).data["manuali"]))
        self.assertIn("combattimento", Lineage(self.camp).data["manuali"][key]["consolidate"][RULES])

    def test_consolidation_cleans_only_the_manual_text(self) -> None:
        self.camp.file(RULES).set_section("Combattimento", "Regola di casa: si tira sempre in vista.")
        client = FakePrepClient()
        self.run_prep(client, consolidate=True)
        sent = next(c["messages"][0]["content"] for c in client.calls if "Sezioni di `" in str(c["messages"][0]["content"]))
        self.assertNotIn("Regola di casa", sent)
        body = self.camp.file(RULES).get_section("Combattimento")
        self.assertTrue(body.startswith("Regola di casa: si tira sempre in vista.\n\n"))
        self.assertIn("Consolidato.", body)

    def test_dice_profile_follows_the_rules_not_the_last_manual(self) -> None:
        from master import dice

        client = FakePrepClient()
        self.run_prep(client, consolidate=False)
        first = next(iter(Lineage(self.camp).data["manuali"]))
        self.assertEqual(self.dice_calls(client), 1)
        self.assertTrue(list((self.cache / first).glob(f"*/{dice.PROFILE_NAME}")))  # un manuale solo: nella sua cache

        class Revised(FakePrepClient):  # un'espansione che cambia una regola sui tiri
            def _create(self, **kwargs):
                reply = super()._create(**kwargs)
                for part in reply.content:
                    part.text = part.text.replace("Danni: arma + Forza.", "Danni: 2d6 + Forza.")
                return reply

        other = self.tmp / "espansione.pdf"
        make_pdf(other, [f"Altra pagina {i} con regole di combattimento e luoghi" for i in range(1, 5)])
        revised = Revised()
        self.run_prep(revised, pdf=other, consolidate=False)
        lineage = Lineage(self.camp)
        second = next(k for k in lineage.data["manuali"] if k != first)
        self.assertIn("2d6 + Forza", self.camp.file(RULES).get_section("Combattimento"))
        self.assertEqual(self.dice_calls(revised), 1)  # le regole sui tiri sono cambiate: il profilo si rifa'
        digest = fingerprint(dice.rules_about_dice(self.camp))
        self.assertEqual(lineage.data["dadi"]["regole"], digest)
        self.assertTrue(list((self.cache / "dadi").glob(f"*-{digest}.json")))  # in cache per contenuto delle regole...
        self.assertFalse(list((self.cache / second).glob(f"*/{dice.PROFILE_NAME}")))  # ...non nella cache dell'ultimo arrivato
        self.run_prep(revised, pdf=other, consolidate=False)  # stesse regole: nessuna chiamata
        self.assertEqual(self.dice_calls(revised), 1)


if __name__ == "__main__":
    unittest.main()
