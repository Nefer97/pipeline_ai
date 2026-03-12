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
from builder import _escape_latex


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
                     dpi: int = 200) -> tuple[dict, str]:
    """
    Renderizza ogni pagina del PDF come PNG e genera LaTeX scheletro.

    Con pymupdf: estrae testo E immagini in un solo passaggio.
    Il testo pymupdf viene usato per arricchire/sostituire pages_data
    dove l'estrazione esterna (pdfplumber) era sparsa (< 80 char).

    Ritorna:
        page_images    — {page_num: "stem_pag_001.png", ...}
        latex_skeleton — LaTeX strutturato con figure e testo per pagina
    """
    pdf_path   = Path(pdf_path)
    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    deps = _check_deps()

    pymupdf_texts: dict = {}   # {page_num: text} estratto da fitz in questo render

    if deps["pymupdf"]:
        page_numbers = {p["page"] for p in pages_data} if pages_data else None
        page_images, pymupdf_texts = _render_with_pymupdf(
            pdf_path, images_dir, dpi, page_numbers
        )
    elif deps["pdf2image"] and deps["pillow"]:
        print(f"    [pdf_renderer] pdf2image")
        page_images = _render_with_pdf2image(pdf_path, images_dir, dpi)
    else:
        print(f"    [pdf_renderer] WARN: nessuna libreria disponibile")
        print(f"                   pip install pymupdf  oppure  pip install pdf2image")
        page_images = {}

    # ── Arricchisci pages_data con testo pymupdf dove necessario ──
    # Se la pagina non ha testo (pages_data mancante) o ne ha poco (< 80 char),
    # sostituiamo/integriamo con il testo estratto direttamente da fitz.
    if pymupdf_texts and pages_data is not None:
        pd_map = {p["page"]: p for p in pages_data}
        for pn, fitz_text in pymupdf_texts.items():
            existing = pd_map.get(pn)
            if existing is None:
                # Pagina non presente in pages_data → aggiungila
                pages_data.append({"page": pn, "text": fitz_text, "chars_count": len(fitz_text)})
            elif existing.get("chars_count", len(existing.get("text", ""))) < 80:
                # Testo sparso → sostituisci con quello fitz (più affidabile)
                existing["text"]        = fitz_text
                existing["chars_count"] = len(fitz_text)
        pages_data.sort(key=lambda p: p["page"])

    # Se pages_data era None (render senza testo fornito), costruiscilo da pymupdf
    effective_pages = pages_data
    if effective_pages is None and pymupdf_texts:
        effective_pages = [
            {"page": pn, "text": txt, "chars_count": len(txt)}
            for pn, txt in sorted(pymupdf_texts.items())
        ]

    # Costruisci LaTeX scheletro con testo + figure
    latex_skeleton = _build_pdf_latex_skeleton(
        pdf_path    = pdf_path,
        pages_data  = effective_pages or [],
        page_images = page_images,
    )

    return page_images, latex_skeleton


# ─────────────────────────────────────────────
# METODO 1: pymupdf (fitz) — render + testo
# ─────────────────────────────────────────────

def _render_with_pymupdf(pdf_path: Path, images_dir: Path,
                          dpi: int, page_numbers: set = None) -> tuple[dict, dict]:
    """
    Apre il PDF una sola volta con fitz e produce:
      - page_images  {page_num: filename}   — PNG renderizzati
      - page_texts   {page_num: text_str}   — testo estratto per pagina

    Usa get_text("blocks") per ottenere testo ordinato per posizione
    (blocchi in ordine y→x, migliore di "text" semplice per layout complessi).
    """
    import fitz
    stem        = pdf_path.stem
    page_images = {}
    page_texts  = {}
    doc         = fitz.open(str(pdf_path))
    mat         = fitz.Matrix(dpi / 72, dpi / 72)
    total       = len(doc)

    try:
        for page_idx, page in enumerate(doc, start=1):
            if page_numbers and page_idx not in page_numbers:
                continue

            # ── Estrazione testo con blocks (ordinati per posizione) ──
            try:
                blocks = page.get_text("blocks", sort=True)  # sort=True: y→x
                lines = []
                prev_y = None
                for b in blocks:
                    # b = (x0, y0, x1, y1, text, block_no, block_type)
                    # block_type 1 = immagine → skip
                    if len(b) < 6 or b[6] == 1:
                        continue
                    txt = b[4].strip()
                    if not txt:
                        continue
                    # Riga vuota tra blocchi distanti verticalmente (> 10pt)
                    if prev_y is not None and b[1] - prev_y > 10:
                        lines.append("")
                    lines.append(txt)
                    prev_y = b[3]
                text = "\n".join(lines).strip()
            except Exception:
                text = page.get_text("text").strip()

            if text:
                page_texts[page_idx] = text

            # ── Rendering PNG ──
            img_filename = f"{stem}_pag_{page_idx:03d}.png"
            img_path     = images_dir / img_filename
            if img_path.exists():
                page_images[page_idx] = img_filename
                continue
            try:
                pix = page.get_pixmap(matrix=mat, alpha=False)
                pix.save(str(img_path))
                page_images[page_idx] = img_filename
                print(f"    ✓ {img_filename}  ({pix.width}×{pix.height}px)")
            except Exception as e:
                print(f"    [WARN] pagina {page_idx} non renderizzata: {e}")
    finally:
        doc.close()

    print(f"    ✓ Renderizzate {len(page_images)}/{total} pagine | "
          f"testo: {len(page_texts)}/{total} pagine")
    return page_images, page_texts


