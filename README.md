# Appunti AI

Sistema per trasformare fonti miste di una lezione universitaria (audio, video, slide, PDF, documenti) in un libro LaTeX strutturato e compilabile.

---

## Architettura

```
fonti grezze
(audio / video / pptx / pdf / docx / txt)
        │
        ▼
  pipeline.py          ← orchestratore principale
        │
        ├── ffmpeg              → estrae audio da video
        ├── Whisper             → trascrive audio in testo con timestamp
        ├── extractor.py        → parsing .pptx (testo, immagini, formule OMML)
        ├── omml2latex.py       → converte formule PowerPoint in LaTeX
        ├── formula_detector.py → riconosce immagini-formula
        ├── ocr_math.py         → pix2tex: OCR formula immagine → LaTeX
        ├── pdfplumber          → estrae testo da PDF (con chunking auto)
        │
        ├── preprocessor.py     → normalizza, pulisce, comprime prima di Claude
        │       ├── NormalizedDocument
        │       │       ├── RAW_CLEAN  (<80k token)
        │       │       ├── DENSE      (80k–180k token)
        │       │       └── OUTLINE    (>180k token)
        │       ├── detect_subject()             → rileva tipo materia
        │       ├── align_transcript_to_slides() → allineamento temporale
        │       ├── update_course_context()      → aggiorna memoria corso
        │       └── corso_context.json           → concetti, definizioni, simboli
        │
        ├── Claude API          → genera LaTeX semantico (opzionale)
        │
        └── builder.py          → assembla i file .tex finali
                │
                ▼
           output/
           ├── main.tex
           ├── lezione_01.tex
           ├── lezione_02.tex
           └── images/
```

---

## File del progetto

| File | Ruolo |
|------|-------|
| `pipeline.py` | Orchestratore principale — coordina tutti i moduli |
| `preprocessor.py` | Normalizza e comprime il testo; rileva la materia; allinea trascrizione e slide; gestisce il contesto corso |
| `extractor.py` | Parsing approfondito dei file `.pptx` |
| `omml2latex.py` | Conversione formule OMML (PowerPoint) → LaTeX |
| `formula_detector.py` | Riconosce immagini che contengono formule matematiche |
| `ocr_math.py` | OCR su immagini-formula tramite pix2tex |
| `builder.py` | Costruisce il file `.tex` finale dalla struttura estratta |
| `api.py` | Backend FastAPI — espone la pipeline via HTTP |
| `index.html` | Frontend web — drag & drop, opzioni, polling stato, download |
| `TeamsHack.py` | Scarica video da Microsoft Teams tramite ffmpeg |

---

## Installazione

### Dipendenze di sistema

```bash
sudo apt install ffmpeg texlive-full
```

### Ambiente Python

```bash
python3 -m venv ~/appunti_ai/venv
source ~/appunti_ai/venv/bin/activate
pip install openai-whisper pdfplumber python-pptx python-docx \
            Pillow numpy lxml fastapi uvicorn python-multipart aiofiles
```

### pix2tex (OCR formule — ambiente separato)

pix2tex richiede dipendenze specifiche che possono confliggere. Va in un venv dedicato:

```bash
python3 -m venv ~/Scrivania/venv
source ~/Scrivania/venv/bin/activate
pip install pix2tex
```

`ocr_math.py` lo chiama automaticamente via subprocess — non serve fare nulla di speciale.

---

## Uso da riga di comando

### Singola lezione

```bash
# Con tutto (Claude + Whisper + OCR)
export ANTHROPIC_API_KEY='sk-ant-...'
python pipeline.py ./lezione_01/ --title "Digital Control"

# Con tipo materia esplicito (altrimenti auto-detect)
python pipeline.py ./lezione_01/ --title "Analisi 1" --subject matematica

# Offline rapido (no Claude, no OCR)
python pipeline.py ./lezione_01/ --skip-ai --skip-ocr

# Solo struttura, senza OCR formule
python pipeline.py ./lezione_01/ --skip-ocr --title "Digital Control"
```

### Batch — intero corso

