"""La documentazione a sezioni: ogni sezione esiste, le generate coincidono col codice, CLAUDE.md le cita tutte."""

from __future__ import annotations

import io
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from master import docs  # noqa: E402
from master.tools import SAVE_SHEET_TOOL, SCENE_TOOL, TOOLS  # noqa: E402


class TestSections(unittest.TestCase):
    def test_every_section_renders_with_its_title(self) -> None:
        for name, (title, _, _) in docs.INDEX.items():
            text = docs.render(name)
            self.assertTrue(text.startswith(f"# {title}\n"), name)
            self.assertGreater(len(text), 800, name)

    def test_generated_files_on_disk_match_the_code(self) -> None:
        self.assertEqual(docs.stale(), [], "lancia: python main.py --doc --rigenera e committa docs/sezioni/")
        for name in docs.GENERATED:
            self.assertIn(docs.GENERATED_MARK, (docs.SECTIONS_DIR / f"{name}.md").read_text(encoding="utf-8"))

    def test_static_sections_are_files_without_the_generated_mark(self) -> None:
        for name in docs.INDEX:
            if name in docs.GENERATED:
                continue
            text = (docs.SECTIONS_DIR / f"{name}.md").read_text(encoding="utf-8")
            self.assertNotIn(docs.GENERATED_MARK, text, name)

    def test_no_stray_files_in_the_sections_folder(self) -> None:
        on_disk = {p.stem for p in docs.SECTIONS_DIR.glob("*.md")}
        self.assertEqual(on_disk, set(docs.INDEX))

    def test_unknown_section_is_refused_with_the_list(self) -> None:
        with self.assertRaises(KeyError) as ctx:
            docs.render("inesistente")
        self.assertIn("scheda", str(ctx.exception))


class TestGeneratedContent(unittest.TestCase):
    def test_every_tool_is_documented_with_its_parameters(self) -> None:
        text = docs.render("strumenti-scriba")
        for tool in [*TOOLS, SCENE_TOOL, SAVE_SHEET_TOOL]:
            self.assertIn(f"### `{tool['name']}`", text)
            for param in (tool["input_schema"].get("properties") or {}):
                self.assertIn(f"| `{param}` |", text, f"{tool['name']}.{param}")

    def test_dice_section_lists_every_profile_field(self) -> None:
        from master.dice import PROFILE_SCHEMA

        text = docs.render("dadi")
        kind = PROFILE_SCHEMA["properties"]["tiri"]["items"]["properties"]
        for field in kind:
            self.assertIn(f"| `{field}` |", text, field)
        for field in kind["eventi"]["items"]["properties"]:
            self.assertIn(f"| `{field}` |", text, field)
        self.assertIn("[[tiro:", text)

    def test_notes_section_quotes_the_prompts(self) -> None:
        from master.engine import MASTER_PROMPT, SCRIBE_PROMPT

        text = docs.render("appunti")
        self.assertIn("<appunti>", text)
        self.assertIn(SCRIBE_PROMPT.strip(), text)
        self.assertIn(docs._prompt_section(MASTER_PROMPT, "Orologi"), text)

    def test_sheet_section_quotes_the_builder_conventions_and_routes(self) -> None:
        from master.sheet import XP_ROUTES

        text = docs.render("scheda")
        self.assertIn("Chiavi in minuscolo", text)
        for route in XP_ROUTES:
            self.assertIn(f"| `{route}` |", text)
        self.assertIn("`stato`, `esperienza`", text)


class TestClaudeMd(unittest.TestCase):
    def test_claude_md_points_to_every_section(self) -> None:
        text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        for name in docs.INDEX:
            self.assertIn(f"--doc {name}", text, name)
        self.assertIn("non esiste", text)


class TestCli(unittest.TestCase):
    def run_main(self, *argv: str) -> tuple[int, str]:
        import main

        out = io.StringIO()
        with redirect_stdout(out):
            code = main.main(list(argv))
        return code, out.getvalue()

    def test_listing_section_and_unknown(self) -> None:
        code, text = self.run_main("--doc")
        self.assertEqual(code, 0)
        for name in docs.INDEX:
            self.assertRegex(text, rf"(?m)^  {re.escape(name)}\s")
        self.assertNotIn("ATTENZIONE", text)
        code, text = self.run_main("--doc", "Scheda")
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("# La scheda del personaggio"))
        code, _ = self.run_main("--doc", "boh")
        self.assertEqual(code, 2)

    def test_regenerate_is_idempotent(self) -> None:
        code, text = self.run_main("--doc", "--rigenera")
        self.assertEqual(code, 0)
        self.assertIn("nessuna", text)


if __name__ == "__main__":
    unittest.main()
