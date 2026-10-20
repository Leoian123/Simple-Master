"""Test della risoluzione: quale testo vale quando due manuali si toccano. Il modello e' finto e risponde
con il piano scelto dal test; cio' che si prova e' il codice attorno: i controlli sul piano, la quarantena,
la memoria delle decisioni, chi puo' correggere chi."""

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
sys.path.insert(0, str(ROOT / "tests"))

from golden import apply_today, load_manual, snapshot  # noqa: E402
from master import Campaign  # noqa: E402
from master.lineage import CAMPAIGN_ORIGIN, Lineage  # noqa: E402
from master.prepare import Preparer, PrepReport  # noqa: E402
from master.resolve import CACHE_DIR, Rejected, apply_plan  # noqa: E402
from test_prepare import FakePrepClient, make_pdf  # noqa: E402

RULES = "meccanica/regole.md"
ITEMS = "meccanica/oggetti.md"
TABLE = "| Giacca | Armatura 1 |\n| Corazza | Armatura 5 |"
ERRATA = "Errata: la Corazza da' Armatura 4."
FIX = {"rapporto": "sostituisce", "dichiarato": True,
       "modifiche": [{"esistente": "| Corazza | Armatura 5 |", "diventa": "| Corazza | Armatura 4 |", "fonte": "la Corazza da' Armatura 4"}]}


def plan(**kw) -> dict:
    return {"rapporto": "estende", "dichiarato": False, "motivo": "", "tutto": False, "modifiche": [], "aggiunte": []} | kw


def block(sections: list[tuple[str, str, str]], glossary: list[tuple[str, str, str, str]] = ()) -> dict:
    return {"riassunto": "x", "sezioni": [{"file": f, "titolo": t, "testo": b} for f, t, b in sections],
            "glossario": [{"term": a, "kind": k, "description": d, "file": f} for a, k, d, f in glossary]}


class ScriptedClient:
    """Risponde ai confronti tra testi con il piano deciso dal test. Non conosce altre domande."""

    def __init__(self, decide=lambda user: {}) -> None:
        self.decide = decide
        self.asked: list[str] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        assert "RAPPORTO TRA DUE TESTI" in str(kwargs["system"]), "chiamata non prevista"
        user = kwargs["messages"][0]["content"]
        self.asked.append(user)
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                               content=[SimpleNamespace(type="text", text=json.dumps(plan(**self.decide(user))))])


class Silent(ScriptedClient):
    def _create(self, **kwargs):
        raise AssertionError("questa decisione era gia' presa: nessuna chiamata")


