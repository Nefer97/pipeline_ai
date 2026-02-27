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
# IMPORT MODULI DEL COLLEGA
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


# ─────────────────────────────────────────────
# STEP 3a: PIPELINE PPTX (moduli collega)
# ─────────────────────────────────────────────
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


# STEP 3b: fallback pptx senza moduli collega
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
# STEP 4: DOCX
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
# STEP 5: PDF — estrazione a chunk di pagine
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
    pages = extract_pdf_pages(pdf_path)
    if not pages:
        print("  [ERRORE] Nessun testo estratto dal PDF")
        return []

    total_pages = pages[-1]["page"] if pages else 0
    chunks = chunk_pdf_pages(pages, chunk_size)
    print(f"  {total_pages} pagine totali → {len(chunks)} chunk da ~{chunk_size} pag")

    tex_files = []
    for idx, chunk in enumerate(chunks):
        lesson_num = base_lesson_number + idx
        p_start = chunk[0]["page"]
        p_end   = chunk[-1]["page"]
        chunk_title = f"{title} — pag. {p_start}–{p_end}"
        chunk_text  = "\n\n".join(
            f"[PAG {p['page']}]\n{p['text']}" for p in chunk
        )

        out_tex = output_dir / f"lezione_{lesson_num:02d}.tex"
        print(f"\n  Chunk {idx+1}/{len(chunks)}: pag {p_start}–{p_end}")

        if skip_ai:
            # Fallback strutturato senza Claude
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
            content = generate_with_claude(
                lesson_number=lesson_num,
                title=chunk_title,
                transcript="",
                slide_text=chunk_text,
                extra_text="",
            )
            if not content:
                # fallback
                def esc(t):
                    for a, b in [("\\","\\textbackslash{}"),("&","\\&"),
                                  ("%","\\%"),("$","\\$"),("#","\\#")]:
                        t = t.replace(a, b)
                    return t
                content = f"\\section{{{esc(chunk_title)}}}\n\\begin{{quote}}\n"
                content += "\n".join(esc(p["text"]) for p in chunk)
                content += "\n\\end{quote}\n"

        write_lesson_tex(lesson_num, chunk_title, content,
                          [f"{pdf_path.name} pag.{p_start}-{p_end}"], out_tex)
        tex_files.append(out_tex)

    return tex_files


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
                          transcript: str, slide_text: str, extra_text: str,
                          subject_hint: str = None,
                          course_context_path: str = None):
    import urllib.request, urllib.error
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("    [SKIP] ANTHROPIC_API_KEY non trovata")
        print("           Imposta: export ANTHROPIC_API_KEY='sk-ant-...'")
        return None

    # ── Preprocessor ──
    if PREPROCESSOR:
        doc = preprocess(
            transcript          = transcript,
            slide_text          = slide_text,
            extra_text          = extra_text,
            title               = f"Lezione {lesson_number}: {title}",
            subject_hint        = subject_hint,
            course_context_path = course_context_path,
            lesson_number       = lesson_number,
        )
        user_content = (
            doc.to_prompt()
            + f"\n\nGenera il capitolo LaTeX. "
            + f"Inizia con \\section{{Lezione {lesson_number}: {title}}}."
        )
    else:
        # Fallback legacy (niente preprocessor)
        parts = [f"# Lezione {lesson_number}: {title}\n"]
        if transcript:
            t = transcript[:6000] + "\n[...]" if len(transcript) > 6000 else transcript
            parts.append(f"## TRASCRIZIONE AUDIO\n{t}\n")
        if slide_text:
            s = slide_text[:3000] + "\n[...]" if len(slide_text) > 3000 else slide_text
            parts.append(f"## CONTENUTO SLIDE\n{s}\n")
        if extra_text:
            e = extra_text[:2000] + "\n[...]" if len(extra_text) > 2000 else extra_text
            parts.append(f"## DOCUMENTI AGGIUNTIVI\n{e}\n")
        parts.append(
            f"\nGenera il capitolo LaTeX. "
            f"Inizia con \\section{{Lezione {lesson_number}: {title}}}."
        )
        user_content = "\n".join(parts)

    payload = json.dumps({
        "model": CONFIG["claude_model"],
        "max_tokens": CONFIG["claude_max_tokens"],
        "system": CLAUDE_SYSTEM,
        "messages": [{"role": "user", "content": user_content}]
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={"Content-Type": "application/json",
                 "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"},
        method="POST"
    )
    print(f"    Claude API: lezione {lesson_number} ...")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req) as resp:
            data  = json.loads(resp.read())
            latex = data["content"][0]["text"]
            print(f"    ✓ {time.time()-t0:.1f}s, {len(latex)} caratteri")
            # Aggiorna contesto corso se preprocessor disponibile
            if PREPROCESSOR and course_context_path:
                update_course_context(
                    context_path  = course_context_path,
                    lesson_number = lesson_number,
                    lesson_title  = title,
                    latex_content = latex,
                )
            return latex
    except urllib.error.HTTPError as e:
        print(f"    [ERRORE] Claude {e.code}: {e.read().decode()[:200]}")
        return None
    except Exception as e:
        print(f"    [ERRORE] {e}")
        return None


