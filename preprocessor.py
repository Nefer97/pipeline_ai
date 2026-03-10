"""
preprocessor.py — Universal LLM Preprocessor  v2
=================================================
Normalizza, pulisce e comprime il contenuto grezzo estratto da pipeline.py
prima di inviarlo a Claude.

Novità v2:
  - Rilevamento automatico tipo materia (ingegneria/matematica/fisica/
    medicina/economia/giurisprudenza/generico)
  - Allineamento temporale trascrizione ↔ slide (usa timestamp Whisper)
  - Integrazione corso_context.json (concetti noti, definizioni, simboli)

Filosofia:
  Spostare più lavoro possibile FUORI dall'LLM:
  - pulizia         → zero token
  - struttura       → zero token
  - compressione    → zero token
  - contesto corso  → informazione mirata, non ridondante
  Solo la sintesi semantica va all'LLM.

Uso rapido:
    from preprocessor import preprocess, update_course_context

    # Genera prompt per Claude
    doc = preprocess(
        transcript="...",
        slide_text="...",
        title="Digital Control — Lez 03",
        subject_hint="ingegneria",
        course_context_path="./output/corso_context.json",
        lesson_number=3,
    )
    prompt = doc.to_prompt()

    # Dopo aver ottenuto il LaTeX da Claude, aggiorna il contesto
    update_course_context(
        context_path="./output/corso_context.json",
        lesson_number=3,
        lesson_title="Sistemi a Tempo Discreto",
        latex_content=generated_latex,
    )
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────
# STIMA TOKEN  (più accurata per italiano + LaTeX)
# ─────────────────────────────────────────────────────────
def estimate_tokens(text: str) -> int:
    """
    Stima migliorata rispetto a len//4:
    - Italiano: ~1 token ogni 3.5 caratteri
    - Comandi LaTeX (\\begin, \\frac): ~1-2 token ciascuno
    """
    latex_cmds   = len(re.findall(r'\\[a-zA-Z]+', text))
    plain        = re.sub(r'\\[a-zA-Z]+', ' ', text)
    plain_tokens = max(1, len(plain) // 3)
    return plain_tokens + latex_cmds


# ─────────────────────────────────────────────────────────
# RILEVAMENTO TIPO MATERIA
# ─────────────────────────────────────────────────────────
SUBJECT_PROFILES: dict[str, dict] = {

    "ingegneria": {
        "keywords": [
            "sistema", "controllo", "circuito", "segnale", "frequenza",
            "algoritmo", "complessità", "rete", "protocollo",
            "differenziale", "trasformata", "laplace", "fourier", "bode",
            "stabilità", "retroazione", "attuatore", "sensore", "pid",
            "programmazione", "compilatore", "cpu", "memoria", "kernel",
            "campionamento", "discreto", "matrici",
        ],
        "prompt_instructions": """\
TIPO DI CORSO: Ingegneria / Scienze Tecniche
- Usa \\begin{definition} per definizioni formali di sistemi, segnali, proprietà
- Usa \\begin{theorem} + \\begin{proof} per dimostrazioni
- Usa \\begin{algorithm} per algoritmi e pseudocodice
- Numera le equazioni con \\begin{equation}\\label{eq:nome}
- Notazione vettoriale: $\\mathbf{x}$, $\\dot{x}$, $\\hat{x}$
- Indica sempre le unità di misura nelle equazioni
- Sezione "Applicazioni pratiche" se il prof ha dato esempi concreti""",
    },

    "matematica": {
        "keywords": [
            "teorema", "dimostrazione", "corollario", "lemma", "proposizione",
            "insieme", "funzione", "limite", "derivata", "integrale",
            "spazio vettoriale", "gruppo", "anello", "campo", "topologia",
            "convergenza", "continuità", "misura", "probabilità",
        ],
        "prompt_instructions": """\
TIPO DI CORSO: Matematica Pura / Analisi
- Ogni definizione → \\begin{definition}[Nome]...\\end{definition}
- Ogni enunciato → \\begin{theorem}[Nome] con \\begin{proof} subito dopo
- Corollari e lemmi → \\begin{corollary} / \\begin{lemma}
- Notazione rigorosa: \\forall, \\exists, \\Rightarrow, \\iff, \\in, \\subset
- Esempi numerici → \\begin{example}
- Controesampi → \\begin{remark} con etichetta "Controesempio"
- NON semplificare le dimostrazioni — mantieni tutti i passaggi""",
    },

    "fisica": {
        "keywords": [
            "forza", "energia", "massa", "accelerazione", "campo",
            "potenziale", "onda", "fotone", "elettrone", "quantistica",
            "termodinamica", "entropia", "hamiltoniano", "lagrangiana",
            "relatività", "ottica", "magnetismo", "vettore",
        ],
        "prompt_instructions": """\