```bash
# Struttura attesa:
# corso/
# ├── lezione_01/   (file della lezione 1)
# ├── lezione_02/   (file della lezione 2)
# └── ...

python pipeline.py --batch ./corso/ --title "Analisi Matematica 1"
python pipeline.py --batch ./corso/ --title "Digital Control" --subject ingegneria
python pipeline.py --batch ./corso/ --skip-ai --skip-ocr
```

### Opzioni disponibili

| Flag | Default | Descrizione |
|------|---------|-------------|
| `--title` | `"Appunti del Corso"` | Titolo per `main.tex` |
| `--output` | `./output` | Cartella di output |
| `--subject` | auto-detect | Tipo materia: `ingegneria` `matematica` `fisica` `medicina` `economia` `giurisprudenza` |
| `--no-context` | off | Non usare/aggiornare `corso_context.json` |
| `--skip-ai` | off | Non chiamare Claude (usa struttura automatica) |
| `--skip-ocr` | off | Non usare pix2tex (più veloce) |
| `--whisper-model` | `base` | Modello Whisper: tiny/base/small/medium/large |
| `--batch` | off | Ogni sottocartella = una lezione |
| `--start-from` | `1` | Numero iniziale per le lezioni |

### Compilazione PDF

```bash
cd output/
pdflatex main.tex && pdflatex main.tex
```

La doppia esecuzione è necessaria per generare correttamente il sommario.

---

## Uso via browser (API + Frontend)

### Avvia il backend

```bash
source ~/appunti_ai/venv/bin/activate
cd ~/appunti_ai
uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

### Apri il frontend

Apri `index.html` nel browser. Il frontend permette di:

- Trascinare file audio, video, slide, documenti
- Impostare titolo, modalità skip-ai/skip-ocr, modello Whisper
- Avviare la pipeline e monitorare lo stato in tempo reale
- Scaricare lo `.zip` con il risultato quando pronto

### Endpoint API

| Endpoint | Metodo | Descrizione |
|----------|--------|-------------|
| `/run-pipeline` | POST | Avvia pipeline, ritorna `job_id` immediatamente |
| `/job/{job_id}` | GET | Stato del job: `queued / running / done / error` |
| `/download/{job_id}` | GET | Scarica lo `.zip` con i file `.tex` + `images/` |
| `/jobs` | GET | Lista tutti i job (debug) |
| `/docs` | GET | Documentazione interattiva FastAPI |

---

## Fonti supportate

| Tipo | Estensioni | Come viene processato |
|------|-----------|----------------------|
| Audio | `.mp3` `.wav` `.m4a` `.ogg` `.flac` | Whisper → testo con timestamp |
| Video | `.mp4` `.mkv` `.avi` `.mov` `.webm` | ffmpeg → mp3 → Whisper |
| Slide | `.pptx` | extractor → testo + OMML + immagini |
| Word | `.docx` | python-docx → testo plain |
| PDF | `.pdf` | pdfplumber → testo per pagina |
| Testo | `.txt` `.md` | lettura diretta |

I PDF con più di 20 pagine vengono divisi automaticamente in chunk da 10 pagine. Ogni chunk genera un `lezione_NN.tex` separato.

---

## Preprocessor

Prima di inviare il contenuto a Claude, `preprocessor.py` esegue automaticamente quattro fasi.

**1. Pulizia (zero token)**

- Rimozione header universitari ripetuti (nome dipartimento, anno accademico)
- Rimozione numeri di pagina e timestamp Whisper `[MM:SS]`
- Ricostruzione frasi spezzate da PDF
- Rimozione frasi riempitive ("come già detto", "in altre parole", ecc.)
- Deduplicazione paragrafi identici tramite hash MD5

**2. Rilevamento tipo materia**

Il preprocessor analizza le keyword presenti nelle slide e nella trascrizione e assegna automaticamente un profilo tra: `ingegneria`, `matematica`, `fisica`, `medicina`, `economia`, `giurisprudenza`, `generico`. Ogni profilo include istruzioni LaTeX specifiche che vengono iniettate nel prompt a Claude — per esempio per matematica richiede `\begin{proof}` dopo ogni teorema, per medicina impone dosaggi in tabella, per ingegneria la notazione vettoriale `$\mathbf{x}$`. Il profilo può essere forzato da CLI con `--subject`.

**3. Allineamento temporale trascrizione ↔ slide**

Se la trascrizione Whisper ha i timestamp `[MM:SS]` e le slide hanno i marker `--- SLIDE N ---`, il preprocessor stima il range temporale di ogni slide e associa i segmenti audio corrispondenti. Il prompt che arriva a Claude non è più due blocchi separati (tutto il testo delle slide + tutta la trascrizione), ma slide per slide: `[CONTENUTO SLIDE]` seguito dalla `[SPIEGAZIONE ORALE]` del prof su quella slide specifica. Senza timestamp Whisper, la distribuzione avviene in modo uniforme.

**4. Contesto corso — `corso_context.json`**

Dopo ogni lezione generata, il preprocessor estrae automaticamente dal LaTeX prodotto i concetti chiave (titoli di section/subsection), le definizioni (`\begin{definition}`), e i simboli introdotti. Questi vengono salvati in `output/corso_context.json`. Dalla lezione successiva in poi, il prompt include una sezione `## CONTESTO DEL CORSO` con la lista delle lezioni precedenti e l'istruzione esplicita "Concetti già introdotti — NON ri-spiegare da zero". Si disabilita con `--no-context`.