# ─────────────────────────────────────────────
# STEP 7: LaTeX STRUTTURATO SENZA AI
# Usa i dati del collega quando disponibili
# ─────────────────────────────────────────────
def build_fallback_latex(lesson_number: int, title: str,
                          slides, transcript: str,
                          slide_text: str, extra_text: str) -> str:
    """
    Costruisce LaTeX strutturato senza Claude.
    Se abbiamo le slides del collega, sfrutta tutta la logica
    di extractor (posizioni, OMML, OCR immagini) per generare
    subsections ricche. Altrimenti usa testo plain.
    """
    parts = []

    if COLLEAGUE_MODULES and slides:
        esc = _escape_latex
        parts.append(f"\\section{{Lezione {lesson_number}: {esc(title)}}}\n")
        parts.append(f"\\label{{sec:lezione{lesson_number:02d}}}\n")

        for slide in slides:
            sec_title = slide.title.strip() if slide.title.strip() else f"Slide {slide.slide_number}"
            parts.append(f"\n\\subsection{{{esc(sec_title)}}}")
            parts.append(f"\\label{{subsec:slide{lesson_number:02d}-{slide.slide_number}}}\n")

            for obj in slide.objects:
                if obj.obj_type == "text":
                    # Salta ripetizione titolo
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
                        parts.append(f"% Conversione parziale OMML:\n% {f}\n")

                elif obj.obj_type == "image":
                    f = getattr(obj, "latex_result", "")
                    img = obj.content
                    if f and f.strip():
                        # Immagine riconosciuta come formula da pix2tex
                        parts.append("\\begin{equation}")
                        parts.append(f)
                        parts.append("\\end{equation}\n")
                    else:
                        # Immagine normale
                        parts.append("\\begin{figure}[H]")
                        parts.append("\\centering")
                        parts.append(f"\\includegraphics[width=0.8\\textwidth]{{{img}}}")
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

    parts.append(f"\\section{{Lezione {lesson_number}: {esc(title)}}}\n")
    if slide_text:
        parts.append("\\subsection{Contenuto Slide}\n")
        for line in slide_text.split("\n"):
            line = line.strip()
            if line.startswith("--- SLIDE"):
                parts.append(f"\n\\subsubsection{{{esc(line)}}}\n")
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
    by = {k: [] for k in ("audio","video","slide","doc","pdf","text")}
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
    print(f"  Fonti: {', '.join(labels)}")

    tmp_dir = output_dir / f"_tmp_{lesson_number:02d}"
    tmp_dir.mkdir(exist_ok=True)
    images_dir = output_dir / CONFIG["images_subdir"]
    images_dir.mkdir(exist_ok=True)

    transcript_text = ""
    slide_text = ""
    extra_text = ""
    pptx_slides = None
    source_names = []

    # Audio
    for af in by["audio"]:
        print(f"\n  [Audio] {af.name}")
        t = transcribe_audio(af, whisper_model)
        if t:
            transcript_text += "\n" + t
            source_names.append(af.name)

    # Video
    for vf in by["video"]:
        print(f"\n  [Video] {vf.name}")
        mp3 = extract_audio_from_video(vf, tmp_dir)
        if mp3:
            t = transcribe_audio(mp3, whisper_model)
            if t:
                transcript_text += "\n" + t
            source_names.append(vf.name)

    # PPTX — pipeline completa del collega
    for sf in by["slide"]:
        print(f"\n  [PPTX] {sf.name}")
        if COLLEAGUE_MODULES:
            slides_obj, plain = process_pptx_full(sf, images_dir, skip_ocr=skip_ocr)
            pptx_slides = slides_obj
            slide_text += "\n" + plain
        else:
            slide_text += "\n" + process_pptx_fallback(sf)
        source_names.append(sf.name)

    # Word
    for df in by["doc"]:
        print(f"\n  [DOCX] {df.name}")
        extra_text += "\n" + extract_docx(df)
        source_names.append(df.name)

    # PDF — chunking automatico se >20 pagine
    for pf in by["pdf"]:
        print(f"\n  [PDF] {pf.name}")
        pages = extract_pdf_pages(pf)
        if len(pages) > 20:
            chunk_files = process_pdf_chunked(
                pdf_path=pf,
                output_dir=output_dir,
                base_lesson_number=lesson_number,
                title=source_dir.name.replace("_"," ").replace("-"," ").title(),
                skip_ai=skip_ai,
                chunk_size=10,
            )
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return chunk_files
        else:
            extra_text += "\n" + "\n".join(
                f"[PAG {p['page']}]\n{p['text']}" for p in pages)
            source_names.append(pf.name)

    # Testo
    for tf in by["text"]:
        print(f"\n  [TXT] {tf.name}")
        extra_text += "\n" + tf.read_text(encoding="utf-8", errors="ignore")
        source_names.append(tf.name)

    title = source_dir.name.replace("_", " ").replace("-", " ").title()
    out_tex = output_dir / f"lezione_{lesson_number:02d}.tex"

    print()
    if skip_ai:
        print("  [--skip-ai] LaTeX strutturato dal collega (no Claude)")
        content = build_fallback_latex(
            lesson_number, title, pptx_slides,
            transcript_text, slide_text, extra_text)
    else:
        content = generate_with_claude(
            lesson_number, title, transcript_text, slide_text, extra_text,
            subject_hint        = subject_hint,
            course_context_path = course_context_path,
        )
        if not content:
            print("  [fallback] Claude non raggiunto, uso struttura dal collega")
            content = build_fallback_latex(
                lesson_number, title, pptx_slides,
                transcript_text, slide_text, extra_text)

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
            help="Tipo di materia: ingegneria, matematica, fisica, medicina, "
                "economia, giurisprudenza (default: auto-detect)")
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