class TestApplyPlan(unittest.TestCase):
    def test_what_only_adds_is_appended_and_what_repeats_is_dropped(self) -> None:
        self.assertEqual(apply_plan("A.", "B.", plan(rapporto="estende"), same_title=True), "A.\n\nB.")
        self.assertEqual(apply_plan("A.", "B.", plan(rapporto="variante"), same_title=True), "A.\n\nB.")
        self.assertEqual(apply_plan("A.", "B.", plan(rapporto="identica"), same_title=True), "A.")
        self.assertEqual(apply_plan("A.", "B.", plan(rapporto="estende"), same_title=False), "A.")  # ha gia' la sua sezione
        self.assertEqual(apply_plan("A.", "B.", plan(rapporto="nessuno"), same_title=False), "A.")

    def test_edit_touches_only_the_quoted_passage(self) -> None:
        self.assertEqual(apply_plan(TABLE, ERRATA, plan(**FIX), same_title=True), "| Giacca | Armatura 1 |\n| Corazza | Armatura 4 |")
        wrapped = "Si tira 1d6 +\nRiflessi. Si agisce in ordine."
        edit = {"esistente": "Si tira 1d6 + Riflessi.", "diventa": "Si tira 2d6 + Riflessi.", "fonte": "tira 2d6 + Riflessi"}
        got = apply_plan(wrapped, "Ora si tira 2d6\n+ Riflessi.", plan(rapporto="sostituisce", modifiche=[edit]), same_title=False)
        self.assertEqual(got, "Si tira 2d6 + Riflessi. Si agisce in ordine.")  # gli a capo non contano

    def test_whole_and_additions(self) -> None:
        self.assertEqual(apply_plan("Vecchia regola.", "Nuova regola intera.", plan(rapporto="sostituisce", tutto=True), same_title=True),
                         "Nuova regola intera.")
        new = "Calderaia ha 60.000 abitanti.\n\nC'e' un acquedotto nuovo."
        edit = {"esistente": "40.000 abitanti", "diventa": "60.000 abitanti", "fonte": "60.000 abitanti"}
        got = apply_plan("Citta' di 40.000 abitanti.", new, plan(rapporto="contraddice", modifiche=[edit], aggiunte=["C'e' un acquedotto nuovo."]), True)
        self.assertEqual(got, "Citta' di 60.000 abitanti.\n\nC'e' un acquedotto nuovo.")

    def test_plan_the_code_cannot_verify_is_rejected(self) -> None:
        def edit(**kw) -> dict:
            return plan(rapporto="sostituisce", modifiche=[FIX["modifiche"][0] | kw])

        cases = {
            "non si trova": edit(esistente="| Corazza | Armatura 9 |"),
            "compare piu volte": edit(esistente="Armatura"),
            "la fonte non e' nel testo nuovo": edit(fonte="la Corazza da' Armatura 3"),
            "numeri che non vengono": edit(diventa="| Corazza | Armatura 7 |"),
            "nessuna modifica": plan(rapporto="sostituisce"),
            "rapporto sconosciuto": plan(rapporto="migliora"),
        }
        for message, bad in cases.items():
            with self.assertRaises(Rejected, msg=message) as caught:
                apply_plan(TABLE, ERRATA, bad, same_title=True)
            self.assertIn(message, str(caught.exception))
        with self.assertRaises(Rejected):  # una sezione su un altro argomento non rimpiazza questa per intero
            apply_plan(TABLE, ERRATA, plan(rapporto="sostituisce", tutto=True), same_title=False)
        with self.assertRaises(Rejected):
            apply_plan(TABLE, ERRATA, plan(**FIX, aggiunte=["Frase che nel testo nuovo non c'e'."]), same_title=True)


class ResolveCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.camp = Campaign.create(self.tmp / "camp")
        self.cache = self.tmp / "cache"
        self.cache.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def feed(self, key: str, blocks: list[dict], year: int, client=None, campaign: Campaign | None = None) -> PrepReport:
        named = [(f"blocco_{i:03d}_p{i}-{i}.json", f"pagine {i}-{i}", data) for i, data in enumerate(blocks, start=1)]
        report = PrepReport(source=key)
        Preparer(campaign or self.camp, client=client, cache_root=self.cache, batch=False).assimilate(
            key, key + ".pdf", named, report, meta={"anno": year, "titolo": key.upper()}, variant="v", resolve=client is not None)
        return report

    def errata(self, client, campaign: Campaign | None = None, reverse: bool = False) -> PrepReport:
        order = [("base", [block([(ITEMS, "Armature", TABLE)])], 2019), ("errata", [block([(ITEMS, "Armature", ERRATA)])], 2022)]
        for key, blocks, year in reversed(order) if reverse else order:
            report = self.feed(key, blocks, year, client, campaign)
        return report

    def datum(self, file: str, ntitle: str, campaign: Campaign | None = None) -> dict:
        return Lineage(campaign or self.camp).data["sezioni"][file][ntitle]


