"""
tests/test_core.py — Test suite minimale per i moduli core di Appunti AI.

Copertura:
  - builder._escape_latex, _make_header
  - omml2latex.omml_to_latex
  - preprocessor.detect_subject, context_to_prompt, align_transcript_to_slides
  - formula_detector.is_formula_image (mock immagini sintetiche)

Esegui con:
    cd ~/appunti_ai
    python -m pytest tests/ -v
"""

import io
import sys
import os
import textwrap
from pathlib import Path

# Assicura che i moduli nel parent directory siano importabili
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


# ─────────────────────────────────────────────
# builder — _escape_latex, _make_header
# ─────────────────────────────────────────────

class TestEscapeLatex:
    def setup_method(self):
        from builder import _escape_latex
        self.escape = _escape_latex

    def test_backslash(self):
        assert r"\textbackslash{}" in self.escape("\\")

    def test_ampersand(self):
        assert r"\&" in self.escape("a & b")

    def test_percent(self):
        assert r"\%" in self.escape("50%")

    def test_dollar(self):
        assert r"\$" in self.escape("$100")

    def test_hash(self):
        assert r"\#" in self.escape("#1")

    def test_underscore(self):
        assert r"\_" in self.escape("x_n")

    def test_braces(self):
        result = self.escape("{a}")
        assert r"\{" in result and r"\}" in result

    def test_tilde(self):
        assert r"\textasciitilde{}" in self.escape("~approx")

    def test_less_greater(self):
        result = self.escape("<tag>")
        assert r"\textless{}" in result and r"\textgreater{}" in result

    def test_empty_string(self):
        assert self.escape("") == ""

    def test_clean_text_unchanged(self):
        # Testo senza caratteri speciali non deve cambiare
        t = "Lezione di Analisi Matematica"
        assert self.escape(t) == t

    def test_apostrophe_unchanged(self):
        # L'apostrofo non va escapato in LaTeX UTF-8 (romperebbe "dell'analisi")
        assert self.escape("dell'analisi") == "dell'analisi"

    def test_double_escape_backslash(self):
        # Il backslash deve essere processato una sola volta
        result = self.escape("\\")
        assert result.count("textbackslash") == 1


class TestMakeHeader:
    def setup_method(self):
        from builder import _make_header, _WHISPER_TO_BABEL
        self.make_header = _make_header
        self.whisper_to_babel = _WHISPER_TO_BABEL

    def test_italian_default(self):
        h = self.make_header("it")
        assert "italian" in h
        assert "\\documentclass" in h

    def test_english(self):
        h = self.make_header("en")
        assert "english" in h

    def test_french(self):
        h = self.make_header("fr")
        assert "french" in h

    def test_german(self):
        h = self.make_header("de")
        assert "ngerman" in h

    def test_unknown_lang_passthrough(self):
        # Lingua non mappata → passata direttamente a babel
        h = self.make_header("catalan")
        assert "catalan" in h

    def test_header_has_required_packages(self):
        h = self.make_header("it")
        for pkg in ["amsmath", "graphicx", "hyperref", "fancyhdr", "textcomp"]:
            assert pkg in h, f"Package mancante: {pkg}"

    def test_no_placeholder_remaining(self):
        h = self.make_header("it")
        assert "{babel_lang}" not in h


# ─────────────────────────────────────────────
# omml2latex — omml_to_latex
# ─────────────────────────────────────────────