TIPO DI CORSO: Fisica
- Leggi fisiche → \\begin{equation} con nome della legge come \\label
- Simboli standard: $\\vec{F}$, $\\hbar$, $\\nabla$, $\\partial$
- Unità di misura nel SI — indica sempre le unità
- Includi ordini di grandezza quando menzionati dal professore
- Esperimenti → \\begin{example} con setup, procedura, risultato
- Grafici e diagrammi → \\begin{figure} con descrizione dettagliata
- Distingui grandezze scalari e vettoriali""",
    },

    "medicina": {
        "keywords": [
            "paziente", "diagnosi", "terapia", "sintomo", "patologia",
            "farmaco", "dose", "anatomia", "fisiologia", "cellula",
            "recettore", "enzima", "metabolismo", "sindrome", "prognosi",
            "chirurgia", "esame", "laboratorio", "biopsia",
        ],
        "prompt_instructions": """\
TIPO DI CORSO: Medicina / Scienze Biomediche
- Definizioni cliniche → \\begin{definition}
- Criteri diagnostici → \\begin{enumerate} con lista numerata
- Farmaci: nome generico in \\textit{corsivo}, dosaggi in tabella \\begin{tabular}
- Diagnosi differenziale → \\begin{tabular} comparativa
- Classificazioni (TNM, staging) → \\begin{enumerate}
- Termini anatomici latini → \\textit{...}
- NON omettere dosaggi, vie di somministrazione, controindicazioni""",
    },

    "economia": {
        "keywords": [
            "mercato", "prezzo", "domanda", "offerta", "equilibrio",
            "utilità", "profitto", "costo marginale", "elasticità",
            "pil", "inflazione", "tasso", "banca", "investimento",
            "bilancio", "contabilità", "asset", "portfolio",
        ],
        "prompt_instructions": """\
TIPO DI CORSO: Economia / Finanza
- Modelli economici → \\begin{equation} con variabili definite sotto
- Tabelle dati → \\begin{tabular} con intestazioni chiare
- Grafici (domanda/offerta, IS-LM) → \\begin{figure} con descrizione
- Definizioni operative → \\begin{definition}
- Notazione standard: $Q_d$, $P^*$, $\\pi$ per profitto
- Esempi numerici → \\begin{example}
- Distingui modello teorico da applicazione empirica""",
    },

    "giurisprudenza": {
        "keywords": [
            "articolo", "comma", "norma", "legge", "decreto",
            "sentenza", "dottrina", "fattispecie", "contratto",
            "obbligazione", "responsabilità", "reato", "pena",
            "codice", "costituzione",
        ],
        "prompt_instructions": """\
TIPO DI CORSO: Giurisprudenza / Diritto
- Norme citate → \\begin{quote} con riferimento preciso (art., comma, legge)
- Definizioni giuridiche → \\begin{definition}
- Elementi costitutivi di fattispecie → \\begin{enumerate}
- Sentenze → \\begin{quote} con estremi (Corte, data, numero)
- Distingui orientamento dottrinale da giurisprudenziale
- Termini latini → \\textit{} (in dubio pro reo, ecc.)
- Sezione "Casistica" per esempi pratici""",
    },

    "generico": {
        "keywords": [],
        "prompt_instructions": """\
