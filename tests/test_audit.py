import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_claim_audit.audit import NOVOICE, VOICE, audit, claims, evidence, main, normalize, pattern

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

class AuditTests(unittest.TestCase):
    def check_case(self, name, severity=None, rule="documented-voice-claim-false"):
        result = audit(FIXTURES / name)
        expected = [] if severity is None else [(rule, severity)]
        self.assertEqual([(f["rule"], f["severity"]) for f in result["findings"]], expected)
        self.assertEqual(result["verdict"], "FAIL" if severity == "HIGH" else "PASS")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([str(FIXTURES / name), "--json"])
        self.assertEqual(code, 2 if severity == "HIGH" else 0)
        self.assertEqual(json.loads(output.getvalue()), result)
        return result

    def test_01_sr_plain(self):
        self.check_case("sr_plain", None)

    def test_02_sr_module_alias(self):
        self.check_case("sr_module_alias", None)

    def test_03_sr_import_alias(self):
        self.check_case("sr_import_alias", None)

    def test_04_sr_attribute(self):
        self.check_case("sr_attribute", None)

    def test_05_sr_chain(self):
        self.check_case("sr_chain", None)

    def test_06_whisper_plain(self):
        self.check_case("whisper_plain", None)

    def test_07_whisper_chain(self):
        self.check_case("whisper_chain", None)

    def test_08_tts_plain(self):
        self.check_case("tts_plain", None)

    def test_09_tts_alias(self):
        self.check_case("tts_alias", None)

    def test_10_gtts_plain(self):
        self.check_case("gtts_plain", None)

    def test_11_gtts_chain(self):
        self.check_case("gtts_chain", None)

    def test_12_bare_import(self):
        self.check_case("bare_import", "HIGH")

    def test_13_creation_only(self):
        self.check_case("creation_only", "HIGH")

    def test_14_unbound(self):
        self.check_case("unbound", "HIGH")

    def test_15_intermediate(self):
        self.check_case("intermediate", "HIGH")

    def test_16_listen_only(self):
        self.check_case("listen_only", "HIGH")

    def test_17_playback(self):
        self.check_case("playback", "HIGH")

    def test_18_strings(self):
        self.check_case("strings", "HIGH")

    def test_19_boundary(self):
        self.check_case("boundary", "HIGH")

    def test_20_negative(self):
        self.check_case("negative", "HIGH", "documented-no-voice-claim-false")

    def test_21_no_claim(self):
        self.check_case("no_claim", None)

    def test_22_bare_noun(self):
        self.check_case("bare_noun", None)

    def test_23_sms(self):
        self.check_case("sms", None)

    def test_24_push(self):
        self.check_case("push", None)

    def test_25_no_source_asymmetry(self):
        self.check_case("no_source_positive", "HIGH")
        self.check_case("no_source_negative")

    def test_26_test_only(self):
        self.check_case("test_only", "MEDIUM")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(FIXTURES / "test_only"), "--fail-on", "MEDIUM"]), 2)

    def test_27_fences_and_inline(self):
        self.check_case("fenced")
        self.check_case("inline", "HIGH")

    def test_28_negation(self):
        self.assertEqual(claims("does not use voice"), (False, True))
        self.check_case("negation")
        self.assertEqual(claims("doesn't use voice"), (False, True))

    def test_29_self_audit(self):
        result = audit(ROOT)
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["claims"], {"positive": False, "negative": False})
        for path in ROOT.rglob("*.md"):
            rel = path.relative_to(ROOT)
            if rel.as_posix() == "README.md" or "docs" in rel.parts[:-1]:
                text = normalize(path.read_text())
                self.assertIsNone(pattern(VOICE + NOVOICE).search(text), str(path))
                self.assertIsNone(re.search(r"\b(?:using|uses|with|via|over|supports|sends|send) voice claim audit\b", text))
        run = subprocess.run([sys.executable, "-m", "voice_claim_audit", str(ROOT), "--json"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["claims"], {"positive": False, "negative": False})

    def test_30_cli_surface(self):
        version = subprocess.run([sys.executable, "-m", "voice_claim_audit", "--version"], capture_output=True, text=True)
        self.assertEqual((version.returncode, version.stdout.strip()), (0, "0.1.0"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main([str(FIXTURES / "sr_plain"), "--verbose"]), 0)
        self.assertIn("speech_recognition", out.getvalue())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "docs").mkdir()
            (root / "docs/a.md").write_text("voice input")
            self.assertEqual(audit(root)["verdict"], "FAIL")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([temp, "--ignore", "docs/*.md", "--json"]), 0)
        for args in (["/nonexistent-audit-root"], [], [str(ROOT), "--fail-on", "LOW"]):
            with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
                main(args)
            self.assertEqual(caught.exception.code, 1)

    def test_31_env_guard(self):
        original = Path.open
        seen = []
        def guarded(path, *args, **kwargs):
            seen.append(path.name)
            self.assertNotEqual(path.name, ".env")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", guarded):
            self.check_case("bare_import", "HIGH")
        self.assertNotIn(".env", seen)
        self.assertTrue((FIXTURES / "bare_import/.env").is_file())

    def test_32_packaging_and_commands(self):
        for path in list((ROOT / "voice_claim_audit").glob("*.py")) + list((ROOT / "tests").glob("*.py")) + [ROOT / "README.md", ROOT / "pyproject.toml"]:
            self.assertIsNone(re.search(r"(?m)^\s*(?:[>$]\s*)?" + "py" + r"thon(?:\s|$)", path.read_text()))
        config = (ROOT / "pyproject.toml").read_text()
        self.assertIn('setuptools>=77', config)
        self.assertIn('license = "MIT"', config)
        self.assertIn('requires-python = ">=3.9"', config)
        self.assertIn('recursive-include tests', (ROOT / "MANIFEST.in").read_text())

    def test_33_all_claim_phrases(self):
        for phrase in VOICE:
            self.assertEqual(claims(phrase), (True, False), phrase)
            self.assertEqual(claims(phrase.upper().replace(" ", "-")), (True, False), phrase)
        for phrase in NOVOICE:
            self.assertEqual(claims(phrase), (False, True), phrase)
        self.assertEqual(claims("voice\ninput"), (True, False))
        self.assertEqual(claims("voice\n\ninput"), (False, False))
        self.assertEqual(claims("~~~text\nvoice input\n~~~"), (False, False))
        self.assertEqual(claims("invoice input"), (False, False))

    def test_34_other_routes_aliases(self):
        sources = [
            "from whisper.audio import load_model as load; self.m = load(); self.m.transcribe(a)",
            "import whisper as wh; wh.load_model().transcribe(a)",
            "from pyttsx3 import init as start; start().say(a)",
            "import gtts as gt; t = gt.gTTS(a); t.write_to_fp(fp)",
            "from gtts.tts import gTTS as T; self.t = T(a); self.t.save(a)",
            "import speech_recognition; a = b = speech_recognition.Recognizer(); b.recognize_google(x)",
            "import pyttsx3; e: object = pyttsx3.init(); e.say(a)",
        ]
        for source in sources:
            self.assertTrue(evidence(source), source)

    def test_35_explicit_non_evidence(self):
        sources = [
            "import speech_recognition; import whisper; import pyttsx3; import gtts",
            "import pyttsx3; e = pyttsx3.init()",
            "import whisper; m = whisper.load_model(a)",
            "import pyttsx3; e = pyttsx3.init(); e.runAndWait()",
            "from gtts import gTTS; t = gTTS(a)",
            "import whisper; other.transcribe(a)",
            "from mail import Client; c = Client(); c.send(a)",
            "client.messages.create(body=a); webpush(a); ses.send_email(a)",
            "def speak_voice():\n    print('voice'); return 'hello'",
            "@app.post('/audio')\ndef route():\n    return 'voice'",
            "import pygame; pygame.mixer.music.play(); sounddevice.play(a); simpleaudio.play_buffer(a)",
            "from unrelated import Recognizer; r = Recognizer(); r.recognize_google(a)",
            "# import whisper; whisper.load_model().transcribe(a)\nx = 'voice'",
            "from whisper import *; load_model().transcribe(a)",
        ]
        for source in sources:
            self.assertEqual(evidence(source), [], source)

    def test_36_scopes_and_symlinks(self):
        source = "import whisper; whisper.load_model().transcribe(a)"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "README.md").write_text("voice input")
            for directory in ("tests", "fixtures", "docs", "examples"):
                (root / directory).mkdir()
                file = root / directory / "engine.py"
                file.write_text(source)
                self.assertFalse(audit(root)["evidence"])
                file.unlink()
            for filename in ("test_engine.py", "engine_test.py"):
                file = root / filename
                file.write_text(source)
                self.assertEqual(audit(root)["findings"][0]["severity"], "MEDIUM")
                file.unlink()
            target = root / "fixtures/target.py"
            target.write_text(source)
            (root / "linked.py").symlink_to(target)
            self.assertEqual(audit(root)["findings"][0]["severity"], "HIGH")
            (root / "linked_dir").symlink_to(root / "fixtures", target_is_directory=True)
            self.assertFalse(audit(root)["evidence"])

    def test_37_absence_docs_and_multiple_families(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "nested/docs").mkdir(parents=True)
            (root / "nested/docs/a.md").write_text("no voice support")
            (root / "app.py").write_text("import whisper; whisper.load_model().transcribe(a)")
            self.assertEqual(audit(root)["findings"][0]["rule"], "documented-no-voice-claim-false")
            self.assertEqual(audit(root)["findings"][0]["severity"], "HIGH")
            (root / "app.py").unlink()
            (root / "README.md").write_text("voice input and voice output\n\nno voice support")
            self.assertEqual(len(audit(root)["findings"]), 1)

    def test_38_unrelated_claims(self):
        for text in ("email notifications", "a webhook", "a websocket", "server-sent events", "a GraphQL interface", "a gRPC interface", "MQTT", "audio playback", "plays audio"):
            self.assertEqual(claims(text), (False, False))

    def test_39_unreadable_and_syntax(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "README.md").write_text("voice input")
            (root / "app.py").write_text("this is invalid !")
            self.assertEqual(audit(root)["findings"][0]["severity"], "HIGH")
            self.assertEqual(len(audit(root)["parse_errors"]), 1)
            root.chmod(0)
            try:
                with self.assertRaises(ValueError):
                    audit(root)
            finally:
                root.chmod(0o700)

if __name__ == "__main__":
    unittest.main()
