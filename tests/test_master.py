"""Test senza rete: classi file, glossario, ciclo a conversazione e costruttore di schede con un client finto."""

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

from master import Campaign, CampaignFile, ConversationEngine, GlossaryEntry, MasterEngine  # noqa: E402
from master.character import CharacterBuilder  # noqa: E402


PROMPT = "Sei il master di prova. Le regole stanno in `meccanica/`."


def conversation(camp, **kw) -> ConversationEngine:
    """Il ciclo a conversazione con tutti gli strumenti: quello che usa il costruttore di schede."""
    return ConversationEngine(camp, prompt=PROMPT, **kw)


class TempCampaign(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "camp"
        shutil.copytree(ROOT / "campaigns" / "esempio", self.root)
        self.camp = Campaign(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestFiles(TempCampaign):
    def test_sections_and_set_section(self) -> None:
        f = self.camp.file("ambientazione/npc.md")
        self.assertIn("Mira Sol", f.outline())
        self.assertEqual(f.set_section("Mira Sol", "Nuova scheda."), "updated")
        self.assertEqual(f.get_section("Mira Sol"), "Nuova scheda.")
        self.assertEqual(f.set_section("Dorian Vesk", "Capo della gilda."), "created")
        self.assertEqual(f.outline(), ["Mira Sol", "Dorian Vesk"])

    def test_set_section_keeps_following_sections(self) -> None:
        f = self.camp.file("ambientazione/mondo.md")
        f.set_section("Velmora", "Riscritta.")
        self.assertEqual(f.outline(), ["Velmora", "Faro Spento", "Gilda delle Maree"])
        self.assertTrue(f.get_section("Faro Spento").startswith("Faro sulla scogliera"))

    def test_append_and_replace(self) -> None:
        f = self.camp.file("ambientazione/diario.md")
        f.append("## Turno 1\n\nKael entra alla Lanterna Blu.")
        self.assertTrue(f.read().endswith("Kael entra alla Lanterna Blu.\n"))
        npc = self.camp.file("ambientazione/npc.md")
        npc.set_section("Guardia del molo", "Punti ferita: 12/12.")
        self.assertEqual(npc.replace("12/12", "9/12"), 1)
        self.assertEqual(npc.replace("assente", "x"), 0)
        self.assertIn("9/12", npc.get_section("Guardia del molo"))

    def test_new_file_append_creates(self) -> None:
        f = self.camp.file("meccanica/oggetti.md")
        self.assertFalse(f.exists())
        f.append("# Oggetti")
        self.assertEqual(f.read(), "# Oggetti\n")

    def test_resolve_rejects_escapes(self) -> None:
        for bad in ["../x.md", "/etc/passwd", "a.py", ""]:
            with self.assertRaises(ValueError):
                self.camp.resolve(bad)

    def test_atomic_write_leaves_no_temp(self) -> None:
        CampaignFile(self.root / "x.md", self.root).write("ciao")
        self.assertEqual([p.name for p in self.root.glob(".tmp-*")], [])

    def test_index_groups_by_folder(self) -> None:
        idx = self.camp.index()
        self.assertIn("Meccanica", idx)
        self.assertIn("Ambientazione", idx)
        self.assertIn("Schede", idx)
        self.assertIn("- schede/kael.md: Identità, Tratti", idx)
        self.assertLess(idx.index("Radice"), idx.index("Meccanica"))

    def test_create_empty_campaign_has_three_folders(self) -> None:
        camp = Campaign.create(self.tmp / "vuota")
        names = [f.name for f in camp.files()]
        self.assertIn("meccanica/creazione-pg.md", names)
        self.assertIn("ambientazione/diario.md", names)
        self.assertTrue((camp.root / "schede").is_dir())


class TestGlossaryTwoLevels(TempCampaign):
    def test_small_glossary_goes_whole_in_prompt(self) -> None:
        g = self.camp.glossary
        self.assertEqual(g.prompt_text(), g.text())

    def test_big_glossary_becomes_compact_index_with_lookup(self) -> None:
        g = self.camp.glossary
        for i in range(200):
            g.upsert(GlossaryEntry(f"Disciplina {i}", "regola", f"Potere vampirico numero {i} con descrizione lunga quanto basta", "meccanica/regole.md"))
        g.upsert(GlossaryEntry("Frenesia", "regola", "Perdita di controllo della Bestia", "meccanica/regole.md"))
        compact = g.prompt_text()
        self.assertLess(len(compact), len(g.text()) / 3)
        self.assertIn("lookup_glossary", compact)
        self.assertIn("**regola** in `meccanica/regole.md`:", compact)
        self.assertIn("Frenesia", compact)
        self.assertNotIn("Perdita di controllo", compact)  # le descrizioni restano fuori dal prompt
        self.assertEqual(g.lookup("frenesia")[0].description, "Perdita di controllo della Bestia")
        self.assertEqual(g.lookup("bestia controllo")[0].term, "Frenesia")  # cerca anche nelle descrizioni
        self.assertEqual(g.lookup("zzz"), [])
        # il tool restituisce le righe complete, e il ciclo a conversazione usa l'indice compatto
        from master.tools import ToolExecutor
        text, err = ToolExecutor(self.camp).execute("lookup_glossary", {"query": "Frenesia"})
        self.assertFalse(err)
        self.assertIn("Frenesia | regola | Perdita di controllo della Bestia | meccanica/regole.md", text)
        engine = conversation(self.camp, client=FakeClient())
        self.assertEqual(engine.system_blocks()[1]["text"], "# GLOSSARIO (primo riferimento)\n\n" + compact)


class TestTokenHygiene(TempCampaign):
    """Niente token sprecati: file grandi mai letti interi, costruttore senza l'indice del mondo."""

    def test_big_file_without_section_returns_the_outline(self) -> None:
        from master.tools import ToolExecutor

        big = self.camp.file("meccanica/regole.md")
        for i in range(60):
            big.set_section(f"Regola {i}", "Testo della regola numero %d. " % i * 30)
        ex = ToolExecutor(self.camp)
        text, err = ex.execute("read_file", {"name": "meccanica/regole.md"})
        self.assertFalse(err)
        self.assertIn("non lo leggo intero", text)
        self.assertIn("- Regola 59", text)
        self.assertLess(len(text), 3000)
        body, err = ex.execute("read_file", {"name": "meccanica/regole.md", "section": "Regola 7"})
        self.assertIn("regola numero 7", body)
        # un file piccolo si legge intero come prima
        small, err = ex.execute("read_file", {"name": "ambientazione/npc.md"})
        self.assertIn("Mira Sol", small)
        # titolo quasi giusto: arriva subito la sezione vicina, senza un giro di API in piu'
        msg, err = ex.execute("read_file", {"name": "meccanica/regole.md", "section": "Regola 5 bis"})
        self.assertFalse(err)
        self.assertIn("## Regola 5\n", msg)
        self.assertIn("regola numero 5", msg)
        self.assertLess(len(msg), 14_000)
        # titolo senza parenti: errore con l'elenco
        msg, err = ex.execute("read_file", {"name": "meccanica/regole.md", "section": "Zzz"})
        self.assertTrue(err)

    def test_builder_prompt_carries_only_creation_entries(self) -> None:
        g = self.camp.glossary
        for i in range(150):
            g.upsert(GlossaryEntry(f"Luogo {i}", "luogo", "Un luogo del mondo con una descrizione abbastanza lunga", "ambientazione/mondo.md"))
        g.upsert(GlossaryEntry("Clan Ventrue", "regola", "Discipline e debolezza del clan", "meccanica/creazione-pg.md"))
        builder = CharacterBuilder(self.camp, client=FakeBuilderClient())
        master = conversation(self.camp, client=FakeClient())  # lo stesso ciclo, senza filtro
        b_gloss, m_gloss = builder.system_blocks()[1]["text"], master.system_blocks()[1]["text"]
        self.assertIn("Clan Ventrue", b_gloss)
        self.assertIn("Kael", b_gloss)  # le schede restano
        self.assertNotIn("Luogo 42", b_gloss)
        self.assertIn("lookup_glossary", b_gloss)
        self.assertIn("Luogo 42", m_gloss)  # senza filtro l'indice e' completo
        self.assertLess(len(b_gloss), len(m_gloss) / 3)
        self.assertNotIn("ambientazione/", builder.system_blocks()[2]["text"])
        self.assertIn("meccanica/creazione-pg.md", builder.system_blocks()[2]["text"])

    def test_cost_accounts_for_cache_reads_and_writes(self) -> None:
        from master.models import cost_usd

        plain = cost_usd("claude-opus-5", 10_000, 1_000)
        self.assertAlmostEqual(plain, 0.05 + 0.025)
        cached = cost_usd("claude-opus-5", 1_000, 1_000, cache_read=9_000)
        self.assertAlmostEqual(cached, 0.005 + 0.0045 + 0.025)
        self.assertLess(cached, plain / 2)
        self.assertAlmostEqual(cost_usd("claude-opus-5", 0, 0, cache_write=10_000), 0.10)


class TestGlossary(TempCampaign):
    def test_entries_parse(self) -> None:
        terms = [e.term for e in self.camp.glossary.entries()]
        self.assertIn("Kael", terms)
        self.assertEqual(self.camp.glossary.get("mira sol").file, "ambientazione/npc.md")
        self.assertEqual(self.camp.glossary.get("Kael").file, "schede/kael.md")

    def test_upsert_and_remove(self) -> None:
        g = self.camp.glossary
        n = len(g.entries())
        self.assertEqual(g.upsert(GlossaryEntry("Dorian Vesk", "png", "Capo | della gilda", "ambientazione/npc.md")), "created")
        self.assertEqual(len(g.entries()), n + 1)
        self.assertEqual(g.get("Dorian Vesk").description, "Capo | della gilda")
        self.assertEqual(g.upsert(GlossaryEntry("dorian vesk", "png", "Aggiornato", "ambientazione/npc.md")), "updated")
        self.assertEqual(len(g.entries()), n + 1)
        self.assertTrue(g.remove("Dorian Vesk"))
        self.assertEqual(len(g.entries()), n)
        self.assertTrue(g.text().startswith("# Glossario"))


# --- client finto -----------------------------------------------------------


def _block(**kw):
    return SimpleNamespace(**kw)


class FakeClient:
    """Simula l'API: prima chiede di leggere npc.md, poi aggiorna i file, poi narra."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0)
        n = len(self.calls)
        if n == 1:
            return SimpleNamespace(
                stop_reason="tool_use",
                usage=usage,
                content=[_block(type="tool_use", id="t1", name="read_file", input={"name": "ambientazione/npc.md", "section": "Mira Sol"})],
            )
        if n == 2:
            return SimpleNamespace(
                stop_reason="tool_use",
                usage=usage,
                content=[
                    _block(type="tool_use", id="t2", name="append_file", input={"name": "ambientazione/diario.md", "text": "## Turno 1\n\nKael parla con Mira."}),
                    _block(type="tool_use", id="t3", name="upsert_glossary", input={"term": "Lanterna Blu", "kind": "luogo", "description": "Locanda del porto", "file": "ambientazione/mondo.md"}),
                    _block(type="tool_use", id="t4", name="read_file", input={"name": "manca.md"}),
                ],
            )
        return SimpleNamespace(
            stop_reason="end_turn",
            usage=usage,
            content=[_block(type="text", text="Mira ti guarda a lungo. Cosa le chiedi?")],
        )


class TestEngine(TempCampaign):
    def test_turn_reads_then_writes_then_narrates(self) -> None:
        client = FakeClient()
        engine = conversation(self.camp, client=client, effort="medium")
        result = engine.play("Entro alla Lanterna Blu e cerco Mira Sol.")

        self.assertEqual(result.text, "Mira ti guarda a lungo. Cosa le chiedi?")
        self.assertEqual(result.files_read, ["ambientazione/npc.md"])
        self.assertEqual(result.files_changed, ["ambientazione/diario.md", "glossario.md"])
        self.assertEqual(result.input_tokens, 30)
        self.assertEqual(len(client.calls), 3)

        # richiesta ben formata
        first = client.calls[0]
        self.assertEqual(first["model"], "claude-opus-5")
        self.assertEqual(first["extra_body"], {"output_config": {"effort": "medium"}})
        self.assertIn("GLOSSARIO", first["system"][1]["text"])
        self.assertIn("meccanica/", first["system"][0]["text"])
        # cache di un'ora: dopo il prompt, dopo glossario+indice, e sull'ultimo messaggio della conversazione
        hour = {"type": "ephemeral", "ttl": "1h"}
        self.assertEqual(first["system"][0]["cache_control"], hour)
        self.assertNotIn("cache_control", first["system"][1])
        self.assertEqual(first["system"][2]["cache_control"], hour)
        self.assertEqual(first["messages"][-1]["content"][-1]["cache_control"], hour)
        self.assertEqual(engine.messages[0], {"role": "user", "content": "Entro alla Lanterna Blu e cerco Mira Sol."})  # la copia salvata resta pulita
        marks = sum(1 for m in client.calls[2]["messages"] for b in (m["content"] if isinstance(m["content"], list) else []) if isinstance(b, dict) and "cache_control" in b)
        self.assertEqual(marks, 2)  # terza chiamata del turno: parole del giocatore + ultimo messaggio, mai di piu'
        # alla seconda chiamata le letture appena fatte NON si scrivono in cache: costerebbero doppio per nulla
        second = client.calls[1]["messages"]
        self.assertEqual(second[0]["content"][-1]["cache_control"], hour)
        self.assertFalse(any(isinstance(b, dict) and "cache_control" in b for b in second[-1]["content"]))
        self.assertGreater(result.cost_usd, 0)
        self.assertTrue(any(t["name"] == "read_file" for t in first["tools"]))
        self.assertFalse(any(t["name"] == "save_sheet" for t in first["tools"]))

        # i risultati dei tool tornano in un unico messaggio user, con l'errore marcato
        tool_msgs = [m for m in engine.messages if m["role"] == "user" and isinstance(m["content"], list)]
        second_results = tool_msgs[-1]["content"]
        self.assertEqual([r["tool_use_id"] for r in second_results], ["t2", "t3", "t4"])
        self.assertTrue(second_results[2]["is_error"])
        self.assertIn("Aggiunto a ambientazione/diario.md", second_results[0]["content"])

        # effetti sui file
        self.assertIn("Kael parla con Mira.", self.camp.file("ambientazione/diario.md").read())
        self.assertEqual(self.camp.glossary.get("Lanterna Blu").file, "ambientazione/mondo.md")
        # il prompt di sistema resta fermo per tutto il turno (una modifica a meta' invaliderebbe la
        # cache di tutta la conversazione); la voce nuova entra alla prossima fotografia
        self.assertEqual(client.calls[0]["system"], client.calls[2]["system"])
        self.assertNotIn("Locanda del porto", client.calls[2]["system"][1]["text"])
        self.assertIn("Locanda del porto", conversation(self.camp, client=FakeClient()).system_blocks()[1]["text"])
        # il giocato: un solo archivio per personaggio, trascrizione e ripresa insieme
        self.assertEqual(sorted(p.name for p in (self.root / "sessioni").iterdir()), ["kael.jsonl", "spese.jsonl"])  # il giocato e il registro delle spese, nient'altro
        turns = self.camp.read_turns("kael")
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["player"], "Entro alla Lanterna Blu e cerco Mira Sol.")
        self.assertIn("Cosa le chiedi?", turns[0]["master"])
        self.assertEqual(turns[0]["changed"], ["ambientazione/diario.md", "glossario.md"])

    def test_history_trim_keeps_pairs_intact(self) -> None:
        engine = conversation(self.camp, client=FakeClient(), max_history=4)
        engine.play("Primo turno.")
        engine.play("Secondo turno.")
        msgs = engine.messages
        self.assertEqual(msgs[0]["role"], "user")
        self.assertIsInstance(msgs[0]["content"], str)
        self.assertLessEqual(len(msgs), 6)


class FakeBuilderClient:
    """Legge le regole di creazione, poi salva la scheda."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0)
        n = len(self.calls)
        if n == 1:
            return SimpleNamespace(stop_reason="tool_use", usage=usage, content=[
                _block(type="tool_use", id="b1", name="read_file", input={"name": "meccanica/creazione-pg.md", "section": "Mestieri"})])
        if n == 2:
            return SimpleNamespace(stop_reason="tool_use", usage=usage, content=[
                _block(type="tool_use", id="b2", name="save_sheet", input={
                    "name": "Sera Vel", "summary": "Contrabbandiera di Velmora",
                    "data": {"identita": {"concetto": "Contrabbandiera"}, "stato": {"punti_ferita": {"attuale": 11, "massimo": 11}}},
                    "starting_xp": 2})])
        return SimpleNamespace(stop_reason="end_turn", usage=usage, content=[_block(type="text", text="Scheda salvata.")])


class TestCharacterBuilder(TempCampaign):
    def test_builder_reads_rules_and_saves_sheet(self) -> None:
        client = FakeBuilderClient()
        builder = CharacterBuilder(self.camp, client=client)
        result = builder.play("Voglio una contrabbandiera.")

        self.assertEqual(result.text, "Scheda salvata.")
        self.assertEqual(result.files_read, ["meccanica/creazione-pg.md"])
        self.assertEqual(result.files_changed, ["schede/sera-vel.json", "schede/sera-vel.md", "glossario.md"])
        data = json.loads((self.camp.root / "schede" / "sera-vel.json").read_text(encoding="utf-8"))
        self.assertEqual(data["nome"], "Sera Vel")
        self.assertEqual(data["stato"]["punti_ferita"], {"attuale": 11, "massimo": 11})  # numeri, non frasi
        self.assertEqual(data["esperienza"]["registro"][0]["via"], "creazione")
        sheet = self.camp.file("schede/sera-vel.md")  # la vista leggibile e' generata dai dati
        self.assertTrue(sheet.read().startswith("# Sera Vel\n"))
        self.assertEqual(sheet.get_section("Stato").strip(), "- **Punti ferita:** 11 / 11")
        self.assertIn("**Disponibili:** 2", sheet.get_section("Esperienza"))
        entry = self.camp.glossary.get("Sera Vel")
        self.assertEqual((entry.kind, entry.file), ("pg", "schede/sera-vel.md"))
        self.assertEqual(sorted(builder.sheets()), ["schede/kael.md", "schede/sera-vel.md"])
        # strumenti ristretti e nessuna trascrizione di sessione
        names = {t["name"] for t in client.calls[0]["tools"]}
        self.assertEqual(names, {"read_file", "list_files", "lookup_glossary", "save_sheet"})
        self.assertIn("creazione-pg.md", client.calls[0]["system"][0]["text"])
        # anche la creazione ha il suo archivio, separato dal gioco
        self.assertEqual(sorted(p.name for p in (self.root / "sessioni").iterdir()), ["_scheda.jsonl", "spese.jsonl"])  # il giocato e il registro delle spese, nient'altro


class TestPersistence(TempCampaign):
    def test_a_new_process_resumes_the_conversation(self) -> None:
        first = conversation(self.camp, client=FakeClient())
        first.play("Entro alla Lanterna Blu.")
        # "riavvio": un motore nuovo, memoria vuota, stessa campagna
        client = FakeClient()
        second = conversation(self.camp, client=client)
        self.assertEqual(second.messages, [])
        self.assertEqual([t["player"] for t in second.history()], ["Entro alla Lanterna Blu."])
        second.play("Le chiedo della taglia.")
        sent = client.calls[0]["messages"]
        self.assertEqual(sent[0], {"role": "user", "content": "Entro alla Lanterna Blu."})
        self.assertEqual(sent[1]["role"], "assistant")
        self.assertIn("Cosa le chiedi?", sent[1]["content"])
        self.assertEqual(sent[2]["content"][0]["text"], "Le chiedo della taglia.")
        self.assertEqual(second.turns, 2)
        self.assertEqual(len(self.camp.read_turns("kael")), 2)

    def test_resume_keeps_only_the_last_turns_in_context(self) -> None:
        for i in range(20):
            self.camp.append_turn("kael", f"azione {i}", f"esito {i}")
        engine = conversation(self.camp, client=FakeClient(), resume_turns=5)
        self.assertEqual(engine.resume(), 5)
        self.assertEqual(engine.messages[0]["content"], "azione 15")
        self.assertEqual(len(engine.history(limit=None)), 20)  # l'archivio resta intero

    def test_each_character_has_its_own_conversation(self) -> None:
        self.camp.file("schede/sera-vel.md").write("# Sera Vel\n\n## Stato\n\nOk.\n")
        self.camp.set_active_pg("kael")
        engine = conversation(self.camp, client=FakeClient())
        engine.play("Kael entra.")
        self.camp.set_active_pg("sera-vel")
        engine.reset_session()
        self.assertEqual(engine.history(), [])
        engine._client = FakeClient()
        engine.play("Sera osserva il porto.")
        self.assertEqual([t["player"] for t in self.camp.read_turns("sera-vel")], ["Sera osserva il porto."])
        self.assertEqual([t["player"] for t in self.camp.read_turns("kael")], ["Kael entra."])

    def test_new_session_archives_and_truncated_line_is_tolerated(self) -> None:
        engine = conversation(self.camp, client=FakeClient())
        engine.play("Primo.")
        with open(self.camp.save_path("kael"), "a", encoding="utf-8") as fh:
            fh.write('{"t": "2026-09-18T10:00:00", "player": "riga tronca')  # chiusura forzata a meta' scrittura
        self.assertEqual(len(self.camp.read_turns("kael")), 1)
        self.assertTrue(engine.new_session())
        self.assertEqual(engine.messages, [])
        self.assertEqual(self.camp.read_turns("kael"), [])
        self.assertEqual(len(list((self.root / "sessioni" / "archivio").glob("kael-*.jsonl"))), 1)
        self.assertFalse(engine.new_session())  # niente da archiviare

    def test_failed_turn_is_not_saved(self) -> None:
        class Boom:
            messages = SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(ConnectionError("giu'")))

        for engine in (conversation(self.camp, client=Boom()), MasterEngine(self.camp, client=Boom())):
            with self.assertRaises(ConnectionError):
                engine.play("Ciao.")  # nel gioco cade anche l'instradatore: il turno va avanti, poi cade il narratore
            self.assertEqual(self.camp.read_turns("kael"), [])


if __name__ == "__main__":
    unittest.main()