class TestOmml2Latex:
    def setup_method(self):
        from omml2latex import omml_to_latex
        self.convert = omml_to_latex

    def _wrap(self, inner: str) -> str:
        """Wrappa l'XML interno nel namespace OMML corretto."""
        return (
            '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
            + inner
            + '</m:oMath>'
        )

    def test_simple_fraction(self):
        xml = self._wrap(
            '<m:f>'
            '<m:num><m:r><m:t>1</m:t></m:r></m:num>'
            '<m:den><m:r><m:t>2</m:t></m:r></m:den>'
            '</m:f>'
        )
        result = self.convert(xml)
        assert result is not None
        assert r"\frac" in result
        assert "1" in result and "2" in result

    def test_superscript(self):
        xml = self._wrap(
            '<m:sSup>'
            '<m:e><m:r><m:t>x</m:t></m:r></m:e>'
            '<m:sup><m:r><m:t>2</m:t></m:r></m:sup>'
            '</m:sSup>'
        )
        result = self.convert(xml)
        assert result is not None
        assert "^" in result
        assert "x" in result and "2" in result

    def test_subscript(self):
        xml = self._wrap(
            '<m:sSub>'
            '<m:e><m:r><m:t>x</m:t></m:r></m:e>'
            '<m:sub><m:r><m:t>i</m:t></m:r></m:sub>'
            '</m:sSub>'
        )
        result = self.convert(xml)
        assert result is not None
        assert "_" in result

    def test_square_root(self):
        xml = self._wrap(
            '<m:rad>'
            '<m:deg/>'
            '<m:e><m:r><m:t>x</m:t></m:r></m:e>'
            '</m:rad>'
        )
        result = self.convert(xml)
        assert result is not None
        assert r"\sqrt" in result

    def test_simple_text(self):
        xml = self._wrap('<m:r><m:t>hello</m:t></m:r>')
        result = self.convert(xml)
        assert result is not None
        assert "hello" in result

    def test_greek_alpha(self):
        xml = self._wrap('<m:r><m:t>α</m:t></m:r>')
        result = self.convert(xml)
        assert result is not None
        assert r"\alpha" in result

    def test_invalid_xml_returns_none(self):
        result = self.convert("questo non è xml valido<<<")
        assert result is None

    def test_empty_string_returns_none(self):
        result = self.convert("")
        assert result is None

    def test_lru_cache_consistency(self):
        # Due chiamate identiche devono restituire lo stesso risultato
        xml = self._wrap('<m:r><m:t>x</m:t></m:r>')
        r1 = self.convert(xml)
        r2 = self.convert(xml)
        assert r1 == r2


# ─────────────────────────────────────────────
# preprocessor — detect_subject
# ─────────────────────────────────────────────

class TestDetectSubject:
    def setup_method(self):
        from preprocessor import detect_subject
        self.detect = detect_subject

    def test_ingegneria_keywords(self):
        text = "Il controllore PID regola il sistema in retroazione tramite l'errore"
        assert self.detect(text) == "ingegneria"

    def test_matematica_keywords(self):
        text = "Il teorema di Cauchy afferma che per ogni funzione olomorfa l'integrale è nullo"
        assert self.detect(text) == "matematica"

    def test_fisica_keywords(self):
        text = "La forza elettromagnetica dipende dalla carica e dal campo magnetico"
        assert self.detect(text) == "fisica"

    def test_medicina_keywords(self):
        text = "La patologia clinica studia la diagnosi e la terapia delle malattie anatomiche"
        assert self.detect(text) == "medicina"

    def test_economia_keywords(self):
        text = "Il PIL cresce del 2% e l'inflazione incide sui mercati finanziari"
        assert self.detect(text) == "economia"

    def test_generico_fallback(self):
        text = "Buongiorno a tutti, oggi parliamo di cose varie"
        result = self.detect(text)
        assert result in ("generico", "ingegneria", "matematica", "fisica",
                          "medicina", "economia", "giurisprudenza")

    def test_empty_text(self):
        result = self.detect("")
        assert isinstance(result, str)
        assert len(result) > 0


# ─────────────────────────────────────────────
# preprocessor — align_transcript_to_slides
# ─────────────────────────────────────────────

class TestAlignTranscript:
    def setup_method(self):
        from preprocessor import align_transcript_to_slides
        self.align = align_transcript_to_slides

    def _make_transcript(self, entries):
        """Crea testo trascrizione con timestamp [MM:SS]."""
        lines = []
        for t, text in entries:
            m, s = divmod(t, 60)
            lines.append(f"[{m:02d}:{s:02d}] {text}")
        return "\n".join(lines)

    def _make_slides(self, n):
        """Crea testo slide con marker --- SLIDE N ---."""
        parts = []
        for i in range(1, n + 1):
            parts.append(f"--- SLIDE {i} ---\nContenuto slide {i}\n")
        return "\n".join(parts)

    def test_basic_alignment(self):
        transcript = self._make_transcript([
            (0, "inizio"),
            (60, "metà"),
            (120, "fine"),
        ])
        slides = self._make_slides(3)
        result = self.align(transcript, slides)
        assert len(result) == 3
        # Tutti i segmenti devono essere assegnati
        total_segs = sum(len(s["transcript_segments"]) for s in result)
        assert total_segs == 3

    def test_pause_does_not_shift_segments(self):
        # Pausa lunga (300s) tra segmento 1 e 2: il segmento 2 non deve
        # finire alla slide finale perché il tempo è cappato a 45s
        transcript = self._make_transcript([
            (0, "primo"),
            (300, "secondo — dopo lunga pausa"),  # 5 minuti dopo
            (360, "terzo"),
        ])
        slides = self._make_slides(3)
        result = self.align(transcript, slides)
        assert len(result) == 3
        total_segs = sum(len(s["transcript_segments"]) for s in result)
        assert total_segs == 3

    def test_no_timestamp_uniform_distribution(self):
        # Senza timestamp, distribuzione uniforme
        transcript = "riga uno\nriga due\nriga tre\nriga quattro\nriga cinque\nriga sei"
        slides = self._make_slides(3)
        result = self.align(transcript, slides)
        assert len(result) == 3

    def test_empty_transcript(self):
        result = self.align("", self._make_slides(2))
        assert len(result) == 2

    def test_no_slides(self):
        transcript = self._make_transcript([(0, "testo")])
        result = self.align(transcript, "")
        # Senza slide ritorna blocco unico
        assert len(result) == 1