TIPO DI CORSO: Corso Universitario
- Definizioni importanti → \\begin{definition}
- Concetti chiave → \\begin{itemize} o \\begin{enumerate}
- Formule → \\begin{equation}
- Esempi → \\begin{example}
- Struttura: \\section > \\subsection > \\subsubsection""",
    },
}


def detect_subject(text: str, hint: Optional[str] = None) -> str:
    """
    Rileva il tipo di materia dal testo.
    Se hint corrisponde a una chiave nota, viene usato direttamente.
    """
    if hint:
        h = hint.lower()
        for key in SUBJECT_PROFILES:
            if key in h or h in key:
                return key

    text_lower = text.lower()
    scores: dict[str, int] = {}
    for subj, profile in SUBJECT_PROFILES.items():
        if subj == "generico":
            continue
        score = sum(1 for kw in profile["keywords"] if kw in text_lower)
        if score > 0:
            scores[subj] = score

    if not scores:
        return "generico"
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] >= 2 else "generico"


def get_subject_prompt(subject: str) -> str:
    """Ritorna le istruzioni LaTeX per il tipo di materia."""
    return SUBJECT_PROFILES.get(subject, SUBJECT_PROFILES["generico"])["prompt_instructions"]


# ─────────────────────────────────────────────────────────
# ALLINEAMENTO TEMPORALE TRASCRIZIONE ↔ SLIDE
# ─────────────────────────────────────────────────────────

def _parse_transcript_segments(transcript: str) -> list[dict]:
    """
    Estrae segmenti dalla trascrizione Whisper con timestamp.
    Ritorna: [{time_sec: int, text: str}, ...]
    """
    segments = []
    pattern  = re.compile(r'^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.+)$', re.M)
    for m in pattern.finditer(transcript):
        parts = m.group(1).split(":")
        if len(parts) == 2:
            secs = int(parts[0]) * 60 + int(parts[1])
        elif len(parts) >= 3:
            secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        else:
            continue
        segments.append({"time_sec": secs, "text": m.group(2).strip()})
    return segments


def _parse_slide_blocks(slide_text: str) -> list[dict]:
    """
    Estrae blocchi slide dalla stringa formattata da extractor/pptx.
    Ritorna: [{slide_num: int, title: str, body: str}, ...]
    """
    slides  = []
    pattern = re.compile(
        r'^---\s*(?:SLIDE|slide)\s*(\d+)(?:\s*:\s*(.+?))?\s*---\s*$', re.M
    )
    markers = list(pattern.finditer(slide_text))
    for i, m in enumerate(markers):
        start = m.end()
        end   = markers[i+1].start() if i+1 < len(markers) else len(slide_text)
        slides.append({
            "slide_num": int(m.group(1)),
            "title":     (m.group(2) or "").strip(),
            "body":      slide_text[start:end].strip(),
        })
    return slides


def align_transcript_to_slides(
    transcript:         str,
    slide_text:         str,
    total_duration_sec: Optional[int] = None,
) -> list[dict]:
    """
    Allinea i segmenti della trascrizione alle slide corrispondenti.

    Strategia con timestamp:
      - Stima la durata per slide = durata_totale / n_slide
      - Assegna ogni segmento alla slide il cui range temporale lo contiene

    Strategia senza timestamp:
      - Distribuzione uniforme delle righe

    Ritorna lista di:
      {slide_num, title, body, transcript_segments: [{time_sec, text}]}
    """
    segments = _parse_transcript_segments(transcript)
    slides   = _parse_slide_blocks(slide_text)

    if not slides:
        return [{"slide_num": 0, "title": "Trascrizione",
                 "body": "", "transcript_segments": segments}]

    if not segments:
        # Nessun timestamp — distribuzione uniforme delle righe
        lines      = [l.strip() for l in transcript.split('\n') if l.strip()]
        chunk_size = max(1, len(lines) // len(slides))
        aligned    = []
        for i, slide in enumerate(slides):
            start = i * chunk_size
            end   = start + chunk_size if i < len(slides)-1 else len(lines)
            aligned.append({
                **slide,
                "transcript_segments": [
                    {"time_sec": -1, "text": l} for l in lines[start:end]
                ],
            })
        return aligned

    # Con timestamp: assegna ogni segmento alla slide corretta
    # Strategia: calcola "tempo di discorso" escludendo le pause lunghe (> 45s).
    # Senza questo, una pausa di 10 minuti al cambio slide scalza tutti i segmenti
    # successivi verso slide più avanzate di quanto non siano in realtà.
    _PAUSE_CAP_SEC = 45   # gap tra segmenti consecutivi viene cappato a questo valore

    n_slides = len(slides)

    # Calcola speech_time cumulativo per ogni segmento
    speech_times: list[float] = [0.0] * len(segments)
    cumulative = 0.0
    for i in range(1, len(segments)):
        gap = segments[i]["time_sec"] - segments[i - 1]["time_sec"]
        cumulative += min(gap, _PAUSE_CAP_SEC)
        speech_times[i] = cumulative

    total_speech = max(1.0, speech_times[-1])

    def slide_idx(seg_i: int) -> int:
        idx = int(speech_times[seg_i] / total_speech * n_slides)
        return max(0, min(n_slides - 1, idx))

    aligned = [{**s, "transcript_segments": []} for s in slides]
    for i, seg in enumerate(segments):
        aligned[slide_idx(i)]["transcript_segments"].append(seg)

    return aligned


def aligned_to_prompt(aligned: list[dict]) -> str:
    """
    Serializza l'allineamento slide ↔ trascrizione in formato prompt.
    Ogni blocco: contenuto slide + spiegazione orale del prof su quella slide.
    """
    parts = []
    for item in aligned:
        num   = item["slide_num"]
        title = item.get("title", "")
        body  = item.get("body", "").strip()
        segs  = item.get("transcript_segments", [])

        header = f"### Slide {num}" + (f": {title}" if title else "")
        parts.append(header)

        if body:
            parts.append(f"[CONTENUTO SLIDE]\n{body}")

        oral = [s["text"] for s in segs if s.get("text", "").strip()]
        if oral:
            parts.append("[SPIEGAZIONE ORALE]\n" + "\n".join(oral))

        parts.append("")

    return "\n".join(parts)


# ─────────────────────────────────────────────────────────
# CORSO CONTEXT  — memoria persistente tra lezioni
# ─────────────────────────────────────────────────────────

# Struttura del JSON:
# {
#   "course_title": "Digital Control",
#   "subject": "ingegneria",
#   "lessons": [
#     {
#       "number": 1,
#       "title": "Introduzione ai Sistemi Discreti",
#       "key_concepts": ["campionamento", "teorema di Shannon"],
#       "definitions": ["sistema LTI", "trasformata Z"],
#       "symbols": {"T": "periodo di campionamento"}
#     }
#   ],
#   "global_symbols": {"x": "vettore di stato"},
#   "prerequisites_done": ["calcolo integrale"]
# }

def load_course_context(context_path: Optional[str]) -> dict:
    """Carica corso_context.json. Ritorna {} se non esiste."""
    if not context_path:
        return {}
    p = Path(context_path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def context_to_prompt(ctx: dict, current_lesson_number: int = 0) -> str:
    """
    Serializza il contesto in un blocco prompt conciso per Claude.
    Mostra solo le lezioni precedenti a quella corrente (max ultime 5).
    """
    if not ctx:
        return ""

    parts = ["## CONTESTO DEL CORSO"]

    if ctx.get("course_title"):
        parts.append(f"Corso: {ctx['course_title']}")

    prereqs = ctx.get("prerequisites_done", [])
    if prereqs:
        parts.append(f"Prerequisiti già trattati: {', '.join(prereqs)}")

    global_syms = ctx.get("global_symbols", {})
    if global_syms:
        sym_str = ", ".join(f"${k}$ = {v}" for k, v in global_syms.items())
        parts.append(f"Notazione del corso: {sym_str}")

    prev_lessons = [
        l for l in ctx.get("lessons", [])
        if l.get("number", 0) < current_lesson_number
    ]
    if prev_lessons:
        parts.append("\nLezioni precedenti:")
        for les in prev_lessons[-5:]:
            line = f"  Lez. {les['number']}: {les.get('title','—')}"
            if les.get("key_concepts"):
                line += f" → {', '.join(les['key_concepts'][:4])}"
            parts.append(line)

        # Raccordo dalla lezione immediatamente precedente
        last_les = prev_lessons[-1]
        last_topic = last_les.get("last_verbal_topic", "")
        if last_topic:
            parts.append(
                f"\n## RACCORDO CON LEZIONE PRECEDENTE"
                f"\nNella Lezione {last_les['number']} il professore si è fermato verbalmente su:"
                f"\n  \"{last_topic}\""
                f"\n"
                f"\nREGOLE DI RACCORDO:"
                f"\n• Questa lezione DEVE iniziare esattamente da dove il professore si era fermato"
                f"\n• Se la trascrizione riprende da un punto già spiegato, fai un raccordo breve (1-2 righe) senza ripetere la spiegazione completa"
                f"\n• LIMITE CRITICO: fermati dove si ferma la trascrizione audio di questa lezione — anche se le slide coprono argomenti successivi non ancora spiegati verbalmente, NON anticiparli"
                f"\n• Non inventare contenuto che non sia esplicitamente presente nella trascrizione o nelle slide corrispondenti alla spiegazione orale"
            )

        # Concetti e definizioni già noti (per evitare che Claude ri-spieghi)
        all_concepts = []
        all_defs     = []
        for les in prev_lessons:
            all_concepts.extend(les.get("key_concepts", []))
            all_defs.extend(les.get("definitions", []))

        if all_concepts:
            uniq = list(dict.fromkeys(all_concepts))
            parts.append(
                f"\nConcetti già introdotti (NON ri-spiegare da zero): "
                f"{', '.join(uniq)}"
            )
        if all_defs:
            uniq = list(dict.fromkeys(all_defs))
            parts.append(
                f"Definizioni già fornite (non ripetere): "
                f"{', '.join(uniq)}"
            )

    parts.append("")
    return "\n".join(parts)


def _extract_concepts_from_latex(latex: str) -> dict:
    """
    Estrae automaticamente concetti chiave, definizioni e simboli
    dal LaTeX generato — per aggiornare il contesto.
    """
    # Titoli section/subsection → concetti chiave
    concepts = [
        m.group(1).strip()
        for m in re.finditer(r'\\(?:sub)*section\{([^}]+)\}', latex)
        if len(m.group(1).strip()) > 3 and not m.group(1).strip().startswith("Lezione")
    ]

    # Contenuto \begin{definition}...\end{definition}
    defs = []
    for m in re.finditer(
        r'\\begin\{definition\}(?:\[([^\]]*)\])?([\s\S]*?)\\end\{definition\}', latex
    ):
        name = m.group(1) or ""
        body = re.sub(r'\s+', ' ', m.group(2)).strip()[:80]
        defs.append(name if name else body[:40])

    # Simboli: "$x$ = descrizione" o "$x$ indica descrizione"
    symbols = {}
    for m in re.finditer(
        r'\$([\\a-zA-Z{}_\^]+)\$\s+(?:è|indica|denota|rappresenta|=)\s+([^.,$\n]{5,60})',
        latex, re.I
    ):
        sym = m.group(1).strip()
        if len(sym) < 20:
            symbols[sym] = m.group(2).strip()

    return {
        "key_concepts": list(dict.fromkeys(concepts))[:10],
        "definitions":  list(dict.fromkeys(defs))[:8],
        "symbols":      symbols,
    }


def update_course_context(
    context_path:  str,
    lesson_number: int,
    lesson_title:  str,
    latex_content: str,
    course_title:  Optional[str] = None,
    subject:       Optional[str] = None,
) -> dict:
    """
    Aggiorna corso_context.json dopo aver generato il LaTeX di una lezione.

    Chiamare da pipeline.py subito dopo generate_with_claude():

        from preprocessor import update_course_context
        if content:
            update_course_context(
                context_path  = str(output_dir / "corso_context.json"),
                lesson_number = lesson_number,
                lesson_title  = title,
                latex_content = content,
                course_title  = args.title,
                subject       = detected_subject,   # opzionale
            )

    Ritorna il contesto aggiornato.
    """
    ctx = load_course_context(context_path)

    if not ctx:
        ctx = {
            "course_title":       course_title or "",
            "subject":            subject or "generico",
            "lessons":            [],
            "global_symbols":     {},
            "prerequisites_done": [],
        }
    if course_title and not ctx.get("course_title"):
        ctx["course_title"] = course_title
    if subject and ctx.get("subject") == "generico":
        ctx["subject"] = subject

    extracted = _extract_concepts_from_latex(latex_content)

    # Ultimo argomento trattato verbalmente = ultima section/subsection NUMERATA
    # Esclude \subsection* usate per note/conclusioni/raccordi interni.
    # Usato nella lezione successiva come punto di raccordo.
    _skip_titles = re.compile(
        r'^(note|osservazioni?|conclusioni?|riferimenti|bibliografia|sommario|summary|remark)s?$',
        re.I,
    )
    all_sections = [
        m.group(1).strip()
        for m in re.finditer(r'\\(?:sub)*section\*?\{([^}]+)\}', latex_content)
        if not _skip_titles.match(m.group(1).strip())
    ]
    last_verbal_topic = all_sections[-1] if all_sections else ""

    # Rimuovi entry precedente con stesso numero (riesecuzione)
    ctx["lessons"] = [l for l in ctx["lessons"] if l.get("number") != lesson_number]
    ctx["lessons"].append({
        "number":           lesson_number,
        "title":            lesson_title,
        "key_concepts":     extracted["key_concepts"],
        "definitions":      extracted["definitions"],
        "symbols":          extracted["symbols"],
        "last_verbal_topic": last_verbal_topic,
    })
    ctx["lessons"].sort(key=lambda l: l.get("number", 0))
    ctx["global_symbols"].update(extracted["symbols"])

    # ── Pruning: evita crescita illimitata su corsi lunghi ──────────────────
    # Lezioni vecchie (oltre le ultime 10): teniamo solo number/title/last_verbal_topic
    # Le ultime 10 rimangono complete per il contesto diretto.
    _FULL_LESSONS_KEEP = 10
    if len(ctx["lessons"]) > _FULL_LESSONS_KEEP:
        for les in ctx["lessons"][:-_FULL_LESSONS_KEEP]:
            les.pop("key_concepts", None)
            les.pop("definitions",  None)
            les.pop("symbols",      None)
    # global_symbols: tieni solo gli ultimi 50 simboli (dizionario preserva inserimento)
    _MAX_SYMBOLS = 50
    if len(ctx["global_symbols"]) > _MAX_SYMBOLS:
        ctx["global_symbols"] = dict(
            list(ctx["global_symbols"].items())[-_MAX_SYMBOLS:]
        )

    p = Path(context_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    [context] aggiornato: {p} (lezione {lesson_number}, "
          f"{len(extracted['key_concepts'])} concetti, "
          f"{len(extracted['definitions'])} definizioni)")
    return ctx


# ─────────────────────────────────────────────────────────
# NORMALIZED DOCUMENT
# ─────────────────────────────────────────────────────────
@dataclass
class NormalizedDocument:
    title:            str
    sections:         list[dict]
    math_blocks:      list[str]
    figures:          list[str]
    metadata:         dict
    raw_text:         str
    clean_text:       str
    estimated_tokens: int  = 0
    mode:             str  = "RAW_CLEAN"
    subject:          str  = "generico"
    subject_prompt:   str  = ""
    context_prompt:   str  = ""
    aligned_sections: list = field(default_factory=list)

    def to_prompt(self) -> str:
        parts = []

        if self.context_prompt:
            parts.append(self.context_prompt)

        if self.subject_prompt:
            parts.append(self.subject_prompt)

        if self.aligned_sections:
            parts.append("## CONTENUTO LEZIONE (slide + spiegazione orale allineate)\n")
            parts.append(aligned_to_prompt(self.aligned_sections))
        elif self.mode == "OUTLINE":
            parts.append(self._to_outline())
        elif self.mode == "DENSE":
            parts.append(self._to_dense())
        else:
            parts.append(self._to_raw_clean())

        if self.math_blocks and not self.aligned_sections:
            parts.append("\n## Formule estratte\n" + "\n".join(self.math_blocks))

        return "\n\n".join(p for p in parts if p.strip())

    def _to_raw_clean(self) -> str:
        parts = [f"# {self.title}\n"]
        for sec in self.sections:
            if sec.get("heading"):
                parts.append(f"\n## {sec['heading']}")
            if sec.get("body"):
                parts.append(sec["body"])
        return "\n".join(parts)

    def _to_dense(self) -> str:
        parts = [f"# {self.title}\n"]
        for sec in self.sections:
            body = _compress_section(sec.get("body", ""))
            if body.strip():
                if sec.get("heading"):
                    parts.append(f"\n## {sec['heading']}")
                parts.append(body)
        return "\n".join(parts)

    def _to_outline(self) -> str:
        parts = [f"# {self.title} [OUTLINE]\n"]
        for i, sec in enumerate(self.sections, 1):
            heading = sec.get("heading") or f"Sezione {i}"
            preview = " ".join(sec.get("body","").split("\n")[:2])[:120]
            parts.append(f"{i}. **{heading}** — {preview}…")
        if self.math_blocks:
            parts.append(f"\n[{len(self.math_blocks)} formule presenti]")
        return "\n".join(parts)


# ─────────────────────────────────────────────────────────
# PULIZIA
# ─────────────────────────────────────────────────────────
_PATTERNS_REMOVE = [
    re.compile(r'\bpag(?:ina|\.?)?\s*\d+\b', re.I),
    re.compile(r'^\s*\d+\s*$', re.M),
    re.compile(r'^\s*[-–—]+\s*$', re.M),
    re.compile(r'\[\d{2}:\d{2}\]\s*', re.M),
    re.compile(r'©.*?(?:\n|$)', re.I),
    re.compile(r'slide\s+\d+\s*/\s*\d+', re.I),
    re.compile(r'department\s+of\s+\w+.*?\n', re.I),
    re.compile(r'university\s+of\s+\w+.*?\n', re.I),
    re.compile(r'a\.y\.\s*\d{4}[-/]\d{2,4}.*?\n', re.I),
]

_FILLER_PATTERNS = [
    re.compile(r'\bcome\s+(?:già\s+)?(?:detto|visto|accennato)\b', re.I),
    re.compile(r'\bcome\s+(?:vedremo|abbiamo\s+visto)\b', re.I),
    re.compile(r'\bin\s+altre\s+parole\b', re.I),
    re.compile(r'\bè\s+importante\s+(?:notare|sottolineare)\b', re.I),
    re.compile(r'\bcioè\s+a\s+dire\b', re.I),
    re.compile(r"\blet(?:'s| us)\s+(?:now\s+)?(?:look at|consider|see)\b", re.I),
    re.compile(r'\bin\s+this\s+(?:slide|lecture)\s+we\b', re.I),
    re.compile(r'\bso,?\s+(?:basically|essentially|fundamentally)\b', re.I),
]


def clean_text(text: str) -> str:
    if not text:
        return ""
    for pat in _PATTERNS_REMOVE:
        text = pat.sub("", text)
    lines   = text.split("\n")
    rebuilt = []
    for line in lines:
        line = line.rstrip()
        if (rebuilt and line and line[0].islower()
                and rebuilt[-1]
                and rebuilt[-1][-1] not in ".!?:;)]\""
                and len(rebuilt[-1]) < 80):
            rebuilt[-1] += " " + line
        else:
            rebuilt.append(line)
    return re.sub(r'\n{3,}', '\n\n', "\n".join(rebuilt)).strip()


def deduplicate(paragraphs: list[str]) -> list[str]:
    seen:   set[str] = set()
    result: list[str] = []
    for p in paragraphs:
        norm = re.sub(r'\W+', '', p.lower())
        if len(norm) < 20:
            result.append(p)
            continue
        h = hashlib.md5(norm.encode()).hexdigest()
        if h not in seen:
            seen.add(h)
            result.append(p)
    return result


def _compress_section(body: str) -> str:
    return "\n".join(
        line for line in body.split("\n")
        if not any(pat.search(line) for pat in _FILLER_PATTERNS)
    )


# ─────────────────────────────────────────────────────────
# PARSING SEZIONI
# ─────────────────────────────────────────────────────────
def parse_sections(text: str) -> list[dict]:
    sections:        list[dict] = []
    current_heading: str        = ""
    current_lines:   list[str]  = []

    slide_marker = re.compile(r'^---\s*(?:SLIDE|PAG|slide|pag)\s*(\d+)\s*---\s*$', re.I)
    time_marker  = re.compile(r'^\[(\d{2}:\d{2})\](.*)$')
    caps_heading = re.compile(r'^[A-Z][A-Z\s\-:]{8,}$')

    def flush():
        body = "\n".join(current_lines).strip()
        if body or current_heading:
            sections.append({"heading": current_heading, "body": body, "type": "text"})

    for line in text.split("\n"):
        ms = slide_marker.match(line.strip())
        mt = time_marker.match(line.strip())
        if ms:
            flush()
            current_heading = f"Slide / Pagina {ms.group(1)}"
            current_lines   = []
        elif mt:
            rest = mt.group(2).strip()
            if rest:
                current_lines.append(rest)
        elif caps_heading.match(line.strip()) and len(line.strip()) > 10:
            flush()
            current_heading = line.strip().title()
            current_lines   = []
        else:
            current_lines.append(line)
    flush()
    return sections


# ─────────────────────────────────────────────────────────
# EXTRACT MATH
# ─────────────────────────────────────────────────────────
_MATH_INLINE  = re.compile(r'\$[^$\n]{2,80}\$')
_MATH_DISPLAY = re.compile(r'\$\$[\s\S]{2,300}?\$\$')
_LATEX_EQ     = re.compile(r'\\begin\{equation\}[\s\S]*?\\end\{equation\}')


def extract_math(text: str) -> tuple[str, list[str]]:
    formulas: list[str] = []

    def grab(m):
        formulas.append(m.group(0))
        return f"[FORMULA_{len(formulas)}]"

    text = _MATH_DISPLAY.sub(grab, text)
    text = _LATEX_EQ.sub(grab, text)
    formulas += _MATH_INLINE.findall(text)
    return text, formulas


# ─────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────
THRESHOLD_DENSE   = 80_000
THRESHOLD_OUTLINE = 180_000


def preprocess(
    transcript:          str = "",
    slide_text:          str = "",
    extra_text:          str = "",
    title:               str = "Lezione",
    figures:             list[str] | None = None,
    force_mode:          Optional[str] = None,
    subject_hint:        Optional[str] = None,
    course_context_path: Optional[str] = None,
    lesson_number:       int = 0,
    total_duration_sec:  Optional[int] = None,
) -> NormalizedDocument:
    """
    Punto di ingresso principale.

    Parametri:
        transcript          — testo Whisper (con o senza timestamp [MM:SS])
        slide_text          — testo estratto da .pptx / .pdf
        extra_text          — documenti aggiuntivi (.docx, .txt)
        title               — titolo della lezione
        figures             — lista path immagini già estratte
        force_mode          — forza RAW_CLEAN | DENSE | OUTLINE
        subject_hint        — suggerimento tipo materia (es. "matematica")
        course_context_path — path a corso_context.json
        lesson_number       — numero lezione corrente (per filtrare contesto)
        total_duration_sec  — durata audio in secondi (per allineamento)
    """
    figures = figures or []

    # 1. Pulizia
    clean_tr  = clean_text(transcript)
    clean_sl  = clean_text(slide_text)
    clean_ex  = clean_text(extra_text)

    # 2. Rilevamento tipo materia
    sample  = clean_sl[:3000] + " " + clean_tr[:2000]
    subject = detect_subject(sample, hint=subject_hint)

    # 3. Allineamento temporale trascrizione ↔ slide
    aligned_sections = []
    has_slides = bool(re.search(r'---\s*SLIDE\s*\d+', slide_text, re.I))
    if has_slides and transcript.strip():
        aligned_sections = align_transcript_to_slides(
            transcript         = transcript,
            slide_text         = slide_text,
            total_duration_sec = total_duration_sec,
        )

    # 4. Testo combinato (usato quando non c'è allineamento o per formule)
    combined_parts = []
    if clean_sl: combined_parts.append(f"[SLIDE]\n{clean_sl}")
    if clean_tr: combined_parts.append(f"[TRASCRIZIONE]\n{clean_tr}")
    if clean_ex: combined_parts.append(f"[DOCUMENTI]\n{clean_ex}")
    combined = "\n\n".join(combined_parts)

    # 5. Estrai formule
    combined_no_math, math_blocks = extract_math(combined)

    # 6. Deduplicazione
    paragraphs = deduplicate([p for p in combined_no_math.split("\n\n") if p.strip()])
    clean_combined = "\n\n".join(paragraphs)

    # 7. Sezioni
    sections = parse_sections(clean_combined)

    # 8. Token e modalità
    total_tokens = estimate_tokens(clean_combined)
    if force_mode:
        mode = force_mode
    elif total_tokens > THRESHOLD_OUTLINE:
        mode = "OUTLINE"
    elif total_tokens > THRESHOLD_DENSE:
        mode = "DENSE"
    else:
        mode = "RAW_CLEAN"

    # 9. Contesto corso
    ctx            = load_course_context(course_context_path)
    context_prompt = context_to_prompt(ctx, current_lesson_number=lesson_number)

    has_ts = bool(re.search(r'\[\d{2}:\d{2}\]', transcript))
    print(
        f"    [preprocessor] modalità={mode} | token≈{total_tokens:,} | "
        f"materia={subject} | sezioni={len(sections)} | "
        f"formule={len(math_blocks)} | "
        f"allineamento={'sì ('+str(len(aligned_sections))+' slide)' if aligned_sections else 'no'} | "
        f"contesto={'sì' if context_prompt else 'no'}"
    )

    return NormalizedDocument(
        title            = title,
        sections         = sections,
        math_blocks      = math_blocks,
        figures          = figures,
        metadata         = {
            "has_transcript":  bool(clean_tr),
            "has_slides":      bool(clean_sl),
            "has_extra":       bool(clean_ex),
            "has_timestamps":  has_ts,
            "sources_count":   sum([bool(clean_tr), bool(clean_sl), bool(clean_ex)]),
            "subject":         subject,
        },
        raw_text         = combined,
        clean_text       = clean_combined,
        estimated_tokens = total_tokens,
        mode             = mode,
        subject          = subject,
        subject_prompt   = get_subject_prompt(subject),
        context_prompt   = context_prompt,
        aligned_sections = aligned_sections,
    )