class TestResolver(ResolveCase):
    def test_errata_corrects_the_row_and_leaves_the_rest(self) -> None:
        client = ScriptedClient(lambda user: FIX)
        report = self.errata(client)
        self.assertEqual(self.camp.file(ITEMS).get_section("Armature"), "| Giacca | Armatura 1 |\n| Corazza | Armatura 4 |")
        self.assertEqual(len(client.asked), 1)
        for piece in ("§ Armature", "ESISTENTE (da: BASE, 2019)", "Armatura 5", "NUOVO (da: ERRATA, 2022; la sezione con lo stesso titolo)", ERRATA):
            self.assertIn(piece, client.asked[0])
        datum = self.datum(ITEMS, "armature")
        step = datum["risoluzione"][0]
        self.assertEqual((datum["atto"], step["esito"], step["rapporto"], step["manuale"]), ("risoluzione", "applicata", "sostituisce", "errata"))
        self.assertNotIn("spareggio", step)  # la correzione e' dichiarata
        self.assertEqual([p["manuale"] for p in datum["genitori"]], ["base", "errata"])
        self.assertIn("1 sostituisce", report.resolution.summary())

    def test_undeclared_correction_is_marked_as_a_tie_break(self) -> None:
        self.errata(ScriptedClient(lambda user: FIX | {"dichiarato": False}))
        self.assertIn("vince il piu' recente", self.datum(ITEMS, "armature")["risoluzione"][0]["spareggio"])

    def test_plan_that_fails_the_checks_is_quarantined(self) -> None:
        wrong = FIX | {"modifiche": [FIX["modifiche"][0] | {"esistente": "| Corazza | Armatura 9 |"}]}
        report = self.errata(ScriptedClient(lambda user: wrong))
        self.assertEqual(self.camp.file(ITEMS).get_section("Armature"), TABLE + "\n\n" + ERRATA)  # resta la composizione semplice
        step = self.datum(ITEMS, "armature")["risoluzione"][0]
        self.assertEqual(step["esito"], "quarantena")
        self.assertIn("non si trova", step["perche"])
        self.assertEqual(self.datum(ITEMS, "armature")["atto"], "composizione")
        self.assertIn("1 in quarantena", report.resolution.summary())
        full = PrepReport(source="x", resolution=report.resolution).summary()
        self.assertIn("in quarantena: meccanica/oggetti.md § Armature <- Armature", full)

    def test_decision_taken_once_is_never_paid_again(self) -> None:
        self.errata(ScriptedClient(lambda user: FIX))
        text = self.camp.file(ITEMS).read()
        report = self.errata(Silent())  # stessa campagna: sta nel registro
        self.assertEqual((report.resolution.calls, report.resolution.remembered), (0, 1))
        self.assertEqual(self.camp.file(ITEMS).read(), text)
        other = Campaign.create(self.tmp / "altra")  # altra campagna, stessi manuali: sta nella cache condivisa
        self.errata(Silent(), other)
        self.assertEqual(other.file(ITEMS).get_section("Armature"), self.camp.file(ITEMS).get_section("Armature"))
        shutil.rmtree(self.cache / CACHE_DIR)  # e senza cache la campagna basta a se stessa
        self.errata(Silent())
        self.assertEqual(self.camp.file(ITEMS).read(), text)

    def test_order_of_preparation_still_does_not_matter(self) -> None:
        self.errata(ScriptedClient(lambda user: FIX))
        other = Campaign.create(self.tmp / "inverso")
        self.errata(Silent(), other, reverse=True)  # l'errata arriva prima del manuale che corregge
        self.assertEqual(snapshot(other), snapshot(self.camp))
        self.assertEqual(Lineage(other).picture(), Lineage(self.camp).picture())

    def test_failed_call_is_quarantined_and_retried_next_time(self) -> None:
        class Down(ScriptedClient):
            def _create(self, **kwargs):
                raise ConnectionError("rete giu'")

        report = self.errata(Down())
        self.assertEqual(self.camp.file(ITEMS).get_section("Armature"), TABLE + "\n\n" + ERRATA)
        self.assertEqual(report.resolution.steps[0]["esito"], "quarantena")
        self.assertIn("nessuna risposta", report.resolution.steps[0]["perche"])
        client = ScriptedClient(lambda user: FIX)
        self.errata(client)
        self.assertEqual(len(client.asked), 1)
        self.assertIn("Armatura 4", self.camp.file(ITEMS).get_section("Armature"))

    def test_text_written_by_the_player_is_not_corrected(self) -> None:
        self.camp.file(ITEMS).set_section("Armature", "Regola di casa: la corazza vale 6.")
        client = ScriptedClient(lambda user: FIX)
        self.errata(client)
        self.assertEqual(client.asked, [])
        self.assertEqual(self.camp.file(ITEMS).get_section("Armature"), "Regola di casa: la corazza vale 6.\n\n" + TABLE + "\n\n" + ERRATA)

    def test_older_manual_does_not_correct_a_newer_one(self) -> None:
        client = ScriptedClient(lambda user: {"rapporto": "sostituisce", "tutto": True})
        self.feed("nuovo", [block([(RULES, "Pressione", "Indicatore da 0 a 6.")], [("Pressione", "regola", "Indicatore", RULES)])], 2022, client)
        self.feed("vecchio", [block([(RULES, "Officine", "Nelle officine la Pressione sale di 1.")])], 2019, client)
        self.assertEqual(client.asked, [])
        self.assertEqual(self.camp.file(RULES).get_section("Pressione"), "Indicatore da 0 a 6.")

    def test_errata_under_another_title_reaches_its_section(self) -> None:
        def decide(user: str) -> dict:
            if "che parla d'altro e nomina «Riparazioni»" in user:
                return {"rapporto": "sostituisce", "dichiarato": True, "modifiche": [
                    {"esistente": "Difficolta' Media (9)", "diventa": "Difficolta' Difficile (11)", "fonte": "Difficolta' Difficile (11)"}]}
            return {}

        client = ScriptedClient(decide)
        self.feed("base", [block([(RULES, "Riparazioni", "Serve una prova a Difficolta' Media (9).")],
                                 [("Riparazioni", "regola", "Come si ripara", RULES)])], 2019, client)
        self.feed("esp", [block([(RULES, "Officine", "In officina +1. Errata alle Riparazioni: la prova e' a Difficolta' Difficile (11).")])], 2022, client)
        self.assertEqual(self.camp.file(RULES).get_section("Riparazioni"), "Serve una prova a Difficolta' Difficile (11).")
        self.assertIn("Errata alle Riparazioni", self.camp.file(RULES).get_section("Officine"))  # la sua sezione resta com'e'
        step = self.datum(RULES, "riparazioni")["risoluzione"][0]
        self.assertEqual((step["trasversale"], step["sezione"], step["manuale"]), (True, "Officine", "esp"))
        self.feed("esp", [block([(RULES, "Officine", "In officina +1. Errata alle Riparazioni: la prova e' a Difficolta' Difficile (11).")])], 2022, Silent())
        self.assertEqual(self.camp.file(RULES).get_section("Riparazioni"), "Serve una prova a Difficolta' Difficile (11).")