def extract_pdf_text_pymupdf(pdf_path: Path) -> list[dict]:
    """
    Estrae testo da tutte le pagine del PDF usando PyMuPDF.
    Ritorna lista [{page: N, text: "...", chars_count: N}].
    Chiamata da pipeline.py come extractor primario/complementare.
    """
    try:
        import fitz
    except ImportError:
        return []

    pages = []
    doc   = fitz.open(str(pdf_path))
    try:
        for page_idx, page in enumerate(doc, start=1):
            try:
                blocks = page.get_text("blocks", sort=True)
                lines  = []
                prev_y = None
                for b in blocks:
                    if len(b) < 6 or b[6] == 1:
                        continue
                    txt = b[4].strip()
                    if not txt:
                        continue
                    if prev_y is not None and b[1] - prev_y > 10:
                        lines.append("")
                    lines.append(txt)
                    prev_y = b[3]
                text = "\n".join(lines).strip()
            except Exception:
                text = page.get_text("text").strip()
            if text:
                pages.append({"page": page_idx, "text": text, "chars_count": len(text)})
    finally:
        doc.close()

    return pages


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

    # Tutta maiuscola (esclusi numeri e punteggiatura): accetta fino a 150 char
    alpha = [c for c in line if c.isalpha()]
    if alpha and all(c.isupper() for c in alpha):
        return len(line) <= 150

    # Troppo lunga per le euristiche basate su posizione → non è un titolo
    if len(line) > 100:
        return False

    # Seguita da riga vuota (paragrafo separato)
    if idx + 1 < len(lines) and not lines[idx + 1].strip():
        # Deve avere almeno 3 parole per non essere un bullet point
        if len(line.split()) >= 3:
            return True

    # Inizia con numero/lettera di sezione:
    #   "1.", "2.1", "A."  (forma classica con punto)
    #   "1)", "A)"          (forma con parentesi chiusa)
    #   "1.2)"              (misto)
    if re.match(r'^(?:[A-Z]|(?:\d+\.)*\d+)[.)]\s+\w', line):
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

def _build_pdf_latex_skeleton(pdf_path: Path, pages_data: list[dict],
                                page_images: dict) -> str:
    """
    Genera LaTeX scheletro dal PDF.

    Struttura per ogni pagina:
      \subsection{Titolo rilevato} oppure \subsection*{Pagina N}
      \begin{figure}...\end{figure}   (se immagine disponibile)
      testo della pagina
    """
    esc  = _escape_latex
    stem = pdf_path.stem
    parts = []

    # Mappa page_num → testo
    page_text_map = {p["page"]: p["text"] for p in pages_data}
    all_pages     = sorted(set(
        list(page_text_map.keys()) + list(page_images.keys())
    ))

    # Pattern per righe-footer da rimuovere (email, URL soli, pagine numerate sole).
    # La fraction pattern è separata e richiede numeri ≤ 4 cifre per evitare di
    # rimuovere contenuto matematico come "1/2" in formule.
    _footer_line = re.compile(
        r'^\s*(?:'
        r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'  # email
        r'|https?://\S+'                                         # URL
        r'|\d{1,4}\s*/\s*\d{1,4}'                               # pagina "3 / 47"
        r')\s*$'
    )

    def _clean_page_text(t: str) -> str:
        """Rimuove righe che sono solo footer (email, URL, numeri di pagina)."""
        lines = [l for l in t.splitlines() if not _footer_line.match(l)]
        return "\n".join(lines).strip()

    for page_num in all_pages:
        raw_text     = page_text_map.get(page_num, "")
        text         = _clean_page_text(raw_text) if raw_text else ""
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