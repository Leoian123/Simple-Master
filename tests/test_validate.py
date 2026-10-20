"""La verifica di una campagna: tre livelli, ogni controllo ha il caso che lo fa scattare, nessuna scrittura."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from master import Campaign, GlossaryEntry  # noqa: E402
from master.validate import LEVELS, hook_text, validate, validate_all  # noqa: E402


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "campaigns" / "esempio", self.tmp / "camp", ignore=shutil.ignore_patterns("sessioni", "preparazione", "stato.json"))
        self.camp = Campaign(self.tmp / "camp")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def titles(self, level: str) -> list[str]:
        return [f.title for f in validate(self.camp).of(level)]

    def write_json(self, name: str, data) -> None:
        path = self.camp.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data, encoding="utf-8")

    def play(self, turns: int = 4, slug: str = "kael") -> None:
        for i in range(turns):
            self.camp.append_turn(slug, f"azione {i}", f"narrazione {i}")


class TestReport(Base):
    def test_example_campaign_is_clean_and_nothing_is_written(self) -> None:
        before = _snapshot(self.camp.root)
        report = validate(self.camp)
        self.assertEqual(report.findings, [], report.text())
        self.assertTrue(report.ok)
        self.assertEqual(report.counts(), {"errors": 0, "warnings": 0, "recommendations": 0})
        self.assertIn("tutto in ordine", report.text())
        self.assertEqual(_snapshot(self.camp.root), before)

    def test_json_shape_has_counts_and_the_three_lists(self) -> None:
        self.camp.glossary.upsert(GlossaryEntry("Vecchia rovina", "luogo", "Non esiste piu'", "rovine.md"))
        data = validate(self.camp).as_dict()
        self.assertEqual(data["campaign"], "camp")
        self.assertEqual(set(data), {"campaign", "validatorVersion", "counts", "errors", "warnings", "recommendations"})
        self.assertEqual(data["counts"], {"errors": 0, "warnings": 1, "recommendations": 0})
        item = data["warnings"][0]
        self.assertEqual(set(item), {"level", "path", "title", "message", "fix"})
        self.assertEqual(item["path"], "glossario.md § Vecchia rovina")
        self.assertTrue(item["fix"])
        json.dumps(data)  # serializzabile

    def test_text_groups_by_level_and_headline_counts(self) -> None:
        self.camp.glossary.upsert(GlossaryEntry("Vecchia rovina", "luogo", "Non esiste piu'", "rovine.md"))
        self.write_json("meccanica/dadi.json", "{non json")
        text = validate(self.camp).text()
        self.assertIn("1 errore, 1 avviso, 0 consigli", text)
        self.assertLess(text.index("ERRORI"), text.index("AVVISI"))
        self.assertNotIn("CONSIGLI", text)

    def test_ok_means_no_errors_even_with_warnings(self) -> None:
        self.camp.glossary.upsert(GlossaryEntry("Vecchia rovina", "luogo", "Non esiste piu'", "rovine.md"))
        self.assertTrue(validate(self.camp).ok)
        self.write_json("meccanica/dadi.json", "{non json")
        self.assertFalse(validate(self.camp).ok)

    def test_levels_are_the_three_known(self) -> None:
        self.assertEqual(LEVELS, ("error", "warning", "recommendation"))


class TestGlossary(Base):
    def test_entry_to_missing_file_is_a_warning(self) -> None:
        self.camp.glossary.upsert(GlossaryEntry("Vecchia rovina", "luogo", "Non esiste piu'", "rovine.md"))
        self.assertIn("voce verso un file sparito", self.titles("warning"))

    def test_term_absent_from_its_file_is_a_warning(self) -> None:
        self.camp.glossary.upsert(GlossaryEntry("Fantasma", "png", "Mai scritto nel file", "ambientazione/npc.md"))
        self.assertIn("termine assente dal file", self.titles("warning"))

    def test_file_entries_are_not_checked_for_the_term(self) -> None:
        self.camp.glossary.upsert(GlossaryEntry("Indice oggetti", "file", "File: ...", "meccanica/oggetti.md"))
        self.assertNotIn("termine assente dal file", self.titles("warning"))

    def test_duplicate_term_is_a_warning(self) -> None:
        text = self.camp.glossary.file.read()
        self.camp.glossary.file.write(text + "| velmora | luogo | Doppione | ambientazione/mondo.md |\n")
        self.assertIn("voce doppia", self.titles("warning"))


class TestDice(Base):
    def test_broken_json_is_an_error(self) -> None:
        self.write_json("meccanica/dadi.json", "{non json")
        self.assertIn("profilo dei dadi illeggibile", self.titles("error"))

    def test_malformed_profile_is_an_error_with_the_path(self) -> None:
        self.write_json("meccanica/dadi.json", {"sistema": "x", "note": "", "tiri": [
            {"nome": "riserva", "tipo": "successi", "dado": 10, "dadi_fissi": None, "successo_da": 11},
            {"nome": "riserva", "tipo": "boh", "dado": 1}]})
        report = validate(self.camp)
        paths = [f.path for f in report.errors]
        self.assertIn("meccanica/dadi.json:tiri[0].successo_da", paths)
        self.assertIn("meccanica/dadi.json:tiri[1].nome", paths)
        self.assertIn("meccanica/dadi.json:tiri[1].tipo", paths)
        self.assertIn("meccanica/dadi.json:tiri[1].dado", paths)

    def test_under_profile_fields_and_samples_are_checked(self) -> None:
        self.write_json("meccanica/dadi.json", {"sistema": "x", "note": "", "tiri": [
            {"nome": "p", "tipo": "sotto", "dado": 100, "dadi_fissi": 1, "soglie": [{"nome": "a", "frazione": 2, "guida": ""}, {"nome": "a", "frazione": 0.5, "guida": ""}],
             "fallimento_critico": {"da": 101, "sotto_valore": None, "altrimenti_da": None}, "successo_critico_fino_a": 0,
             "esempi": [{"marcatore": "x | p | valore 50", "dadi": [20], "atteso": {"valore": 20, "esito": "fallito", "riuscito": None}}]}]})
        paths = [f.path for f in validate(self.camp).errors]
        for expected in ("meccanica/dadi.json:tiri[0].soglie[0].frazione", "meccanica/dadi.json:tiri[0].soglie[1].nome",
                         "meccanica/dadi.json:tiri[0].fallimento_critico", "meccanica/dadi.json:tiri[0].successo_critico_fino_a"):
            self.assertIn(expected, paths)
        self.assertNotIn("meccanica/dadi.json:tiri[0].esempi[0].atteso.esito", paths)  # i campioni si eseguono solo a forma giusta
        self.write_json("meccanica/dadi.json", {"sistema": "x", "note": "", "tiri": [
            {"nome": "p", "tipo": "sotto", "dado": 100, "dadi_fissi": 1, "soglie": [], "fallimento_critico": None, "successo_critico_fino_a": None,
             "esempi": [{"marcatore": "x | p | valore 50", "dadi": [20], "atteso": {"valore": 20, "esito": "fallito", "riuscito": None}}]}]})
        self.assertEqual([f.path for f in validate(self.camp).errors], ["meccanica/dadi.json:tiri[0].esempi[0].atteso.esito"])

    def test_keep_and_explode_fields_are_checked(self) -> None:
        self.write_json("meccanica/dadi.json", {"sistema": "x", "note": "", "tiri": [
            {"nome": "a", "tipo": "somma", "dado": 6, "dadi_fissi": 1, "tieni": {"quanti": 0, "quale": "alto"}, "esiti_su": "dado", "esiti": [], "esplode_su": 1},
            {"nome": "b", "tipo": "somma", "dado": 6, "dadi_fissi": 1, "tieni": {"quanti": 1, "quale": "medio"}, "esiti_su": "x", "esplode_su": 7}]})
        paths = [f.path for f in validate(self.camp).errors]
        for expected in ("meccanica/dadi.json:tiri[0].tieni", "meccanica/dadi.json:tiri[0].esiti", "meccanica/dadi.json:tiri[0].esplode_su",
                         "meccanica/dadi.json:tiri[1].tieni.quale", "meccanica/dadi.json:tiri[1].esiti_su", "meccanica/dadi.json:tiri[1].esplode_su"):
            self.assertIn(expected, paths)

    def test_bands_that_do_not_tile_are_an_error_with_the_path(self) -> None:
        self.write_json("meccanica/dadi.json", {"sistema": "x", "note": "", "tiri": [
            {"nome": "d20", "tipo": "somma", "dado": 20, "dadi_fissi": 1, "esiti": [
                {"nome": "fallito", "da_margine": None, "a_margine": -1, "riuscito": False, "guida": ""},
                {"nome": "riuscito", "da_margine": 1, "a_margine": None, "riuscito": True, "guida": ""}]}]})
        report = validate(self.camp)
        self.assertEqual([f.path for f in report.errors], ["meccanica/dadi.json:tiri[0].esiti[1].da_margine"])

    def test_special_dice_path_missing_from_sheet_is_an_error(self) -> None:
        self.write_json("meccanica/dadi.json", {"sistema": "x", "note": "", "tiri": [
            {"nome": "riserva", "tipo": "successi", "dado": 10, "dadi_fissi": None, "successo_da": 6,
             "speciali": {"nome": "fame", "quanti_da": "stato.fame", "sostituiscono": True}}]})
        report = validate(self.camp)
        self.assertEqual([f.title for f in report.errors], ["percorso assente dalla scheda"])
        self.assertEqual(report.errors[0].path, "meccanica/dadi.json:tiri[0].speciali.quanti_da")
        # con il percorso nella scheda il profilo e' valido
        sheet = json.loads((self.camp.root / "schede" / "kael.json").read_text(encoding="utf-8"))
        sheet["stato"]["fame"] = 2
        self.write_json("schede/kael.json", sheet)
        self.assertEqual(validate(self.camp).errors, [])

    def test_rules_about_dice_without_profile_is_a_recommendation(self) -> None:
        (self.camp.root / "meccanica" / "dadi.json").unlink()
        self.assertIn("dadi non ancora ricavati", self.titles("recommendation"))


class TestSheets(Base):
    def test_broken_sheet_is_an_error(self) -> None:
        self.write_json("schede/kael.json", "{non json")
        self.assertIn("scheda illeggibile", self.titles("error"))

    def test_hand_written_totals_are_an_error(self) -> None:
        sheet = json.loads((self.camp.root / "schede" / "kael.json").read_text(encoding="utf-8"))
        sheet["esperienza"] = {"registro": [], "disponibili": 7}
        self.write_json("schede/kael.json", sheet)
        self.assertIn("totali scritti a mano", self.titles("error"))

    def test_ledger_rows_are_checked(self) -> None:
        sheet = json.loads((self.camp.root / "schede" / "kael.json").read_text(encoding="utf-8"))
        sheet["esperienza"] = {"registro": [
            {"quando": "x", "sessione": 1, "via": "sessione", "punti": 1, "motivo": "a"},
            {"quando": "x", "sessione": 1, "via": "sessione", "punti": 1, "motivo": "b"},
            {"quando": "x", "sessione": 1, "via": "spesa", "punti": 5, "motivo": "c"},
            {"quando": "x", "sessione": 1, "via": "regalo", "punti": 1, "motivo": ""},
        ]}
        self.write_json("schede/kael.json", sheet)
        report = validate(self.camp)
        self.assertIn("punti di sessione assegnati due volte", [f.title for f in report.errors])
        self.assertIn("segno e via non concordano", [f.title for f in report.errors])
        self.assertIn("via sconosciuta", [f.title for f in report.warnings])
        self.assertIn("movimento senza motivo", [f.title for f in report.warnings])

    def test_overspent_ledger_is_an_error(self) -> None:
        sheet = json.loads((self.camp.root / "schede" / "kael.json").read_text(encoding="utf-8"))
        sheet["esperienza"] = {"registro": [{"quando": "x", "sessione": 1, "via": "spesa", "punti": -3, "motivo": "c"}]}
        self.write_json("schede/kael.json", sheet)
        self.assertIn("spesi piu' punti di quelli guadagnati", self.titles("error"))

    def test_text_only_sheet_is_a_recommendation(self) -> None:
        (self.camp.root / "schede" / "kael.json").unlink()
        self.assertIn("scheda solo testo", self.titles("recommendation"))


class TestState(Base):
    def test_broken_state_is_an_error(self) -> None:
        self.write_json("stato.json", "{non json")
        self.assertIn("stato illeggibile", self.titles("error"))

    def test_clocks_out_of_range_are_errors(self) -> None:
        self.write_json("stato.json", {"pg_attivo": "kael", "sessione": 1, "orologi": {
            "troppo": {"segmenti": 20, "pieni": 0, "conseguenza": "x"},
            "oltre": {"segmenti": 6, "pieni": 7, "conseguenza": "x"},
            "muto": {"segmenti": 4, "pieni": 1, "conseguenza": ""}}})
        report = validate(self.camp)
        self.assertEqual([f.title for f in report.errors], ["segmenti fuori misura", "riempimento fuori misura"])
        self.assertIn("orologio senza conseguenza", [f.title for f in report.recommendations])

    def test_unknown_active_pg_and_bad_session_number(self) -> None:
        self.write_json("stato.json", {"pg_attivo": "nessuno", "sessione": 0})
        report = validate(self.camp)
        self.assertIn("personaggio attivo inesistente", [f.title for f in report.warnings])
        self.assertIn("numero di sessione non valido", [f.title for f in report.errors])

    def test_consolidated_beyond_played_is_an_error(self) -> None:
        self.play(2)
        self.write_json("stato.json", {"pg_attivo": "kael", "memoria": {"kael": 5}})
        report = validate(self.camp)
        self.assertIn("consolidato oltre il giocato", [f.title for f in report.errors])
        self.write_json("stato.json", {"pg_attivo": "kael", "memoria": {"kael": 2}})
        self.assertNotIn("consolidato oltre il giocato", self.titles("error"))


class TestLineage(Base):
    def test_quarantine_and_vanished_sections_are_warnings(self) -> None:
        self.write_json("preparazione/paternita.json", {"schema": 1, "manuali": {}, "glossario": {}, "dadi": {}, "sezioni": {
            "meccanica/regole.md": {
                "sistema": {"titolo": "Sistema", "risoluzione": [{"esito": "quarantena", "perche": "passo non trovato"}]},
                "sparita": {"titolo": "Sezione sparita"}},
            "meccanica/perduto.md": {"x": {"titolo": "X"}}}})
        report = validate(self.camp)
        titles = [f.title for f in report.warnings]
        self.assertIn("sezione in quarantena", titles)
        self.assertIn("sezione registrata ma assente", titles)
        self.assertIn("file registrato ma sparito", titles)
        self.assertEqual(report.errors, [])

    def test_other_schema_version_is_a_warning(self) -> None:
        self.write_json("preparazione/paternita.json", {"schema": 99})
        self.assertIn("registro della paternita' illeggibile o di un'altra versione", self.titles("warning"))


class TestPlay(Base):
    def test_played_campaign_without_scene_threads_or_memory_gets_recommendations(self) -> None:
        self.play(4)
        titles = self.titles("recommendation")
        self.assertIn("scena in corso assente", titles)
        self.assertIn("fili aperti assenti", titles)
        self.assertIn("memoria da consolidare", titles)
        self.assertEqual(validate(self.camp).errors, [])

    def test_fresh_campaign_gets_no_play_recommendations(self) -> None:
        self.assertEqual(self.titles("recommendation"), [])

    def test_missing_rules_is_a_recommendation(self) -> None:
        self.camp.file("meccanica/regole.md").write("# Regole\n")
        self.assertIn("campagna da preparare", self.titles("recommendation"))


class TestAll(Base):
    def test_validate_all_and_hook_text_only_speak_when_needed(self) -> None:
        shutil.copytree(self.camp.root, self.tmp / "altra", ignore=shutil.ignore_patterns("sessioni"))
        (self.tmp / ".nascosta").mkdir()
        reports = validate_all(self.tmp)
        self.assertEqual([r.campaign for r in reports], ["altra", "camp"])
        self.assertEqual(hook_text(reports), "")
        Campaign(self.tmp / "altra").glossary.upsert(GlossaryEntry("Vecchia rovina", "luogo", "Non esiste piu'", "rovine.md"))
        self.play(4)  # solo consigli: l'hook non li stampa
        text = hook_text(validate_all(self.tmp))
        self.assertIn("1 campagna con errori o avvisi", text)
        self.assertIn("Verifica di altra", text)
        self.assertNotIn("Verifica di camp", text)
        self.assertNotIn("CONSIGLI", text)


if __name__ == "__main__":
    unittest.main()
