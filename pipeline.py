#!/usr/bin/env python3
"""
pipeline.py — Orchestratore Appunti AI
=======================================
Converte fonti miste in un libro LaTeX strutturato.

Fonti supportate:
  Audio:  .mp3 .wav .m4a .ogg .flac
  Video:  .mp4 .mkv .avi .mov .webm  -> ffmpeg -> Whisper
  Slide:  .pptx                       -> extractor + omml2latex + pix2tex
  Word:   .docx                       -> python-docx
  PDF:    .pdf                        -> pdfplumber
  Testo:  .txt .md                    -> diretto

Output:
  output/
  ├── main.tex          <- compila questo con pdflatex
  ├── lezione_01.tex
  ├── lezione_02.tex
  └── images/

Uso:
  python pipeline.py ./lezione_01/
  python pipeline.py --batch ./corso/ --title "Analisi Matematica 1"
  python pipeline.py --batch ./corso/ --skip-ai --skip-ocr   # veloce, offline
"""

# definisce percorsi, estensioni dei file supportati, modelli AI da usare.
import argparse
import os
import sys
import json
import time
import subprocess
import shutil
from pathlib import Path
from datetime import datetime

# Aggiungi la cartella dello script al path per trovare i moduli del collega
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

# ─────────────────────────────────────────────
# CONFIGURAZIONE
# ─────────────────────────────────────────────
CONFIG = {
    "whisper_model":     "base",
    "claude_model":      "claude-sonnet-4-20250514",
    "claude_max_tokens": 8000,
    "ext_audio":  [".mp3", ".wav", ".m4a", ".ogg", ".flac"],
    "ext_video":  [".mp4", ".mkv", ".avi", ".mov", ".webm"],
    "ext_slide":  [".pptx"],
    "ext_doc":    [".docx"],
    "ext_pdf":    [".pdf"],
    "ext_text":   [".txt", ".md"],
    "images_subdir": "images",
}

# ─────────────────────────────────────────────
# IMPORT MODULI extractor builder formula_detector omml2latex ocr_math
# ─────────────────────────────────────────────
try:
    from extractor import extract_slides
    from builder import build_latex, _escape_latex
    from formula_detector import is_formula_image
    from omml2latex import omml_to_latex
    from ocr_math import image_to_latex
    COLLEAGUE_MODULES = True
    print("✓ Moduli collega: extractor, builder, formula_detector, omml2latex, ocr_math")
except ImportError as e:
    COLLEAGUE_MODULES = False
    print(f"⚠  Moduli collega non disponibili ({e}) — uso fallback base")


