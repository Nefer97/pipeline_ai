"""
pdf_renderer.py — Renderizza pagine PDF come immagini PNG
=========================================================

Produce un PNG per ogni pagina del PDF.
Le immagini vengono salvate in images_dir con naming:
    {stem}_pag_001.png, {stem}_pag_002.png, ...

Dove {stem} è il nome del file PDF senza estensione, così
più PDF nella stessa lezione non si sovrascrivono.

Due output distinti:
  1. Immagini PNG → nel LaTeX finale come \begin{figure}
  2. LaTeX bozza  → scheletro strutturato da passare a Claude

Uso:
    from pdf_renderer import render_pdf_pages, build_pdf_latex

    page_images, latex_skeleton = render_pdf_pages(
        pdf_path   = Path("dispensa.pdf"),
        images_dir = Path("output/images"),
        pages_data = pages,   # da extract_pdf_pages()
    )
    # page_images = {1: "dispensa_pag_001.png", 2: "dispensa_pag_002.png", ...}
    # latex_skeleton = "\\subsection{...}\\begin{figure}..."
"""

import re
from pathlib import Path


# ─────────────────────────────────────────────
# DIPENDENZE OPZIONALI
# ─────────────────────────────────────────────

def _check_deps() -> dict:
    deps = {"pymupdf": False, "pdf2image": False, "pillow": False}
    try:
        import fitz
        deps["pymupdf"] = True
    except ImportError:
        pass
    try:
        from pdf2image import convert_from_path
        deps["pdf2image"] = True
    except ImportError:
        pass
    try:
        from PIL import Image
        deps["pillow"] = True
    except ImportError:
        pass
    return deps


# ─────────────────────────────────────────────
# RENDERING PRINCIPALE
# ─────────────────────────────────────────────

def render_pdf_pages(pdf_path: Path, images_dir: Path,
                     pages_data: list[dict] = None,
                     dpi: int = 150) -> tuple[dict, str]:
    """
    Renderizza ogni pagina del PDF come PNG e genera LaTeX scheletro.

    Ritorna:
        page_images    — {page_num: "stem_pag_001.png", ...}
        latex_skeleton — LaTeX strutturato con figure e testo per pagina
    """
    pdf_path   = Path(pdf_path)
    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    deps = _check_deps()

    if deps["pymupdf"]:
            page_numbers = {p["page"] for p in pages_data} if pages_data else None
            page_images = _render_with_pymupdf(pdf_path, images_dir, dpi, page_numbers)
    elif deps["pdf2image"] and deps["pillow"]:
        print(f"    [pdf_renderer] pdf2image")
        page_images = _render_with_pdf2image(pdf_path, images_dir, dpi)
    else:
        print(f"    [pdf_renderer] WARN: nessuna libreria disponibile")
        print(f"                   pip install pymupdf  oppure  pip install pdf2image")
        page_images = {}

    # Costruisci LaTeX scheletro con testo + figure
    latex_skeleton = _build_pdf_latex_skeleton(
        pdf_path    = pdf_path,
        pages_data  = pages_data or [],
        page_images = page_images,
    )

    return page_images, latex_skeleton


# ─────────────────────────────────────────────
# METODO 1: pymupdf (fitz) — standard, veloce
# ─────────────────────────────────────────────

def _render_with_pymupdf(pdf_path: Path, images_dir: Path,
                          dpi: int, page_numbers: set = None) -> dict:
    import fitz
    stem   = pdf_path.stem
    result = {}
    doc    = fitz.open(str(pdf_path))
    mat    = fitz.Matrix(dpi / 72, dpi / 72)
    total  = len(doc)

    for page_idx, page in enumerate(doc, start=1):
        # ← salta le pagine fuori dal chunk
        if page_numbers and page_idx not in page_numbers:
            continue

        img_filename = f"{stem}_pag_{page_idx:03d}.png"
        img_path     = images_dir / img_filename
        if img_path.exists():
            result[page_idx] = img_filename
            continue
        try:
            pix = page.get_pixmap(matrix=mat, alpha=False)
            pix.save(str(img_path))
            result[page_idx] = img_filename
            print(f"    ✓ {img_filename}  ({pix.width}×{pix.height}px)")
        except Exception as e:
            print(f"    [WARN] pagina {page_idx} non renderizzata: {e}")

    doc.close()
    print(f"    ✓ Renderizzate {len(result)}/{total} pagine")
    return result


# ─────────────────────────────────────────────
# METODO 2: pdf2image (usa poppler)
# ─────────────────────────────────────────────

def _render_with_pdf2image(pdf_path: Path, images_dir: Path, dpi: int) -> dict:
    from pdf2image import convert_from_path

    stem   = pdf_path.stem
    result = {}

    try:
        images = convert_from_path(str(pdf_path), dpi=dpi)
    except Exception as e:
        print(f"    [ERRORE] pdf2image: {e}")
        return {}

    for page_idx, img in enumerate(images, start=1):
        img_filename = f"{stem}_pag_{page_idx:03d}.png"
        img_path     = images_dir / img_filename

        if img_path.exists():
            result[page_idx] = img_filename
            continue

        try:
            img.save(str(img_path), "PNG")
            result[page_idx] = img_filename
            print(f"    ✓ {img_filename}")
        except Exception as e:
            print(f"    [WARN] pagina {page_idx} non salvata: {e}")

    print(f"    ✓ Renderizzate {len(result)} pagine")
    return result


# ─────────────────────────────────────────────
# RILEVAMENTO TITOLI NEL TESTO
# Euristica: riga breve (< 80 char) in maiuscolo
# o seguita da riga vuota → probabile titolo/sezione
# ─────────────────────────────────────────────