class TestResolverOnAdoptedCampaign(ResolveCase):
    """Il caso vero: una campagna preparata prima della paternita', e un'espansione che corregge il manuale base."""

    def setUp(self) -> None:
        super().setUp()
        self.base, self.expansion = load_manual("base"), load_manual("espansione")
        apply_today(self.camp, self.base)
        self.camp.file("fonte-ottone-e-cenere.md").write("# Fonte: Ottone e Cenere.pdf\n")
        folder = self.cache / "0123456789abcdef-Ottone_e_Cenere" / "v2-testo-c36000-p12"
        folder.mkdir(parents=True)
        for i, data in enumerate(self.base["blocchi"], start=1):
            (folder / f"blocco_{i:03d}_p{data['pagine'][0]}-{data['pagine'][1]}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def expand(self, client) -> PrepReport:
        blocks = [(f"blocco_{i:03d}_p{b['pagine'][0]}-{b['pagine'][1]}.json", f"pagine {b['pagine'][0]}-{b['pagine'][1]}", b)
                  for i, b in enumerate(self.expansion["blocchi"], start=1)]
        report = PrepReport(source="x")
        Preparer(self.camp, client=client, cache_root=self.cache, batch=False).assimilate(
            "golden-espansione", "espansione.pdf", blocks, report, meta=self.expansion["meta"], resolve=True)
        return report

    @staticmethod
    def decide(user: str) -> dict:
        if "§ Riparazioni" in user and "«Nuove regole per le officine»" in user:
            return {"rapporto": "sostituisce", "dichiarato": True, "modifiche": [
                {"esistente": "Difficoltà Media (9)", "diventa": "Difficoltà Difficile (11)", "fonte": "Difficoltà Difficile (11)"}]}
        if "§ Iniziativa" in user:
            return {"rapporto": "sostituisce", "dichiarato": True, "tutto": True}
        return {"rapporto": "nessuno"} if "che parla d'altro" in user else {}

    def test_expansion_corrects_the_adopted_base(self) -> None:
        self.expand(ScriptedClient(self.decide))
        repairs = self.camp.file(RULES).get_section("Riparazioni")
        self.assertIn("Difficoltà Difficile (11)", repairs)
        self.assertNotIn("Media (9)", repairs)
        self.assertNotIn("1d6 + Riflessi", self.camp.file(RULES).get_section("Iniziativa"))
        parents = Lineage(self.camp).parents(RULES, "Riparazioni")
        self.assertEqual(parents[0]["origine"], CAMPAIGN_ORIGIN)
        self.assertTrue(parents[1]["assorbito"] and parents[1]["pagine"] == ["pagine 1-12"])  # il manuale base, riconosciuto dalla cache
        text = {f.name: f.read() for f in self.camp.files()}
        self.expand(Silent())  # rilanciare non costa e non cambia nulla
        self.assertEqual({f.name: f.read() for f in self.camp.files()}, text)

    def test_edit_made_in_play_after_the_resolution_is_kept(self) -> None:
        self.expand(ScriptedClient(self.decide))
        f = self.camp.file(RULES)
        f.set_section("Riparazioni", f.get_section("Riparazioni") + " Nota del master: Tobia sa riparare tutto.")
        edited = f.get_section("Riparazioni")
        self.expand(ScriptedClient(self.decide))  # il passo da cambiare non c'e' piu': nessuna modifica, nessun danno
        self.assertEqual(f.get_section("Riparazioni"), edited)


class TestPreparerResolves(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.camp = Campaign.create(self.tmp / "camp")
        self.pdfs = []
        for name in ("base", "espansione"):
            pdf = self.tmp / f"{name}.pdf"
            make_pdf(pdf, [f"{name} pagina {i} con regole di combattimento e luoghi" for i in range(1, 5)])
            self.pdfs.append(pdf)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def prepare(self, client, pdf: Path, **kw) -> PrepReport:
        return Preparer(self.camp, client=client, cache_root=self.tmp / "cache", batch=False).run(
            str(pdf), consolidate=False, tidy=False, progress=lambda s: None, **kw)

    def test_second_manual_is_compared_with_the_first(self) -> None:
        client = FakePrepClient()
        report = self.prepare(client, self.pdfs[0])
        self.assertEqual(client.resolve_calls, [])  # un manuale solo: niente da confrontare
        self.assertNotIn("Risoluzione", report.summary())
        report = self.prepare(client, self.pdfs[1])
        self.assertGreaterEqual(len(client.resolve_calls), 1)
        self.assertIn("Risoluzione:", report.summary())
        self.assertIn("Scontri possibili:", report.summary())

    def test_resolution_can_be_switched_off(self) -> None:
        client = FakePrepClient()
        self.prepare(client, self.pdfs[0], resolve=False)
        self.prepare(client, self.pdfs[1], resolve=False)
        self.assertEqual(client.resolve_calls, [])


if __name__ == "__main__":
    unittest.main()