**Compressione automatica** in base ai token stimati:

| Token stimati | Modalità | Comportamento |
|--------------|----------|--------------|
| < 80.000 | `RAW_CLEAN` | Testo pulito, struttura completa |
| 80.000 – 180.000 | `DENSE` | Rimozione esempi ridondanti e frasi riempitive |
| > 180.000 | `OUTLINE` | Solo struttura gerarchica + prime righe per sezione |

La stima token tiene conto della verbosità dell'italiano (~3.5 caratteri per token) e dei comandi LaTeX.

---

## Struttura output

```
output/
├── main.tex              # documento principale, include tutti i capitoli
├── lezione_01.tex        # capitolo 1
├── lezione_02.tex        # capitolo 2
├── ...
├── corso_context.json    # memoria del corso (concetti, definizioni, simboli)
└── images/
    ├── slide001_abc123.png
    ├── slide002_def456.png
    └── ...
```

`main.tex` include un preambolo LaTeX completo con:
- `amsmath`, `amssymb`, `amsthm` — matematica
- `graphicx`, `float` — immagini
- `hyperref` — link navigabili nel PDF
- `fancyhdr` — intestazioni pagina
- Ambienti: `theorem`, `definition`, `example`, `lemma`, `corollary`, `remark`
- `listings` — blocchi codice

---

## Note pratiche

**Velocità Whisper su CPU:**
Il modello `base` su CPU impiega circa 1 minuto ogni 10 minuti di audio. Per test usa `--whisper-model tiny` (4x più veloce). La trascrizione viene salvata in cache `.transcript.txt` — esecuzioni successive saltano Whisper se il file esiste già.

**`--skip-ai` non salta Whisper:**
`--skip-ai` disattiva solo Claude. Whisper gira sempre perché è trascrizione locale, non AI esterna. Il contesto corso (`corso_context.json`) non viene aggiornato se Claude non viene chiamato.

**`corso_context.json`:**
Viene creato automaticamente nella cartella di output dalla prima lezione processata. Se riprocessi un corso già parzialmente convertito, il contesto delle lezioni precedenti viene già letto e usato automaticamente. Per azzerarlo basta cancellare il file o usare `--no-context`.

**PDF grandi:**
Un PDF da 275 pagine genera automaticamente 27-28 file `lezione_NN.tex` (chunk da 10 pagine), tutti inclusi in `main.tex`.

**Moduli collega opzionali:**
Se `extractor.py`, `builder.py` e gli altri moduli non sono presenti, la pipeline usa un fallback base che funziona comunque. La qualità dell'output `.pptx` è però significativamente migliore con i moduli completi.

**API key Claude:**
```bash
export ANTHROPIC_API_KEY='sk-ant-...'
# Per renderlo permanente:
echo "export ANTHROPIC_API_KEY='sk-ant-...'" >> ~/.bashrc
```