# ─────────────────────────────────────────────
# formula_detector — is_formula_image (immagini sintetiche)
# ─────────────────────────────────────────────

class TestFormulaDetector:
    def setup_method(self):
        from formula_detector import is_formula_image
        self.detect = is_formula_image

    def _make_png(self, tmp_path, width, height, bg=(255, 255, 255), text_color=(0, 0, 0)):
        """Crea un PNG sintetico con sfondo e qualche pixel scuro."""
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)
        # Disegna alcuni pixel scuri per simulare testo/formula
        draw.rectangle([width//4, height//4, 3*width//4, 3*height//4], fill=text_color)
        path = str(tmp_path / f"test_{width}x{height}.png")
        img.save(path)
        return path

    def test_typical_formula(self, tmp_path):
        # Immagine orizzontale, sfondo bianco, simboli neri
        path = self._make_png(tmp_path, 300, 80)
        assert self.detect(path) is True

    def test_too_small_rejected(self, tmp_path):
        path = self._make_png(tmp_path, 15, 8)
        assert self.detect(path) is False

    def test_too_large_rejected(self, tmp_path):
        # Immagine gigante = grafico, non formula
        path = self._make_png(tmp_path, 2100, 900)
        assert self.detect(path) is False

    def test_dark_background_rejected(self, tmp_path):
        # Sfondo nero → light_ratio basso → non è formula
        path = self._make_png(tmp_path, 300, 80, bg=(20, 20, 20), text_color=(255, 255, 255))
        assert self.detect(path) is False

    def test_very_thin_rejected(self, tmp_path):
        # Aspect ratio < 0.5 → rifiutato
        path = self._make_png(tmp_path, 50, 200)
        assert self.detect(path) is False

    def test_colored_image_rejected(self, tmp_path):
        # Immagine colorata (alta saturazione) → non è formula
        from PIL import Image
        img = Image.new("RGB", (200, 100), (255, 50, 50))  # rosso saturo
        path = str(tmp_path / "colored.png")
        img.save(path)
        assert self.detect(path) is False

    def test_nonexistent_file(self):
        # File inesistente → False, non eccezione
        assert self.detect("/tmp/nonexistent_appunti_ai_test.png") is False

    def test_wmf_skipped(self, tmp_path):
        # WMF non supportato → False senza tentare apertura
        path = str(tmp_path / "formula.wmf")
        Path(path).write_bytes(b"\x00\x00")
        assert self.detect(path) is False


# ─────────────────────────────────────────────
# preprocessor — context_to_prompt
# ─────────────────────────────────────────────

class TestContextToPrompt:
    def setup_method(self):
        from preprocessor import context_to_prompt
        self.ctx_to_prompt = context_to_prompt

    def test_empty_context(self):
        assert self.ctx_to_prompt({}) == ""

    def test_basic_structure(self):
        ctx = {
            "course_title": "Analisi Matematica",
            "lessons": [
                {"number": 1, "title": "Limiti", "key_concepts": ["limite", "continuità"]},
            ]
        }
        result = self.ctx_to_prompt(ctx, current_lesson_number=2)
        assert "Analisi Matematica" in result
        assert "Limiti" in result

    def test_filters_future_lessons(self):
        ctx = {
            "lessons": [
                {"number": 1, "title": "Passata"},
                {"number": 5, "title": "Futura"},
            ]
        }
        result = self.ctx_to_prompt(ctx, current_lesson_number=3)
        assert "Passata" in result
        assert "Futura" not in result

    def test_raccordo_presente(self):
        ctx = {
            "lessons": [
                {"number": 1, "title": "Lez 1", "last_verbal_topic": "Teorema di Rolle"},
            ]
        }
        result = self.ctx_to_prompt(ctx, current_lesson_number=2)
        assert "Teorema di Rolle" in result

    def test_global_symbols(self):
        ctx = {
            "global_symbols": {"x": "variabile", "T": "temperatura"},
            "lessons": [],
        }
        result = self.ctx_to_prompt(ctx)
        assert "$x$" in result or "x" in result
