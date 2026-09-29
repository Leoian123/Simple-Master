"""Test della preparazione senza rete: PDF minimale generato a mano + client finto."""

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

from master import Campaign  # noqa: E402
from master.prepare import Chunk, Preparer, load_chunks, parse_json_object, source_key  # noqa: E402


def make_pdf(path: Path, pages: list[str]) -> None:
    """Scrive un PDF minimale con una riga di testo per pagina (font standard Helvetica)."""
    objs: list[bytes] = []
    n_pages = len(pages)
    page_ids = [4 + 2 * i for i in range(n_pages)]
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, text in enumerate(pages):
        pid = page_ids[i]
        stream = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode("latin-1")
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {pid + 1} 0 R >>".encode()
        )
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


def _block(**kw):
    return SimpleNamespace(**kw)


class FakePrepClient:
    """Risponde con JSON di estrazione per i blocchi e con Markdown per il consolidamento."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=100, output_tokens=50)
        user_text = kwargs["messages"][0]["content"]
        if isinstance(user_text, list):
            user_text = " ".join(c.get("text", "") for c in user_text)
        if "Sezioni di `" in user_text:  # consolidamento: restituisce il file con una nota in coda
            original = user_text.split(":\n\n", 1)[1]
            return SimpleNamespace(stop_reason="end_turn", usage=usage,
                                   content=[_block(type="text", text=original.rstrip() + "\n\nConsolidato.\n")])
        first = "pagine 1-" in user_text and "Combattimento" not in self._glossary_seen(user_text)
        data = {
            "riassunto": "Regole base di combattimento." if first else "Segue il combattimento e un luogo.",
            "sezioni": [
                {"file": "meccanica/regole.md", "titolo": "Combattimento", "testo": "Iniziativa: d20 + Agilita'." if first else "Danni: arma + Forza."},
            ] + ([] if first else [{"file": "ambientazione/mondo.md", "titolo": "Velmora", "testo": "Ha un nuovo quartiere: le Docce."}]),
            "glossario": [
                {"term": "Combattimento", "kind": "regola", "description": "Iniziativa e danni", "file": "meccanica/regole.md"},
            ] + ([] if first else [{"term": "Le Docce", "kind": "luogo", "description": "Quartiere di Velmora", "file": "ambientazione/mondo.md"}]),
        }
        return SimpleNamespace(stop_reason="end_turn", usage=usage,
                               content=[_block(type="text", text="Ecco il JSON:\n" + json.dumps(data, ensure_ascii=False))])

    @staticmethod
    def _glossary_seen(user_text: str) -> str:
        return user_text.split("Blocco:")[0]

    def extraction_calls(self) -> int:
        return sum(1 for c in self.calls if "Sezioni di `" not in str(c["messages"][0]["content"]) and "Voci attuali" not in str(c["messages"][0]["content"])
                   and "PROFILO DEI DADI" not in str(c.get("system")))

    def consolidation_calls(self) -> int:
        return sum(1 for c in self.calls if "Sezioni di `" in str(c["messages"][0]["content"]))


class FakeBatchClient(FakePrepClient):
    """Come FakePrepClient, ma con Batch API e count_tokens finti."""

    def __init__(self, fail_first_poll: bool = False) -> None:
        super().__init__()
        self.batches_created: list[list[dict]] = []
        self.polls = 0
        self.fail_first_poll = fail_first_poll
        self.messages.batches = SimpleNamespace(create=self._bcreate, retrieve=self._bretrieve, results=self._bresults)
        self.messages.count_tokens = lambda **kw: SimpleNamespace(input_tokens=1300 + len(str(kw["messages"][0]["content"])) // 4)

    def _bcreate(self, requests):
        self.batches_created.append(list(requests))
        return SimpleNamespace(id=f"batch_{len(self.batches_created)}", processing_status="in_progress")

    def _bretrieve(self, batch_id):
        self.polls += 1
        if self.fail_first_poll and self.polls == 1:
            raise ConnectionError("rete caduta durante l'attesa")
        n = len(self.batches_created[-1])
        ended = self.polls >= 2
        return SimpleNamespace(processing_status="ended" if ended else "in_progress",
                               request_counts=SimpleNamespace(succeeded=n if ended else 0, errored=0))

    def _bresults(self, batch_id):
        for req in self.batches_created[-1]:
            msg = self._create(**req["params"])
            yield SimpleNamespace(custom_id=req["custom_id"], result=SimpleNamespace(type="succeeded", message=msg))


class TestChunking(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pdf_text_chunks_by_chars(self) -> None:
        pdf = self.tmp / "m.pdf"
        make_pdf(pdf, [f"Pagina numero {i} del manuale di prova con testo" for i in range(1, 7)])
        chunks = load_chunks(pdf, mode="testo", chars_per_chunk=100)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0].first_page, 1)
        self.assertEqual(chunks[-1].last_page, 6)
        self.assertIn("Pagina numero 1", chunks[0].text)
        self.assertEqual([c.index for c in chunks], list(range(1, len(chunks) + 1)))

    def test_pdf_native_chunks_and_page_range(self) -> None:
        pdf = self.tmp / "m.pdf"
        make_pdf(pdf, [f"p{i}" for i in range(1, 8)])
        chunks = load_chunks(pdf, mode="pdf", pages="2-6", pages_per_chunk=2)
        self.assertEqual([(c.first_page, c.last_page) for c in chunks], [(2, 3), (4, 5), (6, 6)])
        self.assertTrue(all(c.pdf_b64 for c in chunks))

    def test_scanned_pdf_warning(self) -> None:
        pdf = self.tmp / "s.pdf"
        make_pdf(pdf, ["", "", "x"])
        warnings: list[str] = []
        load_chunks(pdf, mode="testo", warnings=warnings)
        self.assertTrue(any("scansionato" in w for w in warnings))

    def test_text_source(self) -> None:
        src = self.tmp / "note.md"
        src.write_text("a" * 250, encoding="utf-8")
        self.assertEqual(len(load_chunks(src, chars_per_chunk=100)), 3)

    def test_parse_json_tolerant(self) -> None:
        self.assertEqual(parse_json_object('ecco: {"a": 1} fine')["a"], 1)
        with self.assertRaises(ValueError):
            parse_json_object("niente")

    def test_source_key_depends_on_content_not_name(self) -> None:
        a, b, c = self.tmp / "a.pdf", self.tmp / "b.pdf", self.tmp / "c.pdf"
        make_pdf(a, ["uno"]); make_pdf(b, ["uno"]); make_pdf(c, ["due"])
        self.assertEqual(source_key(a).split("-")[0], source_key(b).split("-")[0])
        self.assertNotEqual(source_key(a).split("-")[0], source_key(c).split("-")[0])
        self.assertTrue(source_key(a).endswith("-a"))


class TestPreparer(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "camp"
        shutil.copytree(ROOT / "campaigns" / "esempio", self.root)
        self.camp = Campaign(self.root)
        self.cache = self.tmp / "cache"
        self.pdf = self.tmp / "manuale.pdf"
        make_pdf(self.pdf, [f"Pagina {i} con regole di combattimento e luoghi" for i in range(1, 5)])

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def prep(self, client, campaign: Campaign | None = None, **kw) -> Preparer:
        return Preparer(campaign or self.camp, client=client, cache_root=self.cache, **kw)

    def cached_blocks(self) -> list[Path]:
        return sorted(self.cache.glob("*/*/blocco_*.json"))

    def test_run_merges_sections_glossary_and_source_index(self) -> None:
        client = FakePrepClient()
        prep = self.prep(client, effort="low")
        import master.prepare as mod
        orig = mod.load_chunks
        mod.load_chunks = lambda path, **kw: orig(path, chars_per_chunk=60, **{k: v for k, v in kw.items() if k != "chars_per_chunk"})
        try:
            report = prep.run(str(self.pdf), consolidate=True, progress=lambda s: None)
            self._check_first_run(client, report)
            # seconda esecuzione con la stessa suddivisione: nessuna chiamata di estrazione
            calls_before = len(client.calls)
            report2 = self.prep(client).run(str(self.pdf), consolidate=False, tidy=False, progress=lambda s: None)
        finally:
            mod.load_chunks = orig
        self.assertEqual(report2.chunks_from_cache, report2.chunks)
        self.assertEqual(len(client.calls), calls_before)
        # con una suddivisione diversa (pagine diverse) la cache non viene riusata
        report3 = self.prep(client).run(str(self.pdf), consolidate=False, tidy=False, progress=lambda s: None)
        self.assertEqual(report3.chunks_from_cache, 0)

    def _check_first_run(self, client, report) -> None:
        self.assertEqual(report.chunks, 2)
        self.assertIn("meccanica/regole.md", report.files_touched)
        self.assertIn("ambientazione/mondo.md", report.files_touched)
        self.assertIn("fonte-manuale.md", report.files_touched)
        first = client.calls[0]
        self.assertEqual(first["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(first["output_config"]["effort"], "low")
        self.assertIn("Glossario della campagna finora", first["messages"][0]["content"][0]["text"])
        self.assertIn("Kael", first["messages"][0]["content"][0]["text"])
        self.assertIn("Combattimento", client.calls[1]["messages"][0]["content"][0]["text"])
        regole = self.camp.file("meccanica/regole.md").read()
        self.assertIn("Consolidato.", regole)
        mondo = self.camp.file("ambientazione/mondo.md")
        self.assertEqual(mondo.outline().count("Velmora"), 1)
        self.assertIn("le Docce", mondo.get_section("Velmora"))
        self.assertEqual(self.camp.glossary.get("Le Docce").file, "ambientazione/mondo.md")
        self.assertEqual(self.camp.glossary.get("manuale").kind, "fonte")
        self.assertIn("Pagine 1-", self.camp.file("fonte-manuale.md").read())
        self.assertIn("Riordino glossario", report.summary())
        self.assertEqual(self.camp.glossary.entries()[0].kind, "regola")
        # cache condivisa fuori dalla campagna, per manuale e non per campagna
        self.assertEqual(len(self.cached_blocks()), 2)
        self.assertNotIn("preparazione", self.camp.index())
        self.assertFalse((self.root / "preparazione" / "manuale").exists())

    def test_same_manual_on_another_campaign_costs_nothing(self) -> None:
        """Il caso che costa euro: stesso manuale, seconda campagna. Deve riusare tutto."""
        client = FakePrepClient()
        lines: list[str] = []
        report1 = self.prep(client).run(str(self.pdf), consolidate=True, tidy=False, progress=lines.append)
        self.assertGreaterEqual(client.extraction_calls(), 1)
        self.assertTrue(any("Stima per" in l and "$" in l for l in lines))

        other = Campaign.create(self.tmp / "vampiri")  # campagna vuota, stessi file di destinazione
        calls_before, cons_before = len(client.calls), client.consolidation_calls()
        lines2: list[str] = []
        report2 = self.prep(client, campaign=other).run(str(self.pdf), consolidate=True, tidy=False, progress=lines2.append)
        self.assertEqual(report2.chunks_from_cache, report2.chunks)
        self.assertEqual(client.extraction_calls(), report1.chunks)  # nessuna nuova estrazione
        self.assertTrue(any("gia' in cache (nessun costo)" in l for l in lines2))
        self.assertIn("Combattimento", other.file("meccanica/regole.md").outline())
        self.assertIsNotNone(other.glossary.get("Combattimento"))
        # le uniche chiamate della seconda corsa sono i consolidamenti (i file di partenza qui differiscono
        # dalla prima campagna, che aveva gia' regole proprie); a parita' di contenuto neanche quelli
        self.assertEqual(len(client.calls) - calls_before, client.consolidation_calls() - cons_before)
        self.assertIn("senza costo", report2.summary())

    def test_headings_inside_a_section_do_not_split_it(self) -> None:
        from master.prepare import PrepReport
        prep = self.prep(FakePrepClient())
        data = {"riassunto": "x", "glossario": [], "sezioni": [
            {"file": "meccanica/regole.md", "titolo": "## Conflitti", "testo": "Intro.\n\n## Il turno\n\nTesto.\n\n# Danni\n\nAltro.\n\n### Dettaglio\n\nFine."}]}
        prep.merge(data, PrepReport(source="x"))
        regole = self.camp.file("meccanica/regole.md")
        self.assertIn("Conflitti", regole.outline())
        self.assertNotIn("Il turno", regole.outline())
        self.assertNotIn("Danni", regole.outline())
        body = regole.get_section("Conflitti")
        self.assertIn("### Il turno", body)
        self.assertIn("### Danni", body)
        self.assertIn("### Dettaglio", body)
        self.assertTrue(body.endswith("Fine."))

    def test_rerun_on_same_campaign_does_not_duplicate(self) -> None:
        """Una corsa interrotta dopo la fusione e poi rilanciata non deve accodare di nuovo le stesse sezioni."""
        client = FakePrepClient()
        self.prep(client).run(str(self.pdf), consolidate=False, tidy=False, progress=lambda s: None)
        before = {n: self.camp.file(n).read() for n in ("meccanica/regole.md", "ambientazione/mondo.md")}
        report = self.prep(client).run(str(self.pdf), consolidate=True, tidy=False, progress=lambda s: None)
        self.assertEqual(report.chunks_from_cache, report.chunks)
        for name, text in before.items():
            self.assertEqual(self.camp.file(name).read(), text)
        self.assertEqual(report.merged_files, [])
        self.assertEqual(client.consolidation_calls(), 0)

    def test_consolidation_only_when_sections_were_merged(self) -> None:
        client = FakePrepClient()
        report = self.prep(client).run(str(self.pdf), consolidate=True, tidy=False, progress=lambda s: None)
        # un solo blocco: "Combattimento" e' nuova, niente si accoda -> nessuna chiamata di consolidamento
        self.assertEqual(client.consolidation_calls(), 0)
        self.assertGreaterEqual(report.consolidations_skipped, 1)
        self.assertIn("consolidamenti saltati", report.summary())

    def test_low_value_pages_are_skipped(self) -> None:
        from master.prepare import is_low_value_page

        toc = "\n".join(f"Capitolo {i} ........ {i * 7}" for i in range(1, 12))
        self.assertEqual(is_low_value_page(toc), "indice")
        self.assertEqual(is_low_value_page("x" * 20), "vuota")
        self.assertIsNone(is_low_value_page("Regola: " + "testo " * 60))
        pdf = self.tmp / "misto.pdf"
        long_text = "Regole di combattimento " * 12  # ~290 caratteri
        make_pdf(pdf, [long_text, "", long_text, "x"])
        stats: dict[str, int] = {}
        chunks = load_chunks(pdf, mode="testo", stats=stats)
        self.assertEqual(stats["pages_skipped"], 2)
        self.assertEqual((chunks[0].first_page, chunks[0].last_page), (1, 4))
        self.assertNotIn("--- pagina 2 ---", chunks[0].text)
        self.assertIn("--- pagina 3 ---", chunks[0].text)

    def test_batch_extraction_halves_cost_and_caches(self) -> None:
        client = FakeBatchClient()
        prep = self.prep(client, poll_seconds=0)
        import master.prepare as mod
        orig = mod.load_chunks
        mod.load_chunks = lambda path, **kw: orig(path, chars_per_chunk=60, **{k: v for k, v in kw.items() if k != "chars_per_chunk"})
        lines: list[str] = []
        try:
            report = prep.run(str(self.pdf), consolidate=False, tidy=False, progress=lines.append)
        finally:
            mod.load_chunks = orig
        self.assertEqual(report.chunks, 2)
        self.assertEqual(report.chunks_batched, 2)
        self.assertIn("in batch", report.summary())
        self.assertEqual(len(client.batches_created), 1)
        self.assertEqual(len(client.batches_created[0]), 2)
        self.assertTrue(all("output_config" in r["params"] for r in client.batches_created[0]))
        self.assertTrue(any("batch inviato" in l and "meta' prezzo" in l for l in lines))
        self.assertTrue(any("in batch a meta' prezzo" in l for l in lines))  # stima
        self.assertEqual(len(self.cached_blocks()), 2)
        self.assertFalse(list(self.cache.glob("*/*/batch-in-corso.json")))
        self.assertIn("Combattimento", self.camp.file("meccanica/regole.md").outline())
        self.assertIn("elaborati in batch", report.summary())

    def test_batch_is_resumed_after_interruption(self) -> None:
        client = FakeBatchClient(fail_first_poll=True)
        import master.prepare as mod
        orig = mod.load_chunks
        mod.load_chunks = lambda path, **kw: orig(path, chars_per_chunk=60, **{k: v for k, v in kw.items() if k != "chars_per_chunk"})
        try:
            with self.assertRaises(ConnectionError):
                self.prep(client, poll_seconds=0).run(str(self.pdf), consolidate=False, tidy=False, progress=lambda s: None)
            self.assertTrue(list(self.cache.glob("*/*/batch-in-corso.json")))  # batch ricordato
            lines: list[str] = []
            report = self.prep(client, poll_seconds=0).run(str(self.pdf), consolidate=False, tidy=False, progress=lines.append)
        finally:
            mod.load_chunks = orig
        self.assertEqual(len(client.batches_created), 1)  # nessun secondo batch, nessun nuovo costo
        self.assertTrue(any("riprendo il batch" in l for l in lines))
        self.assertEqual(report.chunks_batched, 2)

    def test_single_block_skips_batch(self) -> None:
        client = FakeBatchClient()
        report = self.prep(client, poll_seconds=0).run(str(self.pdf), consolidate=False, tidy=False, progress=lambda s: None)
        self.assertEqual(report.chunks, 1)
        self.assertEqual(client.batches_created, [])  # un blocco solo: chiamata diretta, niente attesa
        self.assertEqual(report.chunks_batched, 0)

    def test_consolidation_cache_by_content(self) -> None:
        client = FakePrepClient()
        prep = self.prep(client)
        from master.prepare import PrepReport
        regole = self.camp.file("meccanica/regole.md")
        regole.set_section("Combattimento", "Iniziativa: d20.\n\nIniziativa: d20 + Agilita'.")
        untouched = regole.get_section("Sistema")
        r1 = PrepReport(source="x")
        prep.consolidate_sections({"meccanica/regole.md": ["combattimento"]}, self.cache, r1, lambda s: None, use_batch=False)
        self.assertEqual(client.consolidation_calls(), 1)
        self.assertEqual(r1.consolidated_from_cache, 0)
        self.assertIn("Consolidato.", regole.get_section("Combattimento"))
        self.assertEqual(regole.get_section("Sistema"), untouched)  # le altre sezioni non si toccano
        # solo la sezione ripetuta viaggia verso l'API, non il file intero
        sent = client.calls[-1]["messages"][0]["content"]
        self.assertIn("## Combattimento", sent)
        self.assertNotIn("## Sistema", sent)
        # stesso contenuto in un'altra campagna: nessuna chiamata
        other = Campaign.create(self.tmp / "altra")
        other.file("meccanica/regole.md").set_section("Combattimento", "Iniziativa: d20.\n\nIniziativa: d20 + Agilita'.")
        r2 = PrepReport(source="x")
        self.prep(client, campaign=other).consolidate_sections({"meccanica/regole.md": ["combattimento"]}, self.cache, r2, lambda s: None, use_batch=False)
        self.assertEqual(client.consolidation_calls(), 1)
        self.assertEqual(r2.consolidated_from_cache, 1)
        self.assertEqual(other.file("meccanica/regole.md").get_section("Combattimento"), regole.get_section("Combattimento"))

    def test_consolidation_groups_go_in_batch(self) -> None:
        client = FakeBatchClient()
        prep = self.prep(client, poll_seconds=0)
        from master.prepare import PrepReport
        import master.prepare as mod
        regole = self.camp.file("meccanica/regole.md")
        regole.set_section("Combattimento", "A. " * 200)
        regole.set_section("Magia", "B. " * 200)
        old = mod.CONSOLIDATE_GROUP_CHARS
        mod.CONSOLIDATE_GROUP_CHARS = 700  # forza due gruppi
        lines: list[str] = []
        try:
            r = PrepReport(source="x")
            prep.consolidate_sections({"meccanica/regole.md": ["combattimento", "magia"]}, self.cache, r, lines.append)
        finally:
            mod.CONSOLIDATE_GROUP_CHARS = old
        self.assertEqual(len(client.batches_created), 1)
        self.assertEqual(len(client.batches_created[0]), 2)
        self.assertTrue(any("stima consolidamento" in l and "meta' prezzo" in l for l in lines))
        self.assertIn("Consolidato.", regole.get_section("Combattimento"))
        self.assertIn("Consolidato.", regole.get_section("Magia"))
        self.assertEqual(len(list((self.cache / "consolidati").glob("*.md"))), 2)

    def test_truncated_block_is_not_cached_and_is_redone(self) -> None:
        class TruncatingClient(FakePrepClient):
            def __init__(self) -> None:
                super().__init__()
                self.truncate = True

            def _create(self, **kwargs):
                if self.truncate and "Sezioni di `" not in str(kwargs["messages"][0]["content"]):
                    self.calls.append(kwargs)
                    return SimpleNamespace(stop_reason="max_tokens", usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                                           content=[_block(type="text", text='{"riassunto":"inizio","sezioni":[{"file":"meccanica/regole.md","titolo":"X","testo":"tronc')])
                return super()._create(**kwargs)

        client = TruncatingClient()
        report = self.prep(client).run(str(self.pdf), consolidate=False, tidy=False, progress=lambda s: None)
        self.assertTrue(any("troncata" in w for w in report.warnings))
        self.assertEqual(self.cached_blocks(), [])  # niente cache per il blocco fallito
        self.assertEqual(client.calls[0]["max_tokens"], 32000)
        client.truncate = False
        report2 = self.prep(client).run(str(self.pdf), consolidate=False, tidy=False, progress=lambda s: None)
        self.assertEqual(report2.chunks_from_cache, 0)
        self.assertEqual(len(self.cached_blocks()), report2.chunks)

    def test_empty_cache_from_old_runs_is_redone(self) -> None:
        client = FakePrepClient()
        prep = self.prep(client)
        prep.run(str(self.pdf), consolidate=False, tidy=False, progress=lambda s: None)
        blocks = self.cached_blocks()
        self.assertTrue(blocks)
        blocks[0].write_text('{"riassunto": "", "sezioni": [], "glossario": []}', encoding="utf-8")
        calls_before = len(client.calls)
        report = self.prep(client).run(str(self.pdf), consolidate=False, tidy=False, progress=lambda s: None)
        self.assertEqual(report.chunks_from_cache, len(blocks) - 1)
        self.assertEqual(len(client.calls), calls_before + 1)

    def test_create_message_prefers_streaming(self) -> None:
        from master.prepare import create_message

        class Stream:
            def __init__(self, **kw):
                self.kw = kw

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get_final_message(self):
                return SimpleNamespace(stop_reason="end_turn", content=[], streamed=self.kw["max_tokens"])

        streaming = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: Stream(**kw), create=lambda **kw: "NO"))
        self.assertEqual(create_message(streaming, max_tokens=5).streamed, 5)
        plain = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: "creato"))
        self.assertEqual(create_message(plain, max_tokens=5), "creato")

    def test_consolidate_keeps_file_when_output_suspicious(self) -> None:
        class ShortClient(FakePrepClient):
            def _create(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(stop_reason="max_tokens", usage=None, content=[_block(type="text", text="# x")])

        prep = self.prep(ShortClient())
        from master.prepare import PrepReport
        report = PrepReport(source="x")
        before = self.camp.file("ambientazione/mondo.md").read()
        prep.consolidate_sections({"ambientazione/mondo.md": ["velmora"]}, self.cache, report, lambda s: None, use_batch=False)
        self.assertEqual(self.camp.file("ambientazione/mondo.md").read(), before)
        self.assertTrue(report.warnings)
        self.assertEqual(list(self.cache.glob("consolidati/*")), [])  # un esito scartato non va in cache

    def test_consolidation_rejects_shrunk_section(self) -> None:
        class LossyClient(FakePrepClient):
            def _create(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(stop_reason="end_turn", usage=None,
                                       content=[_block(type="text", text="## Velmora\n\nCitta'.")])

        prep = self.prep(LossyClient())
        from master.prepare import PrepReport
        report = PrepReport(source="x")
        before = self.camp.file("ambientazione/mondo.md").get_section("Velmora")
        prep.consolidate_sections({"ambientazione/mondo.md": ["velmora"]}, self.cache, report, lambda s: None, use_batch=False)
        self.assertEqual(self.camp.file("ambientazione/mondo.md").get_section("Velmora"), before)
        self.assertTrue(any("non affidabile" in w for w in report.warnings))


if __name__ == "__main__":
    unittest.main()
