"""Test del golden dataset: i dati sono coerenti e gli strumenti di misura misurano davvero.

Qui non si giudica la preparazione: si controlla che il metro sia dritto. Un caso con uno schema
che non compare in nessun manuale, o una sezione "lunga" che in realta' sta nel taglio, darebbe
misure false senza che nessuno se ne accorga.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from golden import (  # noqa: E402
    apply_today, check_case, check_glossary, cut_for, key_of, load_cases, load_manual, norm,
    score_candidates, score_relations, score_state, sections_of, snapshot,
)
from master import Campaign  # noqa: E402
from master.context import RULE_CHARS  # noqa: E402
from master.prepare import TARGET_FILES  # noqa: E402

RELATIONS = {"nuova", "identica", "estende", "sostituisce", "variante", "contraddice"}
CATEGORIES = {"nuova", "identica", "estende", "sostituzione dichiarata", "sostituzione non dichiarata",
              "variante opzionale", "contraddizione senza segnale", "titolo diverso", "oltre il taglio", "falso allarme"}


class TestGoldenDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = load_manual("base")
        cls.expansion = load_manual("espansione")
        cls.cases = load_cases()
        cls.base_sections = sections_of(cls.base)
        cls.new_sections = sections_of(cls.expansion)

    def test_manuals_load_as_extracted_blocks(self) -> None:
        self.assertEqual(self.base["meta"]["anno"], 2019)
        self.assertEqual(self.expansion["meta"]["anno"], 2022)
        self.assertEqual(self.base["meta"]["edizione"], self.expansion["meta"]["edizione"])
        self.assertEqual(len(self.base_sections), 23)
        self.assertEqual(len(self.new_sections), 26)
        for manual in (self.base, self.expansion):
            self.assertGreater(len(manual["blocchi"]), 2)
            for block in manual["blocchi"]:
                self.assertTrue(block["sezioni"] and block["glossario"], block["pagine"])
                for item in block["sezioni"] + block["glossario"]:
                    self.assertIn(item["file"], TARGET_FILES)

    def test_every_expansion_section_has_exactly_one_case(self) -> None:
        ids = [c["id"] for c in self.cases["casi"]] + [g["id"] for g in self.cases["glossario"]]
        self.assertEqual(len(ids), len(set(ids)))
        covered = [key_of(c["nuova"]) for c in self.cases["casi"]]
        self.assertEqual(sorted(covered), sorted(self.new_sections))

    def test_references_exist(self) -> None:
        for case in self.cases["casi"]:
            self.assertIn(key_of(case["nuova"]), self.new_sections, case["id"])
            if case["esistente"]:
                self.assertIn(key_of(case["esistente"]), self.base_sections, case["id"])
            self.assertTrue(key_of(case["vigente"]) in self.base_sections or key_of(case["vigente"]) in self.new_sections, case["id"])
            for other in case.get("candidati_falsi", []) + case.get("altrove", []):
                self.assertIn(key_of(other), self.base_sections, case["id"])

    def test_every_category_and_relation_is_covered(self) -> None:
        self.assertEqual({c["categoria"] for c in self.cases["casi"]}, CATEGORIES)
        self.assertEqual({t for c in self.cases["casi"] for t in c["tipi"]}, RELATIONS)
        self.assertTrue(any(c.get("tocca_dadi") for c in self.cases["casi"]))

    def test_patterns_come_from_the_manuals(self) -> None:
        """Cio' che deve restare sta in uno dei due testi; cio' che deve sparire sta nel base e non nell'espansione."""
        for case in self.cases["casi"]:
            new = norm(self.new_sections[key_of(case["nuova"])])
            old = norm(self.base_sections[key_of(case["esistente"])]) if case["esistente"] else ""
            for pattern in case.get("deve", []):
                self.assertTrue(re.search(pattern, new) or re.search(pattern, old), f"{case['id']}: {pattern}")
            for pattern in case.get("non_deve", []):
                self.assertTrue(re.search(pattern, old), f"{case['id']}: {pattern} non e' nel manuale base")
                self.assertFalse(re.search(pattern, new), f"{case['id']}: {pattern} e' anche nell'espansione")
            for first, second in case.get("ordine", []):
                self.assertTrue(re.search(first, old) and re.search(second, new), case["id"])

    def test_long_section_really_exceeds_the_cut(self) -> None:
        case = next(c for c in self.cases["casi"] if c["categoria"] == "oltre il taglio")
        body = self.base_sections[key_of(case["esistente"])]
        self.assertGreater(len(body), cut_for(case["esistente"]["file"]))
        self.assertEqual(cut_for(case["esistente"]["file"]), RULE_CHARS)
        for pattern in case["non_deve"]:  # la riga da correggere sta nella parte che il master legge
            self.assertTrue(re.search(pattern, norm(body[:RULE_CHARS])))

    def test_exact_reprise_is_word_for_word(self) -> None:
        key = ("meccanica/regole.md", "Prove")
        self.assertEqual(self.base_sections[key], self.new_sections[key])

    def test_glossary_cases_are_real_collisions(self) -> None:
        def entries(manual):
            return {g["term"]: g for b in manual["blocchi"] for g in b["glossario"]}

        old, new = entries(self.base), entries(self.expansion)
        for expected in self.cases["glossario"]:
            before, after = old[expected["term"]], new[expected["term"]]
            self.assertEqual(before["file"], expected["file"])
            self.assertTrue(re.search(expected["descrizione"], norm(before["description"])))
            self.assertTrue(after["file"] != expected["file"] or not re.search(expected["descrizione"], norm(after["description"])))

    def test_rejected_manual_is_another_edition(self) -> None:
        for name in self.cases["manuali_rifiutati"]:
            other = load_manual(name)
            self.assertNotEqual(other["meta"]["edizione"], self.base["meta"]["edizione"])
            self.assertTrue(set(sections_of(other)) & set(self.base_sections))


class TestGoldenHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.camp = Campaign.create(self.tmp / "camp")
        self.base = load_manual("base")
        self.expansion = load_manual("espansione")
        self.cases = load_cases()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def case(self, **kw) -> dict:
        ref = {"file": "meccanica/regole.md", "titolo": "Salto"}
        return {"id": "X", "nuova": ref, "esistente": ref, "vigente": ref, **kw}

    def test_check_case_reads_facts_not_layout(self) -> None:
        self.camp.file("meccanica/regole.md").set_section("Salto", "Si tira **2d6 + Riflessi**.\n\n| Difficoltà | 9 |")
        self.assertEqual(check_case(self.camp, self.case(deve=["2d6 \\+ riflessi", "\\|\\s*9\\b"], non_deve=["1d6"]), {}), [])
        why = check_case(self.camp, self.case(deve=["3d6"], non_deve=["2d6"]), {})
        self.assertEqual(len(why), 2)
        missing = dict(self.case(), vigente={"file": "meccanica/regole.md", "titolo": "Non esiste"})
        self.assertIn("manca la sezione", check_case(self.camp, missing, {})[0])

    def test_check_case_honours_the_cut_and_the_order(self) -> None:
        self.camp.file("meccanica/regole.md").set_section("Salto", "Regola standard: 2d6.\n\n" + "x" * RULE_CHARS + "\n\nErrata: 3d6.")
        self.assertEqual(check_case(self.camp, self.case(deve=["3d6"]), {}), [])
        self.assertTrue(check_case(self.camp, self.case(deve=["3d6"], entro_taglio=True), {}))
        self.assertEqual(check_case(self.camp, self.case(ordine=[["2d6", "3d6"]]), {}), [])
        self.assertTrue(check_case(self.camp, self.case(ordine=[["3d6", "2d6"]]), {}))

    def test_setting_may_keep_the_past_below_the_cut(self) -> None:
        ref = {"file": "ambientazione/mondo.md", "titolo": "Porto"}
        self.camp.file(ref["file"]).set_section("Porto", "Oggi il porto ha nove moli.\n\n" + "y" * 800 + "\n\nUn tempo i moli erano sette.")
        case = {"id": "X", "nuova": ref, "esistente": ref, "vigente": ref, "deve": ["nove moli"], "entro_taglio": True, "non_deve": ["erano sette"]}
        self.assertEqual(check_case(self.camp, case, {}), [])

    def test_non_cresce_and_altrove(self) -> None:
        f = self.camp.file("meccanica/regole.md")
        f.set_section("Salto", "Testo. " * 10)
        f.set_section("Corsa", "Qui e' finito il Manometro.")
        ref = {"file": "meccanica/regole.md", "titolo": "Salto"}
        self.assertTrue(check_case(self.camp, self.case(non_cresce=True), {key_of(ref): "Testo. " * 5}))
        self.assertEqual(check_case(self.camp, self.case(non_cresce=True), {key_of(ref): "Testo. " * 9}), [])
        other = {"file": "meccanica/regole.md", "titolo": "Corsa", "non_deve": ["manometro"]}
        self.assertTrue(check_case(self.camp, self.case(altrove=[other]), {}))

    def test_check_glossary(self) -> None:
        from master.glossary import GlossaryEntry

        expected = self.cases["glossario"][0]
        self.assertIn("manca la voce", check_glossary(self.camp, expected)[0])
        self.camp.glossary.upsert(GlossaryEntry(expected["term"], "png", "Maestra della Gilda", expected["file"]))
        self.assertEqual(check_glossary(self.camp, expected), [])
        self.camp.glossary.upsert(GlossaryEntry(expected["term"], "png", "Citata altrove", "ambientazione/mondo.md"))
        self.assertEqual(len(check_glossary(self.camp, expected)), 2)

    def test_score_candidates_and_relations_against_the_oracle(self) -> None:
        cases = self.cases
        perfect = {(key_of(c["nuova"]), key_of(c["esistente"])) for c in cases["casi"] if c["esistente"]}
        score = score_candidates(perfect, cases)
        self.assertEqual((score["trovati"], score["mancanti"], score["in_piu"]), (score["attesi"], [], 0))
        self.assertEqual(score_candidates(set(), cases)["trovati"], 0)
        alarm = next(c for c in cases["casi"] if c.get("candidati_falsi"))
        noisy = perfect | {(key_of(alarm["nuova"]), key_of(alarm["candidati_falsi"][0]))}
        self.assertEqual((score_candidates(noisy, cases)["in_piu"], score_candidates(noisy, cases)["falsi_allarmi_presi"]), (1, 1))

        right = {c["id"]: c["tipi"][-1] for c in cases["casi"]}  # ogni tipo accettato vale, non solo il primo
        self.assertEqual(score_relations(right, cases)["giusti"], len(cases["casi"]))
        self.assertEqual(score_relations({}, cases)["giusti"], 0)
        self.assertEqual(score_relations(dict(right, D1="estende"), cases)["sbagliati"], {"D1": "estende"})

    def test_today_runs_on_the_dataset_and_new_material_arrives(self) -> None:
        apply_today(self.camp, self.base)
        apply_today(self.camp, self.expansion)
        result = score_state(self.camp, self.cases, self.base)
        self.assertEqual(set(result), {c["id"] for c in self.cases["casi"]} | {g["id"] for g in self.cases["glossario"]})
        for case in self.cases["casi"]:
            if case["categoria"] == "nuova":  # il materiale nuovo e' la parte che oggi funziona gia'
                self.assertEqual(result[case["id"]], [], case["id"])

    def test_snapshot_ignores_layout_and_sees_content(self) -> None:
        apply_today(self.camp, self.base)
        before = snapshot(self.camp)
        f = self.camp.file("meccanica/regole.md")
        f.set_section("Iniziativa", f.get_section("Iniziativa").replace("tira 1d6", "tira  **1d6**"))
        self.assertEqual(snapshot(self.camp), before)
        f.set_section("Iniziativa", "Si tira 2d6.")
        self.assertNotEqual(snapshot(self.camp), before)


if __name__ == "__main__":
    unittest.main()