# ───────────────────────────────────────────────────────────────────────────────────
#                       ESTRAZIONE TESTO DA AUDIO (ffmpeg + whisper)
# ───────────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────
# STEP 1: ESTRAZIONE AUDIO DA VIDEO (ffmpeg)
# ─────────────────────────────────────────────
def extract_audio_from_video(video_path: Path, out_dir: Path):
    mp3_path = out_dir / (video_path.stem + "_audio.mp3")
    if mp3_path.exists():
        print(f"    [cache] audio già estratto: {mp3_path.name}")
        return mp3_path
    print(f"    ffmpeg: {video_path.name} -> mp3 ...")
    cmd = ["ffmpeg", "-y", "-i", str(video_path),
           "-vn", "-ac", "1", "-codec:a", "libmp3lame", "-qscale:a", "4",
           str(mp3_path)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        print(f"    [ERRORE] ffmpeg: {r.stderr.decode()[:200]}")
        return None
    print(f"    ✓ {mp3_path.name}")
    return mp3_path


# ─────────────────────────────────────────────
# STEP 2: TRASCRIZIONE WHISPER
# ─────────────────────────────────────────────
def transcribe_audio(audio_path: Path, model_name: str = "base") -> str:
    cache = audio_path.with_suffix(".transcript.txt")
    if cache.exists():
        print(f"    [cache] {cache.name}")
        return cache.read_text(encoding="utf-8")
    try:
        import whisper
    except ImportError:
        print("    [MANCANTE] whisper — pip install openai-whisper")
        return ""
    print(f"    Whisper ({model_name}): {audio_path.name} ...")
    t0 = time.time()
    model = whisper.load_model(model_name)
    result = model.transcribe(str(audio_path), language="it", verbose=False)
    elapsed = time.time() - t0
    lines = []
    for seg in result.get("segments", []):
        m, s = int(seg["start"] // 60), int(seg["start"] % 60)
        lines.append(f"[{m:02d}:{s:02d}] {seg['text'].strip()}")
    text = "\n".join(lines)
    cache.write_text(text, encoding="utf-8")
    print(f"    ✓ {elapsed:.0f}s, {len(lines)} segmenti")
    return text



# ───────────────────────────────────────────────────────────────────────────────────
#                                        PIPELINE PPTX / DOCX / PDF
# ───────────────────────────────────────────────────────────────────────────────────
def process_pptx_full(pptx_path: Path, images_dir: Path, skip_ocr: bool = False):
    """
    Usa: extractor -> omml2latex -> formula_detector + pix2tex (opz.)
    Ritorna (slides_list, testo_plain_per_claude)
    """
    print(f"    extractor: {pptx_path.name} ...")
    slides = extract_slides(str(pptx_path), str(images_dir))
    n_omml = n_ocr = 0

    # OMML -> LaTeX
    for slide in slides:
        for obj in slide.objects:
            if obj.obj_type == "omml_formula":
                obj.latex_result = omml_to_latex(obj.content)
                n_omml += 1

    # pix2tex: OCR su immagini che sembrano formule
    if not skip_ocr:
        candidates = [
            (s, o) for s in slides for o in s.objects
            if o.obj_type == "image" and o.image_path and is_formula_image(o.image_path)
        ]
        if candidates:
            print(f"    pix2tex: {len(candidates)} immagini formula ...")
            for _, obj in candidates:
                latex = image_to_latex(obj.image_path)
                if latex:
                    obj.latex_result = latex
                    n_ocr += 1

    total_obj = sum(len(s.objects) for s in slides)
    print(f"    ✓ {len(slides)} slide | {total_obj} oggetti | {n_omml} OMML | {n_ocr} OCR")

    # Testo plain per Claude
    lines = []
    for slide in slides:
        lines.append(f"\n--- SLIDE {slide.slide_number}: {slide.title} ---")
        for obj in slide.objects:
            if obj.obj_type == "text" and obj.content.strip():
                lines.append(obj.content.strip())
            elif obj.obj_type == "omml_formula":
                f = getattr(obj, "latex_result", "")
                if f:
                    lines.append(f"[FORMULA: {f}]")
    return slides, "\n".join(lines)

# ─────────────────────────────────────────────
# STEP 3b: FALLBACK SE NON CI SONO I MODULI
# ─────────────────────────────────────────────
def process_pptx_fallback(pptx_path: Path) -> str:
    try:
        from pptx import Presentation
        prs = Presentation(str(pptx_path))
        lines = []
        for i, slide in enumerate(prs.slides, 1):
            title = ""
            if slide.shapes.title:
                title = slide.shapes.title.text.strip()
            lines.append(f"\n--- SLIDE {i}: {title} ---")
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        t = para.text.strip()
                        if t and t != title:
                            lines.append(t)
        print(f"    ✓ pptx fallback: {len(prs.slides)} slide")
        return "\n".join(lines)
    except Exception as e:
        print(f"    [ERRORE] pptx: {e}")
        return ""


# ─────────────────────────────────────────────
# STEP 4: PDF — estrazione a chunk di pagine
# ─────────────────────────────────────────────
def extract_pdf_pages(pdf_path: Path) -> list[dict]:
    """
    Estrae il testo del PDF pagina per pagina.
    Ritorna lista di dict: [{page: N, text: "..."}]
    """
    pages = []
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                t = page.extract_text()
                if t and t.strip():
                    pages.append({"page": i, "text": t.strip()})
        print(f"    ✓ pdf pdfplumber: {len(pages)} pagine con testo")
        return pages
    except ImportError:
        pass
    try:
        import PyPDF2
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for i, page in enumerate(reader.pages, 1):
                t = page.extract_text()
                if t and t.strip():
                    pages.append({"page": i, "text": t.strip()})
        print(f"    ✓ pdf PyPDF2: {len(pages)} pagine con testo")
        return pages
    except ImportError:
        print("    [MANCANTE] pdfplumber — pip install pdfplumber")
        return []

# ──────────────────────────────
# PDF → singole pagine salvate
# ──────────────────────────────
def save_pdf_pages_as_txt(pdf_path: Path, output_dir: Path) -> list[Path]:
    """
    Estrae ogni pagina del PDF e la salva come file .txt nella cartella output_dir.
    Ritorna la lista dei file salvati.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    saved_files = []

    pages = extract_pdf_pages(pdf_path)
    for page in pages:
        page_num = page["page"]
        text     = page["text"]
        file_path = output_dir / f"slide_{page_num:03d}.txt"
        file_path.write_text(text, encoding="utf-8")
        saved_files.append(file_path)

    print(f"    ✓ Salvate {len(saved_files)} pagine in {output_dir}")
    return saved_files

def chunk_pdf_pages(pages: list[dict], chunk_size: int = 10) -> list[list[dict]]:
    """Divide le pagine in gruppi da chunk_size."""
    return [pages[i:i+chunk_size] for i in range(0, len(pages), chunk_size)]


def extract_pdf(pdf_path: Path) -> str:
    """Versione semplice per PDF piccoli o uso come testo extra."""
    pages = extract_pdf_pages(pdf_path)
    lines = [f"\n--- PAG {p['page']} ---\n{p['text']}" for p in pages]
    return "\n".join(lines)

def process_pdf_chunked(pdf_path: Path, output_dir: Path,
                         base_lesson_number: int, title: str,
                         skip_ai: bool = False,
                         chunk_size: int = 10) -> list[Path]:
    """
    Processa un PDF grande dividendolo in chunk di pagine.
    Ogni chunk diventa un lezione_NN.tex separato.
    Ritorna la lista dei .tex generati.
    """
    print(f"\n  [PDF grande] {pdf_path.name} — chunking ogni {chunk_size} pagine")
    from pdf_renderer import render_pdf_pages
    images_dir = output_dir / "images"
    images_dir.mkdir(exist_ok=True)
    pages = extract_pdf_pages(pdf_path)
    if not pages:
        print("  [ERRORE] Nessun testo estratto dal PDF")
        return []

    total_pages = pages[-1]["page"] if pages else 0
    chunks      = chunk_pdf_pages(pages, chunk_size)
    print(f"  {total_pages} pagine totali → {len(chunks)} chunk da ~{chunk_size} pag")

    tex_files = []
    for idx, chunk in enumerate(chunks):
        lesson_num  = base_lesson_number + idx
        p_start     = chunk[0]["page"]
        p_end       = chunk[-1]["page"]
        chunk_title = f"{title} — pag. {p_start}–{p_end}"
        chunk_text  = "\n\n".join(
            f"[PAG {p['page']}]\n{p['text']}" for p in chunk
        )
        out_tex = output_dir / f"lezione_{lesson_num:02d}.tex"
        print(f"\n  Chunk {idx+1}/{len(chunks)}: pag {p_start}–{p_end}")

        if skip_ai:
            page_images, latex_skeleton = render_pdf_pages(
                pdf_path   = pdf_path,
                images_dir = images_dir,
                pages_data = chunk,
            )
            if latex_skeleton:
                def esc(t):
                    for a, b in [("\\","\\textbackslash{}"),("&","\\&"),
                                ("%","\\%"),("$","\\$"),("#","\\#")]:
                        t = t.replace(a, b)
                    return t
                content = (
                    f"\\section{{Lezione {lesson_num}: {esc(chunk_title)}}}\n"
                    f"\\label{{sec:lezione{lesson_num:02d}}}\n\n"
                    + latex_skeleton
                )
            else:
                def esc(t):
                    for a, b in [("\\","\\textbackslash{}"),("&","\\&"),
                                ("%","\\%"),("$","\\$"),("#","\\#")]:
                        t = t.replace(a, b)
                    return t
                content_lines = [
                    f"\\section{{Lezione {lesson_num}: {esc(chunk_title)}}}",
                    f"\\label{{sec:lezione{lesson_num:02d}}}\n",
                ]
                for p in chunk:
                    content_lines.append(f"\\subsection*{{Pagina {p['page']}}}")
                    for line in p["text"].split("\n"):
                        line = line.strip()
                        if line:
                            content_lines.append(esc(line) + "\n")
                content = "\n".join(content_lines)
        else:
            # ── Costruisce sources con la nuova struttura ──
            page_images, latex_skeleton = render_pdf_pages(
                pdf_path   = pdf_path,
                images_dir = images_dir,
                pages_data = chunk,
            )
            chunk_sources = {
                "has_audio": False,
                "scheletro": [{
                    "filename":    pdf_path.name,
                    "text":        chunk_text,
                    "pages":       len(chunk),
                    "latex":       latex_skeleton,
                    "page_images": page_images,
                }],
                "carne":    [],
                "supporto": [],
                "contorno": [],
            }
            content = generate_with_claude(
                lesson_number = lesson_num,
                title         = chunk_title,
                sources       = chunk_sources,
            )
            if not content:
                def esc(t):
                    for a, b in [("\\","\\textbackslash{}"),("&","\\&"),
                                  ("%","\\%"),("$","\\$"),("#","\\#")]:
                        t = t.replace(a, b)
                    return t
                content = (
                    f"\\section{{{esc(chunk_title)}}}\n"
                    f"\\label{{sec:lezione{lesson_num:02d}}}\n\n"
                )
                for p in chunk:
                    content += f"\\subsection*{{Pagina {p['page']}}}\n"
                    for line in p["text"].split("\n"):
                        line = line.strip()
                        if line:
                            content += esc(line) + "\n"

        write_lesson_tex(lesson_num, chunk_title, content,
                         [f"{pdf_path.name} pag.{p_start}-{p_end}"], out_tex)
        tex_files.append(out_tex)

    return tex_files
# ─────────────────────────────────────────────
# STEP 5: DOCX
# ─────────────────────────────────────────────
def extract_docx(docx_path: Path) -> str:
    try:
        from docx import Document
        doc = Document(str(docx_path))
        paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        print(f"    ✓ docx: {len(paras)} paragrafi")
        return "\n".join(paras)
    except ImportError:
        print("    [MANCANTE] python-docx — pip install python-docx")
        return ""


# ─────────────────────────────────────────────
# STEP 6: GENERAZIONE LaTeX CON CLAUDE
# (con preprocessor integrato)
# ─────────────────────────────────────────────
try:
    from preprocessor import preprocess, NormalizedDocument, update_course_context
    PREPROCESSOR = True
except ImportError:
    PREPROCESSOR = False

CLAUDE_SYSTEM = """Sei un esperto di LaTeX accademico. Trasforma il contenuto normalizzato di una
lezione universitaria in un capitolo LaTeX professionale e strutturato.

REGOLE OBBLIGATORIE:
1. Rispondi SOLO con codice LaTeX valido, iniziando da \\section{...}
2. NON includere \\documentclass, \\begin{document} o \\end{document}
3. Struttura: \\section{} > \\subsection{} > \\subsubsection{}
4. Formule: \\begin{equation}...\\end{equation} oppure $...$ inline
5. Liste: \\begin{itemize} o \\begin{enumerate}
6. Aggiungi \\label{sec:...} a ogni section/subsection
7. Mantieni terminologia tecnica originale
8. Non copiare verbatim la trascrizione: sintetizza i concetti chiave
9. Esempi -> \\begin{example}...\\end{example}
10. Definizioni importanti -> \\begin{definition}...\\end{definition}"""

def generate_with_claude(lesson_number: int, title: str,
                          sources: dict,
                          subject_hint: str = None,
                          course_context_path: str = None) -> str | None:
    """
    Genera LaTeX da Claude con prompt strutturato e gerarchia semantica.

    sources = {
        "has_audio":  bool,
        "scheletro":  [{"filename", "text", "latex", "slide_count", "slide_images"}],
        "carne":      [{"filename", "text"}],
        "supporto":   [{"filename", "text", "pages"}],
        "contorno":   [{"filename", "text"}],
    }
    """
    import urllib.request, urllib.error
    import json, os, time
    from pathlib import Path

    api_key = os.environ.get("ANTHROPIC_API_KEY")

    # ─────────────────────────────────────────
    # SYSTEM PROMPT — invariante
    # ─────────────────────────────────────────
    system_prompt = """Sei un esperto di LaTeX accademico. Trasforma le fonti fornite in un capitolo LaTeX professionale.

REGOLE OBBLIGATORIE:
1. Rispondi SOLO con codice LaTeX valido, iniziando da \\section{...}
2. NON includere \\documentclass, \\begin{document} o \\end{document}
3. Struttura: \\section{} > \\subsection{} > \\subsubsection{}
4. Aggiungi \\label{sec:...} a ogni section e subsection
5. Formule inline: $...$ — formule proprie: \\begin{equation}...\\end{equation}
6. Liste: \\begin{itemize} o \\begin{enumerate}
7. Definizioni: \\begin{definition}...\\end{definition}
8. Teoremi: \\begin{theorem}...\\end{theorem}
9. Esempi: \\begin{example}...\\end{example}
10. Non copiare verbatim la trascrizione — sintetizza i concetti
11. Mantieni la terminologia tecnica originale del professore
12. Le formule [FORMULA_OMML] sono già verificate — usale direttamente
13. I blocchi \\begin{figure}...\\end{figure} nello SCHELETRO vanno mantenuti nella posizione esatta"""

    # ─────────────────────────────────────────
    # PREPROCESSOR — pulizia e contesto corso
    # ─────────────────────────────────────────
    course_context = ""
    subject        = subject_hint or "generico"

    if PREPROCESSOR:
        # Usa preprocessor solo per pulizia e contesto — non per assemblare il prompt
        carne_text    = "\n".join(s["text"] for s in sources["carne"])
        scheletro_raw = "\n".join(s["text"] for s in sources["scheletro"])

        doc = preprocess(
            transcript          = carne_text,
            slide_text          = scheletro_raw,
            extra_text          = "",
            title               = f"Lezione {lesson_number}: {title}",
            subject_hint        = subject_hint,
            course_context_path = course_context_path,
            lesson_number       = lesson_number,
        )
        subject        = doc.subject
        course_context = doc.context_prompt
        subject_instr  = doc.subject_prompt
    else:
        subject_instr = ""

    # ─────────────────────────────────────────
    # USER PROMPT — assemblaggio con gerarchia
    # ─────────────────────────────────────────
    sep  = "═" * 56
    sep2 = "─" * 56
    parts = []

    # ── Intestazione lezione ──
    has_scheletro = bool(sources["scheletro"])
    has_carne     = bool(sources["carne"])
    has_supporto  = bool(sources["supporto"])
    has_contorno  = bool(sources["contorno"])

    fonti_str = []
    if has_scheletro: fonti_str.append("scheletro")
    if has_carne:     fonti_str.append("carne")
    if has_supporto:  fonti_str.append("supporto")
    if has_contorno:  fonti_str.append("contorno")

    parts.append(
        f"{sep}\n"
        f"  LEZIONE {lesson_number}: {title}\n"
        f"  Materia: {subject} | Fonti: {', '.join(fonti_str)}\n"
        f"{sep}"
    )

    # ── Contesto corso ──
    if course_context:
        parts.append(f"{sep}\n  CONTESTO CORSO\n{sep}\n{course_context}")

    # ── Istruzioni materia ──
    if subject_instr:
        parts.append(f"{sep}\n  ISTRUZIONI MATERIA\n{sep}\n{subject_instr}")

    # ── SCHELETRO ──
    if has_scheletro:
        parts.append(f"{sep}\n  FONTE: SCHELETRO\n{sep}")

        for entry in sources["scheletro"]:
            filename    = entry["filename"]
            latex       = entry.get("latex")
            text        = entry.get("text", "")
            slide_count = entry.get("slide_count", "?")
            pages       = entry.get("pages", "?")

            is_pptx = filename.lower().endswith(".pptx")
            is_pdf  = filename.lower().endswith(".pdf")
            is_docx = filename.lower().endswith(".docx")

            if is_pptx:
                meta = f"File: {filename} | {slide_count} slide"
                role = (
                    "Struttura UFFICIALE della lezione generata dalle slide.\n"
                    "REGOLE:\n"
                    "  • Ogni \\subsection corrisponde a una slide — non cambiare questa struttura\n"
                    "  • I blocchi \\begin{figure} contengono le immagini delle slide — mantienili nella posizione esatta\n"
                    "  • Le formule [FORMULA_OMML] sono già in LaTeX verificato — usale direttamente\n"
                    "  • Arricchisci il contenuto testuale con la CARNE (trascrizione)"
                )
                content = latex if latex else text

            elif is_pdf:
                meta = f"File: {filename} | {pages} pagine"
                role = (
                    "Documento PDF che funge da SCHELETRO perché è presente la trascrizione audio.\n"
                    "REGOLE:\n"
                    "  • Usa la struttura del documento (titoli, sezioni) come guida per \\subsection\n"
                    "  • Ogni [PAG N] è una pagina del documento — usala come unità strutturale\n"
                    "  • Arricchisci con la CARNE (trascrizione audio)"
                )
                content = entry.get("latex") or text  # ← usa scheletro se disponibile

            elif is_docx:
                meta = f"File: {filename}"
                role = (
                    "Documento Word che funge da SCHELETRO perché è presente la trascrizione audio.\n"
                    "REGOLE:\n"
                    "  • Usa i paragrafi principali come guida per \\subsection\n"
                    "  • Arricchisci con la CARNE (trascrizione audio)"
                )
                content = text

            else:
                meta    = f"File: {filename}"
                role    = "Documento strutturale della lezione."
                content = text

            parts.append(
                f"{sep2}\n{meta}\n\n{role}\n{sep2}\n{content}"
            )

    # ── CARNE ──
    if has_carne:
        parts.append(f"{sep}\n  FONTE: CARNE (voce del professore)\n{sep}")

        for entry in sources["carne"]:
            filename = entry["filename"]
            text     = entry["text"]
            parts.append(
                f"{sep2}\n"
                f"File: {filename}\n\n"
                f"Trascrizione della VOCE DEL PROFESSORE durante la lezione.\n"
                f"REGOLE:\n"
                f"  • Integra le spiegazioni nelle \\subsection corrispondenti dello SCHELETRO\n"
                f"  • Le ripetizioni di un concetto indicano importanza — enfatizzalo\n"
                f"  • Gli esempi verbali non presenti nello scheletro → \\begin{{example}}\n"
                f"  • Le frasi 'quindi', 'in altre parole', 'ricordate' → spiegazioni chiave\n"
                f"  • I timestamp [MM:SS] indicano la progressione temporale\n"
                f"{sep2}\n{text}"
            )

    # ── SUPPORTO ──
    if has_supporto:
        parts.append(f"{sep}\n  FONTE: SUPPORTO (materiale di riferimento)\n{sep}")

        for entry in sources["supporto"]:
            filename = entry["filename"]
            text     = entry["text"]
            pages    = entry.get("pages", "")
            meta     = f"File: {filename}" + (f" | {pages} pagine" if pages else "")
            parts.append(
                f"{sep2}\n"
                f"{meta}\n\n"
                f"Materiale di SUPPORTO — non è la struttura della lezione.\n"
                f"REGOLE:\n"
                f"  • Usalo per arricchire definizioni formali dove scheletro/carne sono sintetici\n"
                f"  • Non cambiare la struttura \\subsection per adattarla a questo documento\n"
                f"{sep2}\n{text}"
            )

    # ── CONTORNO ──
    if has_contorno:
        parts.append(f"{sep}\n  FONTE: CONTORNO (note informali)\n{sep}")

        for entry in sources["contorno"]:
            filename = entry["filename"]
            text     = entry["text"]
            parts.append(
                f"{sep2}\n"
                f"File: {filename}\n\n"
                f"Note informali — peso MINORE rispetto alle altre fonti.\n"
                f"Usa solo per dettagli non coperti altrove.\n"
                f"{sep2}\n{text}"
            )

    # ── ISTRUZIONI DI SINTESI — adattive ──
    parts.append(f"{sep}\n  ISTRUZIONI DI SINTESI\n{sep}")
    instructions = _build_synthesis_instructions(
        lesson_number, title,
        has_scheletro, has_carne, has_supporto,
        sources,
    )
    parts.append(instructions)

    user_prompt = "\n\n".join(parts)

    # ─────────────────────────────────────────
    # DEBUG — salva prompt su disco
    # ─────────────────────────────────────────
    debug_dir  = Path("debug")
    debug_dir.mkdir(exist_ok=True)
    debug_path = debug_dir / f"prompt_lezione_{lesson_number:02d}.txt"
    debug_path.write_text(
        f"=== SYSTEM ===\n{system_prompt}\n\n=== USER ===\n{user_prompt}",
        encoding="utf-8"
    )
    est_tokens = (len(system_prompt) + len(user_prompt)) // 4
    print(f"\n  [DEBUG] Prompt → {debug_path.resolve()}")
    print(f"  [DEBUG] ~{est_tokens:,} token stimati")
    print(f"  [DEBUG] Fonti: scheletro={len(sources['scheletro'])} "
          f"carne={len(sources['carne'])} "
          f"supporto={len(sources['supporto'])} "
          f"contorno={len(sources['contorno'])}")

    if not api_key:
        print("  [SKIP] ANTHROPIC_API_KEY non impostata")
        return None

    # ─────────────────────────────────────────
    # CHIAMATA API
    # ─────────────────────────────────────────
    payload = json.dumps({
        "model":      CONFIG["claude_model"],
        "max_tokens": CONFIG["claude_max_tokens"],
        "system":     system_prompt,
        "messages":   [{"role": "user", "content": user_prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data    = payload,
        headers = {
            "Content-Type":      "application/json",
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
        },
        method = "POST",
    )

    print(f"  Claude API: lezione {lesson_number} ...")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req) as resp:
            data  = json.loads(resp.read())
            latex = data["content"][0]["text"]
            print(f"  ✓ Claude: {time.time()-t0:.1f}s, {len(latex):,} chars")

            if PREPROCESSOR and course_context_path:
                update_course_context(
                    context_path  = course_context_path,
                    lesson_number = lesson_number,
                    lesson_title  = title,
                    latex_content = latex,
                )
            return latex

    except urllib.error.HTTPError as e:
        print(f"  [ERRORE] Claude HTTP {e.code}: {e.read().decode()[:300]}")
        return None
    except Exception as e:
        print(f"  [ERRORE] Claude: {e}")
        return None


# ─────────────────────────────────────────────
# ISTRUZIONI ADATTIVE — cambiano in base alle fonti
# ─────────────────────────────────────────────

def _build_synthesis_instructions(lesson_number: int, title: str,
                                   has_scheletro: bool, has_carne: bool,
                                   has_supporto: bool, sources: dict) -> str:
    lines = [
        f"Genera: \\section{{Lezione {lesson_number}: {title}}} e tutto il contenuto.",
        "",
    ]

    # Caso 1: scheletro PPTX + carne audio — caso ideale
    has_pptx_skeleton = any(
        s["filename"].lower().endswith(".pptx")
        for s in sources.get("scheletro", [])
    )

    if has_pptx_skeleton and has_carne:
        lines += [
            "CASO: slide + audio (caso ideale)",
            "1. Parti dallo SCHELETRO — mantieni ogni \\subsection e ogni \\begin{figure} nella posizione esatta",
            "2. Per ogni \\subsection integra la spiegazione dalla CARNE corrispondente per timestamp",
            "3. Se scheletro e carne si contraddicono → privilegia lo SCHELETRO",
            "4. Esempi verbali del professore non nello scheletro → aggiungi come \\begin{example}",
            "5. Formule [FORMULA_OMML] → copia direttamente senza modifiche",
            "6. Formule pronunciate nella trascrizione → converti tu in LaTeX",
        ]

    # Caso 2: solo scheletro PPTX, no audio
    elif has_pptx_skeleton and not has_carne:
        lines += [
            "CASO: solo slide (nessun audio)",
            "1. Mantieni ogni \\subsection e ogni \\begin{figure} nella posizione esatta",
            "2. Espandi ogni punto della slide con elaborazione accademica approfondita",
            "3. Aggiungi contesto, motivazioni e implicazioni per ogni concetto",
            "4. Formule [FORMULA_OMML] → copia direttamente senza modifiche",
        ]

    # Caso 3: scheletro PDF/DOCX + carne audio
    elif has_scheletro and has_carne:
        lines += [
            "CASO: documento + audio",
            "1. Usa la struttura del documento come guida per \\subsection",
            "2. Riempi ogni sezione con le spiegazioni dalla trascrizione",
            "3. Se documento e trascrizione si contraddicono → privilegia il documento",
        ]

    # Caso 4: solo audio, niente scheletro
    elif not has_scheletro and has_carne:
        lines += [
            "CASO: solo audio (nessuno scheletro)",
            "1. Struttura autonomamente identificando i macro-argomenti nella trascrizione",
            "2. Ogni cambio di argomento → nuova \\subsection",
            "3. Segui l'ordine cronologico della lezione",
            "4. Formule pronunciate → converti in LaTeX",
        ]

    # Caso 5: solo documenti, niente audio
    elif has_scheletro and not has_carne:
        lines += [
            "CASO: solo documenti (nessun audio)",
            "1. Riformatta il documento in LaTeX strutturato",
            "2. Ogni sezione/capitolo del documento → \\subsection",
            "3. Espandi dove necessario per chiarezza accademica",
        ]

    if has_supporto:
        lines += [
            "",
            "SUPPORTO disponibile:",
            "• Usa per arricchire definizioni formali dove scheletro/carne sono sintetici",
            "• Non cambiare la struttura \\subsection per adattarla al supporto",
        ]

    lines += [
        "",
        "QUALITÀ ATTESA:",
        "• Struttura gerarchica chiara e navigabile",
        "• Ogni concetto spiegato, non solo elencato",
        "• Formule matematiche corrette e leggibili",
        "• Pronto per compilazione con pdflatex",
    ]

    return "\n".join(lines)# ─────────────────────────────────────────────
# STEP 7: LaTeX STRUTTURATO SENZA AI
# Usa i dati del collega quando disponibili
# ─────────────────────────────────────────────
def build_fallback_latex(lesson_number: int, title: str,
                          slides, transcript: str,
                          slide_text: str, extra_text: str,
                          slide_images: dict = None) -> str:
    """
    Costruisce LaTeX strutturato senza Claude.

    Ogni \subsection corrisponde a una slide e include:
      1. \begin{figure} con il PNG della slide (se disponibile)
      2. Contenuto testuale della slide (testo + formule OMML)

    slide_images: {slide_number: "slide_001.png", ...}
                  prodotto da render_slide_images()
    """
    slide_images = slide_images or {}

    if COLLEAGUE_MODULES and slides:
        from slide_renderer import slide_figure_latex
        esc = _escape_latex
        parts = []
        parts.append(f"\\section{{Lezione {lesson_number}: {esc(title)}}}")
        parts.append(f"\\label{{sec:lezione{lesson_number:02d}}}\n")

        for slide in slides:
            sec_title = slide.title.strip() if slide.title.strip() else f"Slide {slide.slide_number}"
            parts.append(f"\n\\subsection{{{esc(sec_title)}}}")
            parts.append(f"\\label{{subsec:slide{lesson_number:02d}-{slide.slide_number}}}\n")

            # ── Figura slide PNG (se disponibile) ──
            img_filename = slide_images.get(slide.slide_number)
            if img_filename:
                parts.append(slide_figure_latex(
                    img_filename = img_filename,
                    slide_number = slide.slide_number,
                    caption      = sec_title,
                ))

            # ── Contenuto testuale + formule ──
            for obj in slide.objects:
                if obj.obj_type == "text":
                    if obj.content.strip() == slide.title.strip():
                        continue
                    lines = obj.content.strip().split("\n")
                    has_bullets = any(
                        l.strip().startswith(("•", "-", "*", "–"))
                        for l in lines
                    )
                    if has_bullets:
                        parts.append("\\begin{itemize}")
                        for l in lines:
                            l = l.strip()
                            if l:
                                parts.append(f"  \\item {esc(l.lstrip('•-*– ').strip())}")
                        parts.append("\\end{itemize}\n")
                    else:
                        body = "\n".join(esc(l) for l in lines if l.strip())
                        if body:
                            parts.append(body + "\n")

                elif obj.obj_type == "omml_formula":
                    f = getattr(obj, "latex_result", "")
                    if f and not f.startswith("%"):
                        parts.append("\\begin{equation}")
                        parts.append(f)
                        parts.append("\\end{equation}\n")
                    elif f:
                        parts.append(f"% OMML parziale:\n% {f}\n")

                elif obj.obj_type == "image":
                    f = getattr(obj, "latex_result", "")
                    img = obj.content
                    if f and f.strip():
                        # Formula riconosciuta da pix2tex
                        parts.append("\\begin{equation}")
                        parts.append(f)
                        parts.append("\\end{equation}\n")
                    else:
                        # Immagine embedded normale
                        parts.append("\\begin{figure}[H]")
                        parts.append("  \\centering")
                        parts.append(f"  \\includegraphics[width=0.8\\textwidth]{{{img}}}")
                        parts.append("\\end{figure}\n")

        # Trascrizione in fondo se disponibile
        if transcript:
            parts.append("\n\\subsection{Trascrizione Audio}")
            parts.append("\\begin{quote}")
            for line in transcript.split("\n")[:60]:
                parts.append(_escape_latex(line))
            if len(transcript.split("\n")) > 60:
                parts.append("\\emph{[trascrizione troncata]}")
            parts.append("\\end{quote}\n")

        return "\n".join(parts)

    # ── Fallback puro (nessun modulo collega) ──
    def esc(t):
        for a, b in [("\\", "\\textbackslash{}"), ("&", "\\&"), ("%", "\\%"),
                     ("$", "\\$"), ("#", "\\#"), ("{", "\\{"), ("}", "\\}")]:
            t = t.replace(a, b)
        return t

    parts = []
    parts.append(f"\\section{{Lezione {lesson_number}: {esc(title)}}}\n")

    if slide_text:
        current_slide_num = None
        for line in slide_text.split("\n"):
            line = line.strip()
            if line.startswith("--- SLIDE"):
                # Estrai numero slide per recuperare il PNG
                import re
                m = re.match(r"--- SLIDE (\d+)", line)
                current_slide_num = int(m.group(1)) if m else None
                parts.append(f"\n\\subsection{{{esc(line)}}}\n")
                # Aggiungi figura se disponibile
                if current_slide_num and current_slide_num in slide_images:
                    from slide_renderer import slide_figure_latex
                    parts.append(slide_figure_latex(
                        img_filename = slide_images[current_slide_num],
                        slide_number = current_slide_num,
                    ))
            elif line:
                parts.append(esc(line) + "\n")

    if transcript:
        parts.append("\\subsection{Trascrizione Audio}\n\\begin{quote}")
        for line in transcript.split("\n")[:50]:
            parts.append(esc(line))
        parts.append("\\end{quote}\n")

    if extra_text:
        parts.append("\\subsection{Note Aggiuntive}\n\\begin{quote}")
        for line in extra_text.split("\n")[:30]:
            parts.append(esc(line))
        parts.append("\\end{quote}\n")

    return "\n".join(parts)
# ─────────────────────────────────────────────
# SCRITTURA lezione_NN.tex
# ─────────────────────────────────────────────
def write_lesson_tex(lesson_number: int, title: str,
                     content: str, sources: list, out_path: Path):
    header = (
        f"% {'='*60}\n"
        f"% Lezione {lesson_number:02d}: {title}\n"
        f"% Generato: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"% Fonti: {', '.join(sources) if sources else '—'}\n"
        f"% {'='*60}\n\n"
    )
    out_path.write_text(header + content, encoding="utf-8")
    kb = out_path.stat().st_size // 1024 + 1
    print(f"    ✓ {out_path.name}  ({kb} KB)")


# ─────────────────────────────────────────────
# GENERAZIONE main.tex
# ─────────────────────────────────────────────
MAIN_TEMPLATE = r"""\documentclass[12pt,a4paper]{{report}}

%% Encoding e lingua
\usepackage[utf8]{{inputenc}}
\usepackage[T1]{{fontenc}}
\usepackage[english,italian]{{babel}}

%% Matematica
\usepackage{{amsmath,amssymb,amsthm,mathtools}}

%% Layout
\usepackage[margin=2.5cm]{{geometry}}
\usepackage{{microtype,setspace}}
\onehalfspacing

%% Immagini
\usepackage{{graphicx,float}}
\graphicspath{{{{{images_path}}}}}

%% Header/Footer
\usepackage{{fancyhdr}}
\pagestyle{{fancy}}\fancyhf{{}}
\rhead{{\thepage}}\lhead{{\leftmark}}

%% Link
\usepackage{{hyperref}}
\hypersetup{{colorlinks=true,linkcolor=blue,urlcolor=blue}}

%% Ambienti teoremi
\newtheorem{{theorem}}{{Teorema}}[chapter]
\newtheorem{{lemma}}[theorem]{{Lemma}}
\newtheorem{{corollary}}[theorem]{{Corollario}}
\newtheorem{{definition}}[theorem]{{Definizione}}
\newtheorem{{example}}[theorem]{{Esempio}}
\newtheorem{{remark}}[theorem]{{Osservazione}}

%% Codice
\usepackage{{listings,xcolor}}
\lstset{{basicstyle=\ttfamily\small,breaklines=true,
        frame=single,backgroundcolor=\color{{gray!10}}}}

%% ──────────────────────
\title{{{title}\\[1ex]\large Appunti del Corso}}
\author{{Appunti AI}}
\date{{{date}}}
%% ──────────────────────

\begin{{document}}
\maketitle
\tableofcontents
\clearpage

{includes}

\end{{document}}
"""

def generate_main_tex(title: str, lesson_files: list, output_dir: Path) -> Path:
    includes = "\n".join(f"\\include{{{f.stem}}}" for f in sorted(lesson_files))
    content = MAIN_TEMPLATE.format(
        title=title,
        date=datetime.now().strftime("%B %Y"),
        images_path=CONFIG["images_subdir"] + "/",
        includes=includes,
    )
    main_path = output_dir / "main.tex"
    main_path.write_text(content, encoding="utf-8")
    print(f"  ✓ main.tex  ({len(lesson_files)} lezioni incluse)")
    return main_path


# ─────────────────────────────────────────────
# CORE: PROCESSA UNA LEZIONE
# ─────────────────────────────────────────────

def process_lesson(source_dir: Path, lesson_number: int, output_dir: Path,
                   skip_ai: bool = False, skip_ocr: bool = False,
                   whisper_model: str = "base",
                   subject_hint: str = None,
                   course_context_path: str = None):

    print(f"\n{'─'*58}")
    print(f"  LEZIONE {lesson_number:02d}  ←  {source_dir.name}")
    print(f"{'─'*58}")

    all_files = list(source_dir.iterdir()) if source_dir.is_dir() else [source_dir]

    by = {k: [] for k in ("audio", "video", "slide", "doc", "pdf", "text")}
    for f in all_files:
        ext = f.suffix.lower()
        if   ext in CONFIG["ext_audio"]: by["audio"].append(f)
        elif ext in CONFIG["ext_video"]: by["video"].append(f)
        elif ext in CONFIG["ext_slide"]: by["slide"].append(f)
        elif ext in CONFIG["ext_doc"]:   by["doc"].append(f)
        elif ext in CONFIG["ext_pdf"]:   by["pdf"].append(f)
        elif ext in CONFIG["ext_text"]:  by["text"].append(f)

    labels = (
        [f"audio:{f.name}" for f in by["audio"]] +
        [f"video:{f.name}" for f in by["video"]] +
        [f"pptx:{f.name}"  for f in by["slide"]] +
        [f"docx:{f.name}"  for f in by["doc"]]   +
        [f"pdf:{f.name}"   for f in by["pdf"]]   +
        [f"txt:{f.name}"   for f in by["text"]]
    )
    if not labels:
        print("  [SKIP] Nessun file riconosciuto")
        return None
    print(f"  Fonti trovate: {', '.join(labels)}")

    tmp_dir    = output_dir / f"_tmp_{lesson_number:02d}"
    images_dir = output_dir / CONFIG["images_subdir"]
    tmp_dir.mkdir(exist_ok=True)
    images_dir.mkdir(exist_ok=True)

    # ─────────────────────────────────────────────
    # DIZIONARIO FONTI — struttura che passa a Claude
    # ─────────────────────────────────────────────
    sources = {
        "scheletro": [],  # struttura della lezione
        "carne":     [],  # spiegazione orale del professore
        "supporto":  [],  # materiale di approfondimento
        "contorno":  [],  # note informali, peso minore
        "has_audio": False,
    }
    source_names  = []
    pptx_slides   = None  # oggetti slide del collega (per fallback)

    # ─────────────────────────────────────────────
    # STEP 1: AUDIO → CARNE (sempre)
    # ─────────────────────────────────────────────
    for af in by["audio"]:
        print(f"\n  [Audio → CARNE] {af.name}")
        t = transcribe_audio(af, whisper_model)
        if t:
            sources["carne"].append({"filename": af.name, "text": t})
            sources["has_audio"] = True
            source_names.append(af.name)

    # ─────────────────────────────────────────────
    # STEP 2: VIDEO → CARNE (sempre)
    # ─────────────────────────────────────────────
    for vf in by["video"]:
        print(f"\n  [Video → CARNE] {vf.name}")
        mp3 = extract_audio_from_video(vf, tmp_dir)
        if mp3:
            t = transcribe_audio(mp3, whisper_model)
            if t:
                sources["carne"].append({"filename": vf.name, "text": t})
                sources["has_audio"] = True
                source_names.append(vf.name)

    # ─────────────────────────────────────────────
    # STEP 3: PPTX → SCHELETRO (sempre)
    # ─────────────────────────────────────────────
    for sf in by["slide"]:
        print(f"\n  [PPTX → SCHELETRO] {sf.name}")
        if COLLEAGUE_MODULES:
            slides_obj, plain = process_pptx_full(sf, images_dir, skip_ocr=skip_ocr)
            pptx_slides = slides_obj

            # ← PRIMA renderizza le slide come PNG
            from slide_renderer import render_slide_images
            slide_images = render_slide_images(sf, images_dir)

            # ← POI costruisci lo scheletro LaTeX con i PNG
            skeleton_latex = build_fallback_latex(
                lesson_number = lesson_number,
                title         = source_dir.name.replace("_"," ").replace("-"," ").title(),
                slides        = slides_obj,
                transcript    = "",
                slide_text    = plain,
                extra_text    = "",
                slide_images  = slide_images,
            )
            sources["scheletro"].append({
                "filename":    sf.name,
                "text":        plain,
                "latex":       skeleton_latex,
                "slide_count": len(slides_obj),
                "slide_images": slide_images,
            })
        else:
            plain = process_pptx_fallback(sf)
            sources["scheletro"].append({
                "filename":    sf.name,
                "text":        plain,
                "latex":       None,
                "slide_count": plain.count("--- SLIDE"),
            })
        source_names.append(sf.name)
    # ─────────────────────────────────────────────
    # STEP 4: PDF — SCHELETRO se c'è audio, SUPPORTO se no
    # ─────────────────────────────────────────────
    for pf in by["pdf"]:
        pages = extract_pdf_pages(pf)
        if not pages:
            continue

        # PDF grande senza audio → chunking (una lezione per chunk)
        if len(pages) > 20 and not sources["has_audio"]:
            print(f"\n  [PDF grande → chunking] {pf.name}")
            chunk_files = process_pdf_chunked(
                pdf_path           = pf,
                output_dir         = output_dir,
                base_lesson_number = lesson_number,
                title              = source_dir.name.replace("_"," ").replace("-"," ").title(),
                skip_ai            = skip_ai,
                chunk_size         = 10,
            )
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return chunk_files

        # PDF piccolo o con audio → rendering + entry normale
        from pdf_renderer import render_pdf_pages
        page_images, latex_skeleton = render_pdf_pages(pf, images_dir, pages)

        text  = "\n".join(f"[PAG {p['page']}]\n{p['text']}" for p in pages)
        entry = {
            "filename":    pf.name,
            "text":        text,
            "pages":       len(pages),
            "latex":       latex_skeleton,
            "page_images": page_images,
        }

        if sources["has_audio"]:
            print(f"\n  [PDF → SCHELETRO] {pf.name}  (c'è audio)")
            sources["scheletro"].append(entry)
        else:
            print(f"\n  [PDF → SUPPORTO] {pf.name}  (nessun audio)")
            sources["supporto"].append(entry)

        source_names.append(pf.name)
    # ─────────────────────────────────────────────
    # STEP 5: DOCX — SCHELETRO se c'è audio, SUPPORTO se no
    # ─────────────────────────────────────────────
    for df in by["doc"]:
        text = extract_docx(df)
        if not text:
            continue
        entry = {"filename": df.name, "text": text}

        if sources["has_audio"]:
            print(f"\n  [DOCX → SCHELETRO] {df.name}  (c'è audio)")
            sources["scheletro"].append(entry)
        else:
            print(f"\n  [DOCX → SUPPORTO] {df.name}  (nessun audio)")
            sources["supporto"].append(entry)

        source_names.append(df.name)

    # ─────────────────────────────────────────────
    # STEP 6: TXT/MD → CONTORNO (sempre)
    # ─────────────────────────────────────────────
    for tf in by["text"]:
        print(f"\n  [TXT → CONTORNO] {tf.name}")
        text = tf.read_text(encoding="utf-8", errors="ignore")
        if text.strip():
            sources["contorno"].append({"filename": tf.name, "text": text})
            source_names.append(tf.name)

    # ─────────────────────────────────────────────
    # LOG GERARCHIA RISOLTA
    # ─────────────────────────────────────────────
    print(f"\n  Gerarchia risolta:")
    print(f"    SCHELETRO : {[s['filename'] for s in sources['scheletro']] or '—'}")
    print(f"    CARNE     : {[s['filename'] for s in sources['carne']] or '—'}")
    print(f"    SUPPORTO  : {[s['filename'] for s in sources['supporto']] or '—'}")
    print(f"    CONTORNO  : {[s['filename'] for s in sources['contorno']] or '—'}")

    # ─────────────────────────────────────────────
    # STEP 7: GENERAZIONE LaTeX
    # ─────────────────────────────────────────────
    title   = source_dir.name.replace("_", " ").replace("-", " ").title()
    out_tex = output_dir / f"lezione_{lesson_number:02d}.tex"

    def _latex_from_skeleton(sources, lesson_number, title, pptx_slides):
        """Cerca il LaTeX scheletro in scheletro poi in supporto, fallback testo puro."""
        entry = (
            next((s for s in sources["scheletro"] if s.get("latex")), None) or
            next((s for s in sources["supporto"]  if s.get("latex")), None)
        )
        if entry:
            return (
                f"\\section{{Lezione {lesson_number}: {title}}}\n"
                f"\\label{{sec:lezione{lesson_number:02d}}}\n\n"
                + entry["latex"]
            )
        # fallback testo puro
        slide_text = "\n".join(s["text"] for s in sources["scheletro"])
        carne_text = "\n".join(s["text"] for s in sources["carne"])
        extra_text = "\n".join(s["text"] for s in sources["supporto"] + sources["contorno"])
        return build_fallback_latex(
            lesson_number, title, pptx_slides,
            carne_text, slide_text, extra_text,
        )

    print()
    if skip_ai:
        print("  [--skip-ai] LaTeX strutturato senza Claude")
        content = _latex_from_skeleton(sources, lesson_number, title, pptx_slides)
    else:
        content = generate_with_claude(
            lesson_number       = lesson_number,
            title               = title,
            sources             = sources,
            subject_hint        = subject_hint,
            course_context_path = course_context_path,
        )
        if not content:
            print("  [fallback] Claude non raggiunto, uso scheletro")
            content = _latex_from_skeleton(sources, lesson_number, title, pptx_slides)

    write_lesson_tex(lesson_number, title, content, source_names, out_tex)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return out_tex
# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Appunti AI — fonti miste -> libro LaTeX",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("source",
        help="Cartella lezione (singola) o cartella corso (con --batch)")
    parser.add_argument("--batch", action="store_true",
        help="Ogni sottocartella = una lezione")
    parser.add_argument("--output", default="./output",
        help="Cartella di output (default: ./output)")
    parser.add_argument("--title", default="Appunti del Corso",
        help="Titolo del corso per main.tex")
    parser.add_argument("--skip-ai", action="store_true",
        help="Non usare Claude (usa struttura dal collega, offline)")
    parser.add_argument("--skip-ocr", action="store_true",
        help="Non usare pix2tex OCR (più veloce)")
    parser.add_argument("--whisper-model", default=CONFIG["whisper_model"],
        choices=["tiny","base","small","medium","large"],
        help="Modello Whisper (default: base)")
    parser.add_argument("--start-from", type=int, default=1,
        help="Inizia numerazione lezioni da N")
    parser.add_argument("--subject",
        choices=["ingegneria","matematica","fisica","medicina",
                "economia","giurisprudenza","generico"],
        help="Tipo di materia (default: auto-detect)")
    parser.add_argument("--no-context", action="store_true",
        help="Non usare/aggiornare corso_context.json")
    args = parser.parse_args()

    source_path  = Path(args.source)
    output_dir   = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / CONFIG["images_subdir"]).mkdir(exist_ok=True)

    # Path contesto corso (auto, nella cartella output)
    course_context_path = None
    if PREPROCESSOR and not args.no_context and not args.skip_ai:
        course_context_path = str(output_dir / "corso_context.json")

    print(f"\n{'═'*58}")
    print(f"  APPUNTI AI")
    print(f"  Output  : {output_dir.resolve()}")
    print(f"  Titolo  : {args.title}")
    ai_str  = "OFF (--skip-ai)" if args.skip_ai else f"Claude {CONFIG['claude_model']}"
    ocr_str = "OFF (--skip-ocr)" if args.skip_ocr else "pix2tex"
    sub_str = args.subject or "auto-detect"
    ctx_str = course_context_path or "OFF (--no-context)"
    print(f"  Claude  : {ai_str}")
    print(f"  Whisper : {args.whisper_model}")
    print(f"  OCR     : {ocr_str}")
    print(f"  Materia : {sub_str}")
    print(f"  Contesto: {ctx_str}")
    print(f"{'═'*58}")

    lesson_files = []

    def collect(result):
        """Gestisce sia Path singolo che lista di Path (PDF chunked)."""
        if result is None:
            return
        if isinstance(result, list):
            lesson_files.extend(result)
        else:
            lesson_files.append(result)

    if args.batch:
        subdirs = sorted(d for d in source_path.iterdir() if d.is_dir())
        if not subdirs:
            print(f"[ERRORE] Nessuna sottocartella in {source_path}")
            sys.exit(1)
        print(f"\n  {len(subdirs)} lezioni trovate")
        for i, subdir in enumerate(subdirs, start=args.start_from):
            result = process_lesson(
                subdir, i, output_dir,
                skip_ai             = args.skip_ai,
                skip_ocr            = args.skip_ocr,
                whisper_model       = args.whisper_model,
                subject_hint        = args.subject,
                course_context_path = course_context_path,
            )
            collect(result)
    else:
        result = process_lesson(
            source_path, args.start_from, output_dir,
            skip_ai             = args.skip_ai,
            skip_ocr            = args.skip_ocr,
            whisper_model       = args.whisper_model,
            subject_hint        = args.subject,
            course_context_path = course_context_path,
        )
        collect(result)

    if lesson_files:
        generate_main_tex(args.title, lesson_files, output_dir)
        print(f"\n{'═'*58}")
        print(f"  COMPLETATO — {len(lesson_files)} lezioni")
        print(f"\n  Compila il PDF:")
        print(f"    cd {output_dir.resolve()}")
        print(f"    pdflatex main.tex && pdflatex main.tex")
        print(f"{'═'*58}\n")
    else:
        print("\n[ATTENZIONE] Nessun .tex generato.")
        sys.exit(1)


if __name__ == "__main__":
    main()