def _detect_title(lines: list[str], idx: int) -> bool:
    """
    Ritorna True se la riga all'indice idx sembra un titolo.
    """
    line = lines[idx].strip()
    if not line:
        return False

    # Troppo lunga per essere un titolo
    if len(line) > 80:
        return False

    # Tutta maiuscola (esclusi numeri e punteggiatura)
    alpha = [c for c in line if c.isalpha()]
    if alpha and all(c.isupper() for c in alpha):
        return True

    # Seguita da riga vuota (paragrafo separato)
    if idx + 1 < len(lines) and not lines[idx + 1].strip():
        # Ma deve avere almeno 3 parole per non essere un bullet
        if len(line.split()) >= 3:
            return True

    # Inizia con numero di sezione tipo "1.", "2.1", "A."
    if re.match(r'^(\d+\.)+\s+\w', line) or re.match(r'^[A-Z]\.\s+\w', line):
        return True

    return False


def _detect_sections(text: str) -> list[dict]:
    """
    Divide il testo in sezioni rilevando i titoli.
    Ritorna lista di {"title": str, "body": str}
    Se nessun titolo trovato ritorna [{"title": None, "body": text}]
    """
    lines    = text.split("\n")
    sections = []
    current_title = None
    current_body  = []

    for i, line in enumerate(lines):
        if _detect_title(lines, i):
            # Salva sezione precedente
            if current_body or current_title:
                sections.append({
                    "title": current_title,
                    "body":  "\n".join(current_body).strip(),
                })
            current_title = line.strip()
            current_body  = []
        else:
            current_body.append(line)

    # Ultima sezione
    if current_body or current_title:
        sections.append({
            "title": current_title,
            "body":  "\n".join(current_body).strip(),
        })

    return sections if sections else [{"title": None, "body": text}]


# ─────────────────────────────────────────────
# COSTRUZIONE LaTeX SCHELETRO
# ─────────────────────────────────────────────

def _escape_latex_basic(text: str) -> str:
    """Escape caratteri speciali LaTeX — versione standalone."""
    for a, b in [
        ("\\", "\\textbackslash{}"),
        ("&",  "\\&"),
        ("%",  "\\%"),
        ("$",  "\\$"),
        ("#",  "\\#"),
        ("{",  "\\{"),
        ("}",  "\\}"),
        ("~",  "\\textasciitilde{}"),
        ("^",  "\\textasciicircum{}"),
    ]:
        text = text.replace(a, b)
    return text


def _build_pdf_latex_skeleton(pdf_path: Path, pages_data: list[dict],
                                page_images: dict) -> str:
    """
    Genera LaTeX scheletro dal PDF.

    Struttura per ogni pagina:
      \subsection{Titolo rilevato} oppure \subsection*{Pagina N}
      \begin{figure}...\end{figure}   (se immagine disponibile)
      testo della pagina
    """
    esc  = _escape_latex_basic
    stem = pdf_path.stem
    parts = []

    # Mappa page_num → testo
    page_text_map = {p["page"]: p["text"] for p in pages_data}
    all_pages     = sorted(set(
        list(page_text_map.keys()) + list(page_images.keys())
    ))

    for page_num in all_pages:
        text        = page_text_map.get(page_num, "")
        img_filename = page_images.get(page_num)

        # Prova a rilevare sezioni nel testo della pagina
        sections = _detect_sections(text) if text else []

        if sections and sections[0]["title"]:
            # Usa il titolo rilevato come \subsection
            first = sections[0]
            parts.append(
                f"\n\\subsection{{{esc(first['title'])}}}"
                f"\\label{{subsec:{stem}-pag{page_num:03d}}}\n"
            )
        else:
            # Nessun titolo rilevato → \subsection* con numero pagina
            parts.append(
                f"\n\\subsection*{{Pagina {page_num}}}"
                f"\\label{{subsec:{stem}-pag{page_num:03d}}}\n"
            )

        # Figura pagina PDF
        if img_filename:
            parts.append(
                f"\\begin{{figure}}[H]\n"
                f"  \\centering\n"
                f"  \\includegraphics[width=\\textwidth]{{{img_filename}}}\n"
                f"  \\caption{{Pagina {page_num} — {esc(stem)}}}\n"
                f"\\end{{figure}}\n"
            )

        # Testo della pagina
        if text:
            if sections and sections[0]["title"]:
                # Prima sezione già usata come titolo → scrivi solo il body
                body = sections[0]["body"]
                if body:
                    parts.append(esc(body) + "\n")
                # Sezioni successive diventano \subsubsection
                for sec in sections[1:]:
                    if sec["title"]:
                        parts.append(f"\\subsubsection{{{esc(sec['title'])}}}\n")
                    if sec["body"]:
                        parts.append(esc(sec["body"]) + "\n")
            else:
                # Nessun titolo — scrivi tutto il testo
                if sections:
                    parts.append(esc(sections[0]["body"]) + "\n")

    return "\n".join(parts)


# ─────────────────────────────────────────────
# HELPER — figura LaTeX per una pagina PDF
# (per uso esterno se serve)
# ─────────────────────────────────────────────

def pdf_page_figure_latex(img_filename: str, page_number: int,
                           pdf_stem: str = "") -> str:
    """
    Genera blocco \begin{figure} per una pagina PDF.
    """
    cap = f"Pagina {page_number}" + (f" — {pdf_stem}" if pdf_stem else "")
    return (
        f"\\begin{{figure}}[H]\n"
        f"  \\centering\n"
        f"  \\includegraphics[width=\\textwidth]{{{img_filename}}}\n"
        f"  \\caption{{{cap}}}\n"
        f"\\end{{figure}}\n"
    )