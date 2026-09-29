"""La UI: menu principale, entrata e uscita dalla campagna, creazione, turno di gioco, via HTTP con client finto."""

from __future__ import annotations

import http.client
import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from master.ui import App, make_handler  # noqa: E402
from test_master import FakeClient  # noqa: E402


class TestPageScript(unittest.TestCase):
    def test_page_javascript_is_syntactically_valid(self) -> None:
        """Un apostrofo fuori posto in una stringa JS ferma l'intera pagina: lo si prende qui, non nel browser."""
        import re
        import shutil as sh
        import subprocess

        from master.ui import PAGE

        node = sh.which("node")
        if node is None:
            self.skipTest("node non disponibile")
        scripts = re.findall(r"<script>(.*?)</script>", PAGE, flags=re.S)
        self.assertEqual(len(scripts), 1)
        tmp = Path(tempfile.mkdtemp())
        try:
            f = tmp / "page.mjs"  # modulo: ammette await al livello piu' alto come fa la pagina? no: solo sintassi
            f.write_text("async function __page(){\n" + scripts[0] + "\n}\n", encoding="utf-8")
            result = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr[:600])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestUI(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.camps = self.tmp / "campaigns"
        shutil.copytree(ROOT / "campaigns" / "esempio", self.camps / "esempio")
        self.app = App(self.camps, None, client=FakeClient())
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict | list | str]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        conn.close()
        try:
            return resp.status, json.loads(raw)
        except json.JSONDecodeError:
            return resp.status, raw

    def test_starts_in_main_menu(self) -> None:
        status, page = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn('id="home"', page)
        self.assertIn('id="play" hidden', page)
        self.assertIn("Menu", page)
        status, state = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertIsNone(state["campaign"])
        self.assertEqual([c["name"] for c in state["campaigns"]], ["esempio"])
        self.assertTrue(state["campaigns"][0]["has_rules"])
        self.assertFalse(state["campaigns"][0]["preparing"])
        self.assertEqual(state["campaigns"][0]["sheets"], ["Kael"])
        self.assertEqual(state["campaigns"][0]["pg"], "Kael")  # unica scheda: e' il personaggio della campagna
        # dal menu non si gioca
        status, err = self.call("POST", "/api/play", {"text": "Ciao"})
        self.assertEqual(status, 400)
        self.assertIn("menu principale", err["error"])

    def test_enter_play_leave(self) -> None:
        status, r = self.call("POST", "/api/campaign", {"name": "esempio"})
        self.assertEqual((status, r["campaign"]), (200, "esempio"))
        status, state = self.call("GET", "/api/state")
        self.assertEqual(state["campaign"], "esempio")
        self.assertTrue(state["campaigns"][0]["current"])
        self.assertIn("Situazione iniziale", state["intro"])

        status, turn = self.call("POST", "/api/play", {"text": "Entro alla Lanterna Blu."})
        self.assertEqual(status, 200)
        self.assertEqual(turn["text"], "Mira ti guarda a lungo. Cosa le chiedi?")

        status, r = self.call("POST", "/api/campaign/leave", {})
        self.assertEqual((status, r["campaign"]), (200, None))
        status, state = self.call("GET", "/api/state")
        self.assertIsNone(state["campaign"])
        self.assertIsNone(self.app.engine)

    def test_create_from_menu_without_entering(self) -> None:
        status, r = self.call("POST", "/api/campaign/new", {"name": "Nuova Prova", "empty": True})
        self.assertEqual((status, r["campaign"]), (200, "Nuova Prova"))
        self.assertTrue((self.camps / "Nuova Prova" / "meccanica" / "creazione-pg.md").exists())
        status, state = self.call("GET", "/api/state")
        self.assertIsNone(state["campaign"])  # si resta nel menu
        new = next(c for c in state["campaigns"] if c["name"] == "Nuova Prova")
        self.assertFalse(new["has_rules"])  # da preparare
        status, err = self.call("POST", "/api/campaign/new", {"name": "Nuova Prova"})
        self.assertEqual(status, 400)
        self.assertIn("Esiste gia'", err["error"])
        for bad in ("../fuori", "inesistente"):
            status, err = self.call("POST", "/api/campaign", {"name": bad})
            self.assertEqual(status, 400)

    def test_character_is_bound_to_the_campaign(self) -> None:
        self.call("POST", "/api/campaign", {"name": "esempio"})
        status, state = self.call("GET", "/api/state")
        self.assertEqual(state["pg"]["slug"], "kael")
        self.assertEqual([s["name"] for s in state["sheets"]], ["Kael"])
        self.assertIn("cacciatore di taglie", state["sheets"][0]["summary"])
        self.assertTrue(state["has_creation_rules"])
        status, sheet = self.call("GET", "/api/pg/sheet?slug=kael")
        self.assertEqual(status, 200)
        self.assertIn("## Stato", sheet["markdown"])
        status, err = self.call("GET", "/api/pg/sheet?slug=nessuno")
        self.assertEqual(status, 400)
        # il master riceve la scheda del personaggio attivo a ogni turno
        self.call("POST", "/api/play", {"text": "Mi guardo attorno."})
        turn = [c for c in self.app.client.calls if "Sei il master" in str(c.get("system"))][0]["messages"][0]["content"]
        self.assertIn("## Personaggio del giocatore: Kael", turn)  # livello veloce: la scheda viaggia nel contesto del turno
        self.assertIn('"punti_ferita":{"attuale":12,"massimo":12}', turn)
        # il giocato porta il nome del personaggio e si ritrova al rientro
        status, h = self.call("GET", "/api/history?which=play")
        self.assertEqual((status, h["total"]), (200, 1))
        self.assertEqual((h["turns"][0]["pg"], h["turns"][0]["player"]), ("Kael", "Mi guardo attorno."))

    def test_leaving_and_coming_back_resumes_the_game(self) -> None:
        self.call("POST", "/api/campaign", {"name": "esempio"})
        self.call("POST", "/api/play", {"text": "Entro alla Lanterna Blu."})
        self.call("POST", "/api/campaign/leave", {})
        status, state = self.call("GET", "/api/state")
        card = state["campaigns"][0]
        self.assertEqual(card["turns"], 1)
        self.assertTrue(card["last_played"])
        # rientro: motore nuovo, la conversazione torna dall'archivio
        self.app.client = FakeClient()
        self.call("POST", "/api/campaign", {"name": "esempio"})
        status, h = self.call("GET", "/api/history?which=play")
        self.assertEqual(h["turns"][0]["player"], "Entro alla Lanterna Blu.")
        self.call("POST", "/api/play", {"text": "Le chiedo della taglia."})
        narrator = [c for c in self.app.client.calls if "Sei il master" in str(c.get("system"))][0]
        self.assertEqual(len(narrator["messages"]), 1)  # niente conversazione al seguito: un contesto composto dal codice
        context = narrator["messages"][0]["content"]
        self.assertIn("## Ultimi scambi, parola per parola\nGIOCATORE: Entro alla Lanterna Blu.", context)
        self.assertTrue(context.rstrip().endswith("GIOCATORE: Le chiedo della taglia."))
        # nuova sessione: archivio, conversazione vuota, file di campagna intatti
        status, r = self.call("POST", "/api/session/new", {"which": "play"})
        self.assertEqual((status, r["archived"]), (200, True))
        status, h = self.call("GET", "/api/history?which=play")
        self.assertEqual(h["turns"], [])
        self.assertIn("Kael parla con Mira.", (self.camps / "esempio" / "ambientazione" / "diario.md").read_text(encoding="utf-8"))

    def test_no_character_no_play_and_first_sheet_becomes_active(self) -> None:
        from test_master import FakeBuilderClient

        self.call("POST", "/api/campaign/new", {"name": "Vuota", "empty": True})
        self.call("POST", "/api/campaign", {"name": "Vuota"})
        status, state = self.call("GET", "/api/state")
        self.assertIsNone(state["pg"])
        self.assertEqual(state["sheets"], [])
        status, err = self.call("POST", "/api/play", {"text": "Entro."})
        self.assertEqual(status, 400)
        self.assertIn("personaggio", err["error"])
        # il costruttore salva la prima scheda: diventa il personaggio della campagna
        self.app.builder._client = FakeBuilderClient()
        status, turn = self.call("POST", "/api/sheet", {"text": "Voglio una contrabbandiera."})
        self.assertEqual(status, 200)
        self.assertIn("schede/sera-vel.json", turn["files_changed"])
        status, state = self.call("GET", "/api/state")
        self.assertEqual(state["pg"]["name"], "Sera Vel")
        self.assertTrue((self.camps / "Vuota" / "stato.json").exists())
        status, turn = self.call("POST", "/api/play", {"text": "Entro."})
        self.assertEqual(status, 200)

    def test_select_character_persists_and_resets_conversation(self) -> None:
        self.call("POST", "/api/campaign", {"name": "esempio"})
        (self.camps / "esempio" / "schede" / "sera-vel.md").write_text("# Sera Vel\n\n## Stato\n\nPunti ferita: 11/11.\n", encoding="utf-8")
        # una seconda scheda non fa dimenticare il personaggio fissato all'ingresso
        status, state = self.call("GET", "/api/state")
        self.assertEqual(state["pg"]["slug"], "kael")
        self.assertEqual(len(state["sheets"]), 2)
        status, _ = self.call("POST", "/api/play", {"text": "Primo turno con Kael."})
        self.assertEqual(status, 200)
        self.assertEqual(self.app.engine.messages, [])  # il livello veloce non tiene conversazioni in memoria
        status, sheet = self.call("POST", "/api/pg/select", {"slug": "sera-vel"})
        self.assertEqual((status, sheet["name"]), (200, "Sera Vel"))
        self.assertEqual(self.app.engine.messages, [])
        self.assertEqual(json.loads((self.camps / "esempio" / "stato.json").read_text(encoding="utf-8"))["pg_attivo"], "sera-vel")
        # uscendo e rientrando la campagna ricorda il suo personaggio
        self.call("POST", "/api/campaign/leave", {})
        self.call("POST", "/api/campaign", {"name": "esempio"})
        status, state = self.call("GET", "/api/state")
        self.assertEqual(state["pg"]["slug"], "sera-vel")
        status, err = self.call("POST", "/api/pg/select", {"slug": "inesistente"})
        self.assertEqual(status, 400)

    def test_prepare_targets_a_named_campaign(self) -> None:
        status, err = self.call("POST", "/api/prepare", {"source": "x.pdf"})
        self.assertEqual(status, 400)  # campagna mancante
        status, err = self.call("POST", "/api/prepare", {"campaign": "esempio", "source": "   "})
        self.assertEqual(status, 400)
        self.assertIn("manuale", err["error"])
        status, st = self.call("GET", "/api/prepare/status")
        self.assertEqual((status, st["running"], st["done"]), (200, False, False))


if __name__ == "__main__":
    unittest.main()
