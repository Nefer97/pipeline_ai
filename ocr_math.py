# -*- coding: utf-8 -*-
r"""
ocr_math.py  —  OCR di formule matematiche da immagini
=======================================================
Wrapper unificato che tenta la conversione immagine → LaTeX
con più backend in ordine di qualità decrescente.

Backend (in ordine di priorità):
  1. pix2tex   — modello ML dedicato formule, qualità alta
                 Richiede: pip install pix2tex  (venv separato raccomandato)
                 Oppure venv in: ~/venv, ~/Scrivania/venv, ~/pix2tex_venv
  2. latex-ocr — alternativa a pix2tex (noto come "nougat-lite")
                 Richiede: pip install latex-ocr
  3. Tesseract — OCR generico + post-processing matematico
                 Richiede: apt install tesseract-ocr + pip install pytesseract
  4. Euristico  — analisi strutturale pixel (solo per formule semplici)
                 Nessuna dipendenza esterna
  0. Fallback   — ritorna None (formula non convertita)

Cache:
  Risultati salvati in cache MD5 su disco per evitare ri-elaborazione.
  File cache: <immagine>.ocr_cache.json

Uso:
    from ocr_math import image_to_latex

    latex = image_to_latex("path/to/formula.png")
    # Ritorna stringa LaTeX (senza $ o \begin{equation})
    # oppure None se nessun backend disponibile o formula non riconoscibile

    # Forza un backend specifico:
    latex = image_to_latex("formula.png", backend="tesseract")

    # Disabilita cache:
    latex = image_to_latex("formula.png", use_cache=False)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────
# DISCOVERY BACKEND  (lazy — caricati solo al primo uso)
# ─────────────────────────────────────────────────────────────

_PIX2TEX_LOADED:   bool = False
_PIX2TEX_MODEL     = None   # pix2tex.LatexOCR instance

_LATEX_OCR_LOADED: bool = False
_LATEX_OCR_MODEL   = None

_TESSERACT_OK:     Optional[bool] = None  # None = non ancora testato


def _find_pix2tex_python() -> Optional[str]:
    """Cerca l'interprete Python con pix2tex installato."""
    candidates = [
        sys.executable,  # Python corrente
        os.path.expanduser("~/venv/bin/python"),
        os.path.expanduser("~/Scrivania/venv/bin/python"),
        os.path.expanduser("~/pix2tex_venv/bin/python"),
        os.path.expanduser("~/.venv/bin/python"),
        "/opt/pix2tex_venv/bin/python",
    ]
    for py in candidates:
        if py and os.path.isfile(py):
            r = subprocess.run(
                [py, "-c", "import pix2tex; print('ok')"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0 and "ok" in r.stdout:
                return py
    return None


def _load_pix2tex():
    global _PIX2TEX_LOADED, _PIX2TEX_MODEL
    if _PIX2TEX_LOADED:
        return _PIX2TEX_MODEL is not None

    _PIX2TEX_LOADED = True
    try:
        from pix2tex.cli import LatexOCR
        _PIX2TEX_MODEL = LatexOCR()
        print("    [ocr_math] backend: pix2tex (caricato)")
        return True
    except ImportError:
        pass
    except Exception as e:
        print(f"    [ocr_math] pix2tex caricamento fallito: {e}", file=sys.stderr)
    return False


def _load_latex_ocr():
    global _LATEX_OCR_LOADED, _LATEX_OCR_MODEL
    if _LATEX_OCR_LOADED:
        return _LATEX_OCR_MODEL is not None

    _LATEX_OCR_LOADED = True
    try:
        from latex_ocr import LatexOCR as LaTeXOCR_alt
        _LATEX_OCR_MODEL = LaTeXOCR_alt()
        print("    [ocr_math] backend: latex-ocr (caricato)")
        return True
    except ImportError:
        pass
    except Exception as e:
        print(f"    [ocr_math] latex-ocr caricamento fallito: {e}", file=sys.stderr)
    return False


def _check_tesseract() -> bool:
    global _TESSERACT_OK
    if _TESSERACT_OK is not None:
        return _TESSERACT_OK
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        _TESSERACT_OK = True
    except Exception:
        try:
            r = subprocess.run(["tesseract", "--version"],
                               capture_output=True, timeout=5)
            _TESSERACT_OK = (r.returncode == 0)
        except Exception:
            _TESSERACT_OK = False
    return _TESSERACT_OK


# ─────────────────────────────────────────────────────────────
# CACHE  (MD5 del file → LaTeX)
# ─────────────────────────────────────────────────────────────

def _cache_path(image_path: str) -> Path:
    return Path(image_path).with_suffix(".ocr_cache.json")


def _load_cache(image_path: str) -> Optional[str]:
    cp = _cache_path(image_path)
    if not cp.exists():
        return None
    try:
        data = json.loads(cp.read_text(encoding="utf-8"))
        # Verifica che l'immagine non sia cambiata
        cur_md5 = _md5_file(image_path)
        if data.get("md5") == cur_md5:
            return data.get("latex")  # può essere None (fallback cached)
    except Exception:
        pass
    return None


def _save_cache(image_path: str, latex: Optional[str]):
    cp = _cache_path(image_path)
    try:
        data = {"md5": _md5_file(image_path), "latex": latex}
        cp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _md5_file(path: str) -> str:
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except Exception:
        pass
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────
# BACKEND 1: pix2tex  (ML, qualità alta)
# ─────────────────────────────────────────────────────────────

def _pix2tex_ocr(image_path: str) -> Optional[str]:
    """
    Usa pix2tex.LatexOCR se disponibile nel Python corrente.
    Se non disponibile, tenta via subprocess con venv alternativo.
    """
    # Prova import diretto
    if _load_pix2tex() and _PIX2TEX_MODEL is not None:
        try:
            from PIL import Image
            img    = Image.open(image_path)
            result = _PIX2TEX_MODEL(img)
            if result and result.strip():
                return _postprocess_latex(result.strip())
        except Exception as e:
            print(f"    [pix2tex] errore: {e}", file=sys.stderr)
        return None

    # Prova via subprocess con venv alternativo
    py = _find_pix2tex_python()
    if py and py != sys.executable:
        try:
            script = (
                "from pix2tex.cli import LatexOCR; from PIL import Image; "
                f"model = LatexOCR(); "
                f"img = Image.open({image_path!r}); "
                "print(model(img))"
            )
            r = subprocess.run(
                [py, "-c", script],
                capture_output=True, text=True, timeout=60
            )
            if r.returncode == 0 and r.stdout.strip():
                return _postprocess_latex(r.stdout.strip())
        except Exception as e:
            print(f"    [pix2tex subprocess] errore: {e}", file=sys.stderr)

    return None


# ─────────────────────────────────────────────────────────────
# BACKEND 2: latex-ocr  (alternativa pix2tex)
# ─────────────────────────────────────────────────────────────

def _latex_ocr_backend(image_path: str) -> Optional[str]:
    if not _load_latex_ocr() or _LATEX_OCR_MODEL is None:
        return None
    try:
        from PIL import Image
        img    = Image.open(image_path)
        result = _LATEX_OCR_MODEL(img)
        if result and str(result).strip():
            return _postprocess_latex(str(result).strip())
    except Exception as e:
        print(f"    [latex-ocr] errore: {e}", file=sys.stderr)
    return None


# ─────────────────────────────────────────────────────────────
# BACKEND 3: Tesseract + post-processing matematico
# ─────────────────────────────────────────────────────────────

# Mappatura correzioni post-OCR per formule
# Tesseract sbaglia sistematicamente alcuni simboli su formule
_TESSERACT_FIXES: list[tuple] = [
    # Sequenze comuni di errore → correzione
    (re.compile(r'\b[|I]([0-9])\b'), r'1\1'),      # I1 → 11
    (re.compile(r'\b([0-9])[|I]\b'), r'\g<1>1'),   # 1I → 11
    (re.compile(r'(?<![a-zA-Z])O(?![a-zA-Z])'), '0'),  # O isolata → 0
    (re.compile(r'\bI\b'), '1'),                    # I isolata → 1
    (re.compile(r'—'), '-'),                        # dash lungo → minus
    (re.compile(r'–'), '-'),                        # dash medio → minus
    (re.compile(r"'"), "'"),                        # apostrofo curvo
    (re.compile(r'"'), '"'),                        # virgolette curve
]

# Simboli matematici che tesseract a volte legge come testo
_SYMBOL_FIXES: dict[str, str] = {
    "alpha":  r"\alpha",  "beta":   r"\beta",   "gamma":  r"\gamma",
    "delta":  r"\delta",  "epsilon":r"\epsilon","lambda": r"\lambda",
    "mu":     r"\mu",     "sigma":  r"\sigma",  "omega":  r"\omega",
    "pi":     r"\pi",     "theta":  r"\theta",  "phi":    r"\phi",
    "infty":  r"\infty",  "sum":    r"\sum",    "int":    r"\int",
    "sqrt":   r"\sqrt",   "frac":   r"\frac",   "cdot":   r"\cdot",
    "times":  r"\times",  "div":    r"\div",    "pm":     r"\pm",
    "leq":    r"\leq",    "geq":    r"\geq",    "neq":    r"\neq",
    "approx": r"\approx", "forall": r"\forall", "exists": r"\exists",
    "partial":r"\partial","nabla":  r"\nabla",  "in":     r"\in",
    "subset": r"\subset", "cup":    r"\cup",    "cap":    r"\cap",
    "lim":    r"\lim",    "max":    r"\max",    "min":    r"\min",
    "sin":    r"\sin",    "cos":    r"\cos",    "tan":    r"\tan",
    "log":    r"\log",    "ln":     r"\ln",     "exp":    r"\exp",
}


def _tesseract_ocr(image_path: str) -> Optional[str]:
    """
    OCR con Tesseract + correzione simboli matematici.
    Restituisce LaTeX approssimativo o None.
    """
    if not _check_tesseract():
        return None

    try:
        from PIL import Image

        img = Image.open(image_path)

        # Pre-processing immagine per migliorare riconoscimento
        img = _preprocess_for_tesseract(img)

        # Prova pytesseract (API Python)
        try:
            import pytesseract

            # PSM 7: single line — adatto per formule su una riga
            # PSM 6: uniform block — per formule multi-riga
            configs = ["--psm 7 -l eng", "--psm 6 -l eng"]
            best = ""
            for cfg in configs:
                text = pytesseract.image_to_string(img, config=cfg).strip()
                if len(text) > len(best):
                    best = text
            raw = best

        except ImportError:
            # Fallback subprocess
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                img.save(tmp.name)
                tmp_path = tmp.name
            try:
                r = subprocess.run(
                    ["tesseract", tmp_path, "stdout", "--psm", "7"],
                    capture_output=True, text=True, timeout=30
                )
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            if r.returncode != 0:
                return None
            raw = r.stdout.strip()

        if not raw:
            return None

        return _postprocess_tesseract(raw)

    except Exception as e:
        print(f"    [tesseract] errore: {e}", file=sys.stderr)
        return None


def _preprocess_for_tesseract(img):
    """
    Pre-processing per migliorare accuratezza OCR su formule:
    - Scala a 3x per avere caratteri più grandi
    - Binarizza (soglia Otsu)
    - Aggiunge bordo bianco
    """
    try:
        import numpy as np
        from PIL import Image, ImageFilter, ImageOps

        # Converti in scala di grigi
        gray = img.convert("L")

        # Ridimensiona se troppo piccola
        w, h = gray.size
        if w < 300:
            scale = max(2, 300 // w)
            gray = gray.resize((w * scale, h * scale), Image.LANCZOS)

        # Denoising leggero
        gray = gray.filter(ImageFilter.MedianFilter(size=1))

        # Binarizzazione adattiva
        arr = np.array(gray)
        thresh = arr.mean() - arr.std() * 0.5
        binary = ((arr > thresh) * 255).astype("uint8")
        result = Image.fromarray(binary)

        # Bordo bianco (tesseract funziona meglio)
        result = ImageOps.expand(result, border=10, fill=255)

        return result

    except Exception:
        return img  # ritorna originale se preprocessing fallisce


def _postprocess_tesseract(raw: str) -> Optional[str]:
    """
    Trasforma il testo OCR grezzo in LaTeX migliore possibile.
    """
    text = raw.strip()
    if not text:
        return None

    # 1. Correzioni caratteri comuni
    for pattern, replacement in _TESSERACT_FIXES:
        text = pattern.sub(replacement, text)

    # 2. Cerca e sostituisce nomi di funzioni/simboli
    for word, cmd in _SYMBOL_FIXES.items():
        # Usa lambda per evitare che re.sub interpreti backslash nel replacement
        pat = re.compile(r'(?<![a-zA-Z])' + re.escape(word) + r'(?![a-zA-Z])')
        text = pat.sub(lambda m, c=cmd: c, text)

    # 3. Rimuovi spazi attorno a operatori matematici
    text = re.sub(r'\s*([+\-*/=^_<>])\s*', r'\1', text)
    text = re.sub(r'\s*([{}])\s*', r'\1', text)

    # 4. Rimuovi ritorni a capo multipli
    text = re.sub(r'\n+', ' ', text)

    # 5. Risultato minimale: almeno 2 caratteri non-spazio
    text = text.strip()
    if len(re.sub(r'\s+', '', text)) < 2:
        return None

    return _postprocess_latex(text)


# ─────────────────────────────────────────────────────────────
# BACKEND 4: Euristica pixel (fallback puro, nessuna dipendenza)
# ─────────────────────────────────────────────────────────────

def _heuristic_ocr(image_path: str) -> Optional[str]:
    """
    Analisi strutturale dell'immagine per riconoscere pattern comuni.
    Funziona solo per formule molto semplici/tipizzate.
    Non produce LaTeX perfetto, ma qualcosa di utile.
    """
    try:
        from PIL import Image
        import numpy as np

        img = Image.open(image_path).convert("L")
        arr = np.array(img)
        h, w = arr.shape

        # Binarizza
        thresh = arr.mean()
        binary = arr < thresh  # True = pixel scuro (inchiostro)

        # Analisi proiezione verticale (distribuzione verticale dei pixel scuri)
        row_sums = binary.sum(axis=1)  # somma per riga
        col_sums = binary.sum(axis=0)  # somma per colonna

        total_ink = binary.sum()
        if total_ink < 10:
            return None

        # Indice di "orizzontalità": rapporto tra larghezza e altezza zona ink
        ink_rows = (row_sums > 0).sum()
        ink_cols = (col_sums > 0).sum()

        # Centro di massa verticale
        if total_ink > 0:
            rows_idx  = np.arange(h)
            center_y  = (rows_idx * row_sums).sum() / total_ink
            center_y_norm = center_y / h
        else:
            center_y_norm = 0.5

        # Stima "gap" orizzontale centrale (possibile barra di frazione)
        middle_region = binary[h//3:2*h//3, :]
        mid_row_sums  = middle_region.sum(axis=1)
        has_horiz_bar = any(
            s > w * 0.3 and s == mid_row_sums.max()
            for s in mid_row_sums
        )

        # Pattern riconoscibili
        if has_horiz_bar and ink_rows > 3:
            # Probabilmente una frazione: ritorna placeholder
            return r"\frac{\cdot}{\cdot}"

        if ink_rows <= 2 and ink_cols > ink_rows * 5:
            # Formula molto piatta: probabilmente equazione semplice
            return None  # Non possiamo fare meglio senza OCR

        # Per formule generiche: segnaposto descrittivo
        density = total_ink / (h * w)
        if density > 0.05:
            return r"\text{[formula]}"

        return None

    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# POST-PROCESSING LaTeX  (condiviso tra backend)
# ─────────────────────────────────────────────────────────────

def _postprocess_latex(latex: str) -> str:
    """
    Pulizia finale del LaTeX prodotto da qualsiasi backend.
    """
    if not latex:
        return latex

    # Rimuovi wrapper $ ... $ o \( ... \) se presenti
    # (li aggiungerà pipeline.py nel contesto giusto)
    latex = re.sub(r'^\$\$?\s*', '', latex)
    latex = re.sub(r'\s*\$\$?$', '', latex)
    latex = re.sub(r'^\\\\\(\s*', '', latex)
    latex = re.sub(r'\s*\\\\\)$', '', latex)
    latex = re.sub(r'^\\\\\[\s*', '', latex)
    latex = re.sub(r'\s*\\\\\]$', '', latex)

    # Rimuovi \begin{equation} e simili (gestiti da pipeline)
    latex = re.sub(r'\\begin\{equation\*?\}', '', latex)
    latex = re.sub(r'\\end\{equation\*?\}', '', latex)

    # Collassa spazi multipli
    latex = re.sub(r'[ \t]+', ' ', latex)
    latex = re.sub(r'\n{2,}', '\n', latex)

    return latex.strip()


# ─────────────────────────────────────────────────────────────
# VALIDAZIONE QUALITÀ  (filtra output spazzatura)
# ─────────────────────────────────────────────────────────────

_JUNK_PATTERNS = [
    re.compile(r'^[.\s,;:]+$'),             # Solo punteggiatura
    re.compile(r'^[a-zA-Z]{1,2}$'),         # Solo 1-2 lettere (troppo breve)
    re.compile(r'^\d+$'),                    # Solo cifre
    re.compile(r'[^\x00-\x7F]{5,}'),        # Troppi non-ASCII (OCR confuso)
]


def _is_valid_latex(latex: Optional[str]) -> bool:
    """Verifica che il LaTeX non sia spazzatura."""
    if not latex or len(latex.strip()) < 2:
        return False
    s = latex.strip()
    for pat in _JUNK_PATTERNS:
        if pat.search(s):
            return False
    # Deve contenere almeno un carattere mathemtatico o lettera
    has_math = any(c in s for c in r'\{}^_+=-/*<>')
    has_alnum = any(c.isalnum() for c in s)
    return has_math or has_alnum


# ─────────────────────────────────────────────────────────────
# API PUBBLICA
# ─────────────────────────────────────────────────────────────

def image_to_latex(
    image_path:  str,
    backend:     Optional[str] = None,
    use_cache:   bool = True,
    min_quality: float = 0.0,
) -> Optional[str]:
    r"""
    Converte un'immagine di formula matematica in LaTeX.

    Parametri:
        image_path  — path all'immagine (PNG/JPG/BMP)
        backend     — forza un backend specifico:
                      "pix2tex" | "latex_ocr" | "tesseract" | "heuristic"
                      None = automatico (prova in ordine di qualità)
        use_cache   — usa/salva cache su disco (default: True)
        min_quality — soglia qualità minima (non usato attualmente,
                      riservato per future implementazioni)

    Ritorna:
        Stringa LaTeX (pronta per \begin{equation}...\end{equation})
        oppure None se nessun backend produce output affidabile.

    Esempio:
        latex = image_to_latex("slide003_a1b2c3d4.png")
        if latex:
            print(rf"\begin{{equation}}\n{latex}\n\end{{equation}}")
    """
    if not image_path or not os.path.isfile(image_path):
        return None

    # Cache
    if use_cache:
        cached = _load_cache(image_path)
        if cached is not None:
            return cached if _is_valid_latex(cached) else None

    result = None

    if backend:
        # Backend specifico richiesto
        _backends = {
            "pix2tex":   _pix2tex_ocr,
            "latex_ocr": _latex_ocr_backend,
            "tesseract": _tesseract_ocr,
            "heuristic": _heuristic_ocr,
        }
        fn = _backends.get(backend)
        if fn:
            result = fn(image_path)
        else:
            print(f"    [ocr_math] backend sconosciuto: {backend!r}", file=sys.stderr)
    else:
        # Prova automatica in ordine di qualità
        for fn in [_pix2tex_ocr, _latex_ocr_backend, _tesseract_ocr, _heuristic_ocr]:
            try:
                result = fn(image_path)
                if _is_valid_latex(result):
                    break
                result = None
            except Exception as e:
                print(f"    [ocr_math] {fn.__name__} errore: {e}", file=sys.stderr)
                result = None

    # Valida risultato finale
    if not _is_valid_latex(result):
        result = None

    # Salva in cache (anche None, per non ri-provare)
    if use_cache:
        _save_cache(image_path, result)

    return result


def get_available_backends() -> list[str]:
    """
    Ritorna la lista dei backend OCR disponibili nel sistema.
    Utile per diagnostica e logging.
    """
    available = []
    if _load_pix2tex():
        available.append("pix2tex")
    if _find_pix2tex_python() and "pix2tex" not in available:
        available.append("pix2tex (subprocess)")
    if _load_latex_ocr():
        available.append("latex-ocr")
    if _check_tesseract():
        available.append("tesseract")
    available.append("heuristic")  # sempre disponibile
    return available


def clear_cache(image_dir: str, recursive: bool = False) -> int:
    """
    Rimuove tutti i file .ocr_cache.json nella cartella.
    Ritorna il numero di file rimossi.
    """
    count = 0
    p = Path(image_dir)
    pattern = "**/*.ocr_cache.json" if recursive else "*.ocr_cache.json"
    for cache_file in p.glob(pattern):
        try:
            cache_file.unlink()
            count += 1
        except Exception:
            pass
    return count


# ─────────────────────────────────────────────────────────────
# DIAGNOSTICA  (uso da linea di comando)
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="ocr_math — OCR formule matematiche da immagini"
    )
    parser.add_argument("images", nargs="*", help="Immagini da convertire")
    parser.add_argument("--backend", choices=["pix2tex","latex_ocr","tesseract","heuristic"],
                        help="Forza un backend specifico")
    parser.add_argument("--no-cache", action="store_true", help="Disabilita cache")
    parser.add_argument("--info", action="store_true", help="Mostra backend disponibili")
    parser.add_argument("--clear-cache", metavar="DIR", help="Svuota cache in DIR")
    args = parser.parse_args()

    if args.info or not args.images:
        print("\n  Backend disponibili:")
        for b in get_available_backends():
            print(f"    ✓ {b}")

        if not args.images:
            print("\n  Uso: python ocr_math.py <immagine.png> [--backend tesseract]")
            print("       python ocr_math.py --info")
            print("       python ocr_math.py --clear-cache ./images/")
            sys.exit(0)

    if args.clear_cache:
        n = clear_cache(args.clear_cache)
        print(f"  Rimossi {n} file cache da {args.clear_cache}")
        sys.exit(0)

    print(f"\n  Backend disponibili: {', '.join(get_available_backends())}")
    print("=" * 60)

    for img_path in args.images:
        if not os.path.isfile(img_path):
            print(f"  ✗ {img_path}: file non trovato")
            continue

        print(f"\n  Immagine: {img_path}")
        result = image_to_latex(
            img_path,
            backend   = args.backend,
            use_cache = not args.no_cache,
        )
        if result:
            print(f"  LaTeX: {result}")
        else:
            print(f"  LaTeX: (non riconosciuta)")

    print("=" * 60)