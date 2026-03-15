# Appunti AI

Trasforma fonti miste di una lezione universitaria — audio, video, slide, PDF, documenti — in un libro LaTeX strutturato e compilabile, usando Whisper per trascrivere e Claude per sintetizzare.

Claude riceve il **prompt testuale completo + le immagini PNG delle slide** (multimodale), così può vedere grafici, schemi e diagrammi che il testo non descrive.

---

## Piattaforme supportate

> **Sviluppato e testato su Ubuntu 22.04 LTS.** È la piattaforma di riferimento — tutto funziona.

| Sistema | Stato | Note |
|---------|-------|------|
| **Ubuntu 22.04 / Debian** | ✅ testato | piattaforma di riferimento |
| **Linux (altre distro)** | ✅ dovrebbe funzionare | stesso stack apt/pip |
| **macOS** | ⚠ parziale | ffmpeg/pdflatex/tesseract via `brew`; LibreOffice path da verificare in `slide_renderer.py`; pix2tex su Apple Silicon richiede PyTorch ARM |
| **Windows** | ⚠ non testato | ffmpeg/pdflatex installabili; LibreOffice non in PATH di default; alcune subprocess call POSIX potrebbero richiedere adattamenti |

---

## Requisiti di sistema

| Strumento | Versione minima | Note |
|-----------|-----------------|------|
| Python | **3.10** | Obbligatorio |
| ffmpeg | qualsiasi | Obbligatorio per audio/video e Teams |
| pdflatex | qualsiasi | Per compilare il PDF finale |
| Claude API key | — | Opzionale — senza funziona con `--skip-ai` |

---

## Installazione

### 1. Dipendenze di sistema

**Linux (Ubuntu/Debian):**
```bash
# Minimale — sufficiente per quasi tutti i casi
sudo apt install ffmpeg \
    texlive-latex-base texlive-latex-recommended texlive-latex-extra \
    texlive-fonts-recommended texlive-lang-italian

# Completa — tutti i pacchetti LaTeX (~2 GB, non strettamente necessaria)
sudo apt install ffmpeg texlive-full
```

> **Altre lingue LaTeX:** per lezioni in inglese, francese, tedesco, spagnolo ecc. aggiungi il pacchetto babel corrispondente: `texlive-lang-european` copre francese/tedesco/spagnolo/olandese. La lingua viene auto-rilevata da Whisper e il documento LaTeX usa `\usepackage[lingua]{babel}` in modo dinamico.

### 2. Clona il repo e crea il venv

```bash
git clone <repo_url> ~/appunti_ai
cd ~/appunti_ai

python3 -m venv venv
source venv/bin/activate
```

### 3. Installa le dipendenze Python

```bash
pip install -r requirements.txt
```

### 4. (Opzionale) OCR per PDF scansionati

Se hai PDF senza testo selezionabile (scansioni, foto di libri):

```bash
pip install pytesseract
sudo apt install tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng
```

Senza il binario `tesseract` i PDF scansionati vengono saltati — la pipeline continua con le altre fonti. Con tesseract installato, `ocr_math.py` lo usa anche come secondo backend per OCR formule (dopo pix2tex).

### 5. (Opzionale) pix2tex — OCR formule matematiche

pix2tex converte immagini di formule in LaTeX. Ha dipendenze pesanti (PyTorch ~2 GB), quindi va installato in un **venv separato**:

```bash
python3 -m venv ~/pix2tex_venv
source ~/pix2tex_venv/bin/activate
pip install pix2tex

# Verifica (scarica i pesi ~116 MB al primo avvio)
python -c "from pix2tex.cli import LatexOCR; m = LatexOCR(); print('OK')"
```

`ocr_math.py` cerca automaticamente pix2tex nei path standard (`~/pix2tex_venv`, `~/venv`, `~/Scrivania/venv`, `~/.venv`, `/opt/pix2tex_venv`). Se non trovato, le immagini-formula vengono saltate senza errori — le formule scritte con l'editor equazioni PowerPoint (OMML) vengono comunque convertite correttamente tramite `omml2latex.py`.

### 6. API key Claude

```bash
export ANTHROPIC_API_KEY='sk-ant-...'

# Per renderla permanente:
echo "export ANTHROPIC_API_KEY='sk-ant-...'" >> ~/.bashrc   # Linux
echo "export ANTHROPIC_API_KEY='sk-ant-...'" >> ~/.zshrc    # macOS
```

In alternativa, imposta la chiave direttamente dall'interfaccia web (`/settings`).
Senza API key la pipeline funziona ugualmente con `--skip-ai`.

### 7. Verifica installazione

```bash
source venv/bin/activate
python -c "import fitz; print('pymupdf OK:', fitz.__version__)"
python -c "import whisper; print('whisper OK')"
python -c "import fastapi, uvicorn; print('server OK')"
python -c "import anthropic; print('anthropic OK')"
python pipeline.py --help

# Backend OCR formule
python -c "from ocr_math import get_available_backends; print(get_available_backends())"
# ['pix2tex (subprocess)', 'tesseract', 'heuristic']  ← tutto attivo
# ['pix2tex (subprocess)', 'heuristic']               ← tesseract mancante
# ['tesseract', 'heuristic']                          ← pix2tex mancante
# ['heuristic']                                       ← solo fallback euristico
```

---

## Quick Start — primo utilizzo in 5 minuti

### Opzione A — con browser (consigliata)

```bash
source venv/bin/activate
uvicorn server:app --host 0.0.0.0 --port 8000
```

Apri `http://localhost:8000`, trascina i file della lezione, inserisci un titolo e clicca **Start**.

### Opzione B — da riga di comando

```bash
source venv/bin/activate

# Con audio + slide: Whisper trascrive, Claude genera il LaTeX
python pipeline.py ./lezione_01/ --title "Analisi Matematica 1"

# Solo slide/PDF, senza audio, senza Claude (offline, gratuito)
python pipeline.py ./lezione_01/ --skip-ai --title "Analisi Matematica 1"

# Intero corso in batch (ogni sottocartella = una lezione)
python pipeline.py --batch ./corso/ --title "Digital Control"
```

Output in `./output/`:
```
output/
├── main.tex          ← documento principale
├── lezione_01.tex    ← capitolo 1
├── images/           ← PNG slide e pagine PDF
└── ...
```

Compilazione PDF:
```bash
cd output/
pdflatex main.tex && pdflatex main.tex   # doppia esecuzione per il sommario
```

---

## Cosa fa e come funziona

Appunti AI usa Claude **esattamente una volta per lezione** — per l'unica cosa che richiede intelligenza semantica: sintetizzare la trascrizione orale con le slide in prosa accademica LaTeX. Tutto il resto (trascrizione, OCR, rendering, compilazione) gira in locale.

```
fonti grezze (audio / video / pptx / pdf / docx / txt)
        │
        ▼
  pipeline.py                    ← orchestratore
        │
        ├── ffmpeg                → estrae audio da video
        ├── Whisper (locale)      → trascrive in testo con timestamp [MM:SS]
        │
        ├── extractor.py          → parsing PPTX (testo, immagini, tabelle, formule OMML)
        ├── omml2latex.py         → formule PowerPoint (OMML) → LaTeX (nessun OCR)
        ├── formula_detector.py   → riconosce immagini-formula nelle slide
        ├── ocr_math.py           → pix2tex / tesseract: immagine formula → LaTeX
        ├── slide_renderer.py     → ogni slide PPTX → PNG (LibreOffice+pymupdf se disponibile, altrimenti Pillow)
        │
        ├── pdfplumber + pymupdf  → estrazione duale per pagina (qualità comparata)
        ├── _clean_extracted_text → fix doubled chars, collassa spazi, rimuove padding
        ├── pytesseract           → OCR fallback per PDF scansionati
        ├── pdf_renderer.py       → ogni pagina PDF → PNG
        │
        ├── preprocessor.py       → pulisce, comprime, allinea slide↔audio per Claude
        │
        ├── Claude API (cloud)    → riceve testo + PNG slide → genera LaTeX strutturato
        │                           (multimodale: vede grafici e schemi oltre al testo)
        ├── _clean_claude_output  → rimuove code fences, prefissi e suffissi spurii
        │                           prima di scrivere il .tex su disco
        └── builder.py            → assembla i .tex finali con escape unicode completo
```

### Gerarchia delle fonti

La pipeline assegna automaticamente un ruolo a ogni file in base al tipo:

| Ruolo | File | Comportamento |
|-------|------|---------------|
| **SCHELETRO** | `.pptx` sempre; `.pdf` `.docx` se c'è audio | Struttura ufficiale della lezione |
| **CARNE** | `.mp3` `.wav` `.mp4` `.mkv` ecc. | Spiegazione orale del professore |
| **SUPPORTO** | `.pdf` `.docx` senza audio | Materiale di approfondimento |
| **CONTORNO** | `.txt` `.md` `.rtf` | Note informali, peso minore |

### Cosa riceve Claude

La chiamata API è **multimodale**: testo + immagini nella stessa richiesta.

| Blocco | Contenuto | Limite |
|--------|-----------|--------|
| System | 19 regole LaTeX accademiche (cachato da Anthropic) | — |
| Immagini | PNG slide PPTX in ordine (max 20) | solo se c'è PPTX |
| SCHELETRO | LaTeX skeleton dalle slide / testo estratto da PDF e DOCX | 160.000 char |
| CARNE | Trascrizione Whisper completa con timestamp (o allineamento slide↔audio) | 200.000 char |
| SUPPORTO | Testo da PDF/DOCX di riferimento | 80.000 char |
| CONTORNO | Note informali, testi liberi | 40.000 char |
| Istruzioni | Regole di sintesi adattive per la lezione | — |

Le immagini PNG delle **pagine PDF** vengono allegate solo se non c'è audio (la trascrizione già copre il contenuto testuale). Se ci sono più di 20 immagini, vengono prese le prime 20 per slide in ordine.

---

## Fonti supportate

| Tipo | Estensioni |
|------|-----------|
| Audio | `.mp3` `.wav` `.m4a` `.ogg` `.flac` |
| Video | `.mp4` `.mkv` `.avi` `.mov` `.webm` |
| Teams URL | URL manifest incollato nella UI (scaricato via ffmpeg) |
| Slide | `.pptx` |
| Word | `.docx` |
| PDF | `.pdf` — testo digitale + OCR fallback per scansionati |
| Testo | `.txt` `.md` `.rtf` (RTF con strip automatico dei tag) |

PDF > 20 pagine senza audio vengono suddivisi automaticamente in chunk da 10 pagine, ognuno come `lezione_NN.tex` separato.

---

## Uso da riga di comando

### Singola lezione

```bash
# Standard: Whisper + Claude (con immagini slide)
python pipeline.py ./lezione_01/ --title "Reti di Calcolatori"

# Con materia esplicita (altrimenti auto-detect)
python pipeline.py ./lezione_01/ --title "Analisi 1" --subject matematica

# Offline: nessuna API, nessun OCR formule — produce comunque LaTeX con immagini
python pipeline.py ./lezione_01/ --skip-ai --skip-ocr --title "Fisica 1"
```

### Batch — intero corso

```bash
# Struttura attesa:
# corso/
# ├── lezione_01/   (file audio, slide, PDF...)
# ├── lezione_02/
# └── ...

python pipeline.py --batch ./corso/ --title "Analisi Matematica 1"
python pipeline.py --batch ./corso/ --skip-ai --skip-ocr   # offline
python pipeline.py --batch ./corso/ --continue-on-error    # salta lezioni che falliscono
```

### Opzioni

| Flag | Default | Descrizione |
|------|---------|-------------|
| `--title` | `"Appunti del Corso"` | Titolo per `main.tex` e `\chapter{}` |
| `--output` | `./output` | Cartella di output |
| `--subject` | auto | `ingegneria` `matematica` `fisica` `medicina` `economia` `giurisprudenza` `generico` |
| `--skip-ai` | off | Non chiamare Claude — usa struttura automatica con immagini |
| `--skip-ocr` | off | Non usare pix2tex (più veloce) |
| `--no-context` | off | Non usare/aggiornare `corso_context.json` |
| `--whisper-model` | `base` | `tiny` `base` `small` `medium` `large` |
| `--batch` | off | Ogni sottocartella = una lezione |
| `--start-from` | auto | Numero iniziale lezione (default: auto da `state.json`) |
| `--continue-on-error` | off | In batch: salta lezioni che falliscono invece di bloccare |

---

## Uso via browser

```bash
source venv/bin/activate
cd ~/appunti_ai
uvicorn server:app --host 0.0.0.0 --port 8000
```

Apri `http://localhost:8000`. Il frontend permette di:

- Trascinare file audio, video, slide, documenti
- Incollare URL manifest di Microsoft Teams (scaricati automaticamente via ffmpeg)
- Scegliere la materia tra 7 profili disciplinari (o auto-detect)
- Impostare titolo, Claude on/off, OCR, modello Whisper, contesto corso
- Monitorare lo stato in tempo reale con percentuale di avanzamento
- Scaricare lo `.zip` con il risultato e/o il PDF compilato
- Vedere la history dei job con accesso diretto a ZIP, PDF e anteprima
- **Editor LaTeX integrato** — si apre cliccando "👁 .tex" nella history; pannello split: editor a sinistra, PDF viewer a destra
  - Larghezza dei due pannelli regolabile trascinando il divisore centrale
  - CodeMirror 5 con syntax highlighting LaTeX completo, numeri di riga, indentazione Tab/Shift-Tab
  - Tema editor segue il tema dell'interfaccia (darcula in dark, default in light)
  - Tab per selezionare e modificare qualsiasi file `.tex` del job
  - Salvataggio separato da ricompilazione (💾 Salva / ⚙ Ricompila)
  - Auto-save in `localStorage` ogni 2s — bozza ripristinata se si riapre il job prima di salvare
  - Pannello immagini sotto l'editor con tutte le PNG della cartella `images/`
- **Pulsante copia log** — nel header del pannello "log pipeline", copia l'intero output in un click
- **Visualizzatore prompt Claude** — bottone "📄 Prompt" nella history: mostra il prompt SYSTEM+USER completo inviato a Claude; se la lezione ha più capitoli, tab per navigare tra essi
- **Validazione file upload** — ogni file nella lista mostra un badge: ✓ verde (ok), ⚠ giallo (file molto grande), ✗ rosso (formato non supportato per quel tipo di slot)
- **Dialog di conferma custom** — elimina job e annulla pipeline usano un modal stilizzato invece del `confirm()` del browser
- Controllare lo stato dei tool di sistema (API key, ffmpeg, pdflatex) in tempo reale
- Impostare l'API key Claude direttamente dall'interfaccia (persiste su `settings.json`)
- Alternare tema chiaro/scuro

Le pagine `schema.htm` (diagramma interattivo architettura) e `docs.htm` (documentazione completa) sono accessibili dalla navbar in alto.

### Continuità tra lezioni

Il campo **Continua da job** accetta il `job_id` di una sessione precedente. Il server copia automaticamente `state.json`, `corso_context.json`, `lezione_*.tex`, `run_*.tex` e la cartella `images/` — la numerazione, i file già generati e la memoria del corso proseguono esattamente da dove ci si era fermati.

Claude riceve anche il contenuto dell'**ultimo capitolo generato** (fino a 6000 caratteri) come contesto nel system prompt: sa cosa è stato spiegato nella sessione precedente e può riallacciarsi senza ripetizioni.

Il log di pipeline appare in tempo reale via Server-Sent Events (`/job/{id}/stream`) anche per le sessioni "continua", non solo per quelle nuove.

### Accesso remoto

Il server ascolta su `0.0.0.0` — raggiungibile da qualsiasi dispositivo nella rete locale con l'IP del server (es. `http://192.168.1.x:8000`).

Per accesso da reti esterne usa **Tailscale**:
```bash
# Sul server Linux
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
# Su macOS: scarica da https://tailscale.com/download
```
Dopo il login con lo stesso account, il server è raggiungibile via Tailscale IP (`tailscale ip -4`).

### Endpoint API

| Endpoint | Metodo | Descrizione |
|----------|--------|-------------|
| `/` | GET | Frontend (`index.htm`) |
| `/schema.htm` | GET | Diagramma architettura interattivo |
| `/docs.htm` | GET | Documentazione interattiva |
| `/run-pipeline` | POST | Avvia pipeline, ritorna `job_id` |
| `/job/{job_id}` | GET | Stato job + progress, step, detail, pdf_errors |
| `/job/{job_id}` | DELETE | Elimina job (`?full=true` rimuove anche ZIP e output) |
| `/job/{job_id}/stream` | GET | Server-Sent Events: log pipeline in tempo reale |
| `/jobs` | GET | Lista tutti i job |
| `/download/{job_id}` | GET | Scarica `.zip` output |
| `/pdf/{job_id}` | GET | Visualizza PDF compilato inline nel browser |
| `/tex/{job_id}/{filename}` | GET | Contenuto di un file `.tex` del job |
| `/save/{job_id}` | POST | Salva un file `.tex` su disco (senza ricompilare) |
| `/recompile/{job_id}` | POST | Salva + ricompila con pdflatex, ritorna errori |
| `/preview/{job_id}` | GET | Contenuto `main.tex` + lista file `.tex` + stato PDF |
| `/prompt/{job_id}` | GET | Prompt SYSTEM+USER inviati a Claude (file `debug/prompt_lezione_NN.txt`) |
| `/images/{job_id}` | GET | Lista PNG nella cartella `images/` del job |
| `/image/{job_id}/{filename}` | GET | Serve una singola immagine PNG |
| `/health` | GET | Stato tool di sistema: `api_key`, `ffmpeg`, `pdflatex`, `whisper` |
| `/settings` | GET | Configurazione corrente |
| `/settings` | POST | Salva `api_key`, `ttl_days` (1–365), `ffmpeg_timeout`, `pipeline_timeout`, `max_concurrent_jobs` (1–10) |
| `/docs` | GET | Documentazione interattiva FastAPI |

---

## Struttura output

```
output/
├── main.tex                   # documento principale — include run_01, run_02, ...
├── run_01.tex                 # \part{Titolo — data} + \input{lezione_01} ...
├── run_02.tex                 # \part{Titolo — data} + \input{lezione_03} ...
├── lezione_01.tex             # capitolo 1
├── lezione_02.tex             # capitolo 2
├── corso_context.json         # memoria corso (concetti, simboli, ultimo argomento)
├── state.json                 # stato pipeline: next_lesson, lessons, next_run, runs
├── images/
│   ├── nome_slide_001.png     # screenshot slide 1 ({stem}_slide_NNN.png)
│   ├── dispense_pag_001.png   # screenshot pagina 1 PDF ({stem}_pag_NNN.png)
│   ├── slide001_abc123.png    # immagine embedded estratta dal PPTX
│   └── formula_def456.png     # immagine formula → pix2tex
└── debug/
    ├── prompt_lezione_01.txt      # SYSTEM + USER prompt inviati a Claude
    ├── images_lezione_01/         # symlink alle PNG allegate (ordinate 01_, 02_, ...)
    └── riepilogo_lezione_01.txt   # classificazione sorgenti con char count
```

`main.tex` include un preambolo LaTeX completo con `amsmath`, `amssymb`, `amsthm`, `graphicx`, `hyperref`, `fancyhdr`, `babel` (lingua dinamica), ambienti `theorem`, `definition`, `example`, `lemma`, `corollary`, `remark`, e `listings` per blocchi codice.

### Aggregazione multi-run

Ogni invocazione della pipeline (ogni "sessione" dal browser o da CLI) genera un **run** numerato. Il `main.tex` compilato ha struttura:

```
main.tex
  └── \part{Lezione 1 — 2025-06-10}    ← run_01.tex
        \input{lezione_01}
        \input{lezione_02}
  └── \part{Lezione 3 — 2025-06-17}    ← run_02.tex
        \input{lezione_03}
```

`state.json` persiste `next_run` e la lista `runs` oltre a `next_lesson` e `lessons`. Quando si usa "Continua da job", il server copia `lezione_*.tex`, `run_*.tex` e `images/` dal job precedente, garantendo che il PDF finale raccolga tutto il corso in un unico documento.

---

## Costi API

Con Claude Sonnet (modello di default):

| | Token tipici | Costo |
|--|-------------|-------|
| Input testo (trascrizione + slide + istruzioni) | ~40.000 | ~$0.12 |
| Input immagini (10 slide PNG a ~1920px) | ~15.000 | ~$0.05 |
| Output (capitolo LaTeX) | ~10.000 | ~$0.15 |
| **Per lezione con slide** | ~65.000 | **~$0.32** |
| **Per lezione solo testo** | ~50.000 | **~$0.27** |
| **Corso da 30 lezioni** | ~1.950.000 | **~$9.60** |

Il system prompt (~600 token) è cacheato con `cache_control: ephemeral` — dalla seconda lezione in poi costa ~10% del normale. Il log mostra `cache=hit` o `cache=miss` per ogni chiamata.

Le immagini vengono allegate **solo per PPTX** (contenuto visivo non riducibile a testo). Se non ci sono slide, la chiamata è solo testo e rimane nel costo inferiore.

---

## Preprocessor

Prima di inviare il contenuto a Claude, `preprocessor.py` esegue quattro fasi automatiche:

**1. Pulizia** — rimozione header ripetuti, numeri di pagina, timestamp Whisper, frasi riempitive, deduplicazione paragrafi (hash MD5).

**2. Rilevamento materia** — analisi keyword → uno tra `ingegneria`, `matematica`, `fisica`, `medicina`, `economia`, `giurisprudenza`, `generico`. Ogni profilo inietta istruzioni LaTeX specifiche nel prompt (es. matematica → `\begin{proof}`, medicina → dosaggi in tabella). Forzabile con `--subject`.

**3. Compressione adattiva** — stima i token del prompt e sceglie la modalità: `RAW_CLEAN` (<80k token, testo pulito completo), `DENSE` (80–180k, rimozione esempi ridondanti), `OUTLINE` (>180k, solo struttura gerarchica). La trascrizione Whisper viene inclusa con i timestamp `[MM:SS]` originali — Claude li usa per seguire l'evoluzione temporale della lezione.

**4. Contesto corso** — dopo ogni lezione, `corso_context.json` viene aggiornato con i concetti chiave (titoli section/subsection), definizioni (`\begin{definition}`), simboli introdotti, e l'ultimo argomento spiegato verbalmente (`last_verbal_topic`). Dalla lezione successiva il prompt include:
- `## CONTESTO DEL CORSO` con i concetti già trattati (non da rispiegare)
- `## RACCORDO CON LEZIONE PRECEDENTE` — da dove ripartire

Si disabilita con `--no-context`. Pruning automatico: oltre le ultime 10 lezioni, le più vecchie vengono compresse.

**Compressione automatica:**

| Token stimati | Modalità | Comportamento |
|--------------|----------|--------------|
| < 80.000 | `RAW_CLEAN` | Testo pulito, struttura completa |
| 80.000–180.000 | `DENSE` | Rimozione esempi ridondanti |
| > 180.000 | `OUTLINE` | Solo struttura gerarchica |

---

## File del progetto

| File | Ruolo |
|------|-------|
| `pipeline.py` | Orchestratore principale |
| `server.py` | Backend FastAPI + editor LaTeX integrato |
| `index.htm` | Frontend web (upload, editor split, PDF viewer, history) |
| `schema.htm` | Diagramma architettura interattivo |
| `docs.htm` | Documentazione interattiva (19 sezioni, sidebar, burger mobile) |
| `assets/css/shared.css` | Stili condivisi da tutte le pagine (variabili, navbar, tema) |
| `preprocessor.py` | Normalizza e comprime testo; rileva materia; compressione adattiva RAW/DENSE/OUTLINE; gestisce contesto corso |
| `extractor.py` | Parsing approfondito `.pptx` (testo, immagini, tabelle, formule OMML) |
| `slide_renderer.py` | Ogni slide PPTX → PNG (LibreOffice+pymupdf prioritario, Pillow come fallback) |
| `pdf_renderer.py` | Ogni pagina PDF → PNG + LaTeX skeleton |
| `omml2latex.py` | Formule OMML (PowerPoint) → LaTeX |
| `formula_detector.py` | Riconosce immagini-formula (aspect ratio, luminosità, saturazione) |
| `ocr_math.py` | OCR immagini-formula: pix2tex → latex-ocr → tesseract → euristico |
| `builder.py` | Assembla il file `.tex` finale; escape unicode → LaTeX (150+ caratteri: greche, operatori, subscript…) |
| `TeamsHack.py` | Scarica video Microsoft Teams via ffmpeg (anche standalone) |
| `requirements.txt` | Dipendenze Python |
| `tests/test_core.py` | Test sui moduli core |

---

## Note pratiche

**Whisper su CPU:** `base` impiega ~1 minuto per 10 minuti di audio. Per test usa `--whisper-model tiny` (4× più veloce). La trascrizione viene salvata in cache `.transcript.txt` — esecuzioni successive la riusano.

**`--skip-ai` salva comunque il prompt:** anche con `--skip-ai` il prompt completo (testo + lista immagini) viene scritto in `debug/prompt_lezione_NN.txt` e i symlink alle immagini in `debug/images_lezione_NN/`. Utile per verificare cosa verrebbe inviato prima di consumare crediti API.

**`--skip-ai` non salta Whisper:** `--skip-ai` disattiva solo Claude. Whisper gira sempre (è locale). Il contesto corso non viene aggiornato se Claude non viene chiamato.

**PDF grandi (chunking automatico):** PDF > 20 pagine senza audio → suddiviso in chunk da 10 pagine, ognuno elaborato da Claude separatamente. Con audio associato il PDF viene usato come scheletro strutturale senza chunking.

**Estrazione PDF duale:** ogni pagina viene estratta sia con pdfplumber sia con PyMuPDF. Vince quella con la densità testuale più alta (`caratteri non-spazio / totale`). `pdfplumber` usa `layout=False` per evitare il padding spaziale artificiale. Un post-processing rimuove automaticamente i caratteri raddoppiati (artefatto frequente in alcuni PDF: `ffiilliippppoo` → `filippo`) se più del 40% dei caratteri è una coppia duplicata.

**PDF scansionati:** pytesseract viene applicato automaticamente sulle pagine senza testo digitale. Copre sia PDF 100% scansionati sia PDF misti. La lingua OCR segue `WHISPER_LANG`; senza variabile usa `eng+ita` (inglese prioritario).

**Formule PowerPoint:** le formule scritte con l'editor equazioni di Office (OMML) vengono convertite direttamente da `omml2latex.py` senza OCR. Le formule come immagini (screenshot, foto di lavagna) vengono rilevate da `formula_detector.py` e passate a pix2tex.

**Unicode nel testo:** `builder.py` gestisce automaticamente 150+ caratteri Unicode comuni nei PDF/DOCX/PPTX — subscript (₂→`$_{2}$`), lettere greche (α→`$\alpha$`), operatori (≤→`$\leq$`), simboli testo (€→`\texteuro{}`). Caratteri non mappati passano attraverso ed eventualmente causano errori LaTeX visibili in `main.log`.

**Rendering slide:** se LibreOffice è installato, le slide PPTX vengono convertite a PDF e poi renderizzate con pymupdf a 200 DPI — fedeltà massima (font reali, temi, gradienti). Se LibreOffice non è disponibile, si usa python-pptx + Pillow (rendering elemento per elemento). In entrambi i casi le immagini vengono allegate a Claude.

**Output Claude:** prima di scrivere il `.tex` su disco, la risposta viene pulita automaticamente — vengono rimossi code fences markdown (` ```latex ``` `), testo introduttivo prima di `\section` e note conclusive. Se qualcosa viene rimosso viene loggato con anteprima.

**Lingua Whisper:** solo `en` (default) e `it` sono supportati. Se auto-detect rileva un'altra lingua, la pipeline ri-trascrive forzando `en`. Per forzare italiano:
```bash
export WHISPER_LANG=it
```

**Auto-save editor:** le modifiche nel pannello LaTeX vengono salvate automaticamente in `localStorage` dopo 2 secondi di inattività. Se si riapre lo stesso job prima di aver cliccato "💾 Salva", la bozza locale viene ripristinata con un avviso giallo. Si cancella automaticamente dopo il salvataggio confermato.

**Timeout configurabili** (senza riavviare il server):
```bash
curl -X POST http://localhost:8000/settings \
  -H "Content-Type: application/json" \
  -d '{"ffmpeg_timeout": 14400, "pipeline_timeout": 7200}'
```

**Concorrenza job:** default 2 job contemporanei. HTTP 429 se si supera il limite. Configurabile via `max_concurrent_jobs` in `/settings`.

**Debug:** ogni job scrive in `output/{job_id}/{nome}/debug/`:
- `prompt_lezione_NN.txt` — SYSTEM + USER prompt completo + lista immagini allegate
- `images_lezione_NN/` — symlink ordinati (01_, 02_, …) alle PNG esatte inviate a Claude
- `riepilogo_lezione_NN.txt` — classificazione sorgenti con char count

**Sicurezza `settings.json`:** il file contiene la API key in chiaro. I permessi vengono impostati a `600` (solo proprietario) al salvataggio. Per ambienti condivisi preferire la variabile d'ambiente `ANTHROPIC_API_KEY`.

**Test suite:**
```bash
source venv/bin/activate
python -m pytest tests/ -v
```

---

## Troubleshooting

| Problema | Causa | Soluzione |
|----------|-------|-----------|
| `ModuleNotFoundError: fitz` | pymupdf mancante | `pip install pymupdf` nel venv attivo |
| `ModuleNotFoundError: anthropic` | anthropic SDK mancante | `pip install anthropic` nel venv attivo |
| `images/` vuota | pymupdf non trovato | `python -c "import fitz"` nel venv attivo |
| PDF scansionato senza testo | pdfplumber estrae 0 testo | `pip install pytesseract && sudo apt install tesseract-ocr tesseract-ocr-ita` |
| `ffprobe: command not found` | ffprobe non installato | `sudo apt install ffmpeg` |
| Claude non risponde | API key mancante o errata | `echo $ANTHROPIC_API_KEY` per verificare |
| Più `lezione_NN.tex` generati | Batch o PDF > 20 pag senza audio | Comportamento atteso — vedi *PDF grandi* |
| `pdflatex` fallisce | Pacchetti LaTeX mancanti | `sudo apt install texlive-latex-extra texlive-lang-italian texlive-fonts-recommended` |
| Errore Unicode nel PDF | Carattere non mappato in builder.py | Cerca il carattere nel `main.log`; se comune, aprire issue |
| Frontend non raggiungibile da remoto | Server non su `0.0.0.0` | Avvia con `--host 0.0.0.0`; usa IP del server o Tailscale IP |
| pix2tex non trovato (`['heuristic']`) | Venv non nei path standard | Installa in `~/pix2tex_venv` o `~/Scrivania/venv` (entrambi cercati automaticamente) |
| `NNPACK: Unsupported hardware` in stderr | CPU senza istruzioni NNPACK | Warning innocuo — pix2tex funziona ugualmente |
| pix2tex lento (30-60s per formula) | Modello ML su CPU | Normale; la cache `.ocr_cache.json` evita rielaborazioni |
| Raccordo inter-lezione non attivo | Contesto corso assente | Usa il campo "Continua da job" nel frontend |
| Job non compaiono nella history | Era un bug: il frontend cancellava i job 5s dopo il completamento | Fix già applicato — i job ora persistono in `jobs.json` |
| PDF con testo raddoppiato (`ffiilliippppoo`) | Artefatto di alcuni PDF | Fix automatico: se >40% coppie duplicate, deduplicazione via regex |
| Testo PDF con eccessivo whitespace | pdfplumber `layout=True` generava padding spaziale | Fix: `layout=False` + estrazione duale con PyMuPDF |
| PDF non compilato — badge rosso | Errori LaTeX nel sorgente | Clicca sul badge per vedere gli errori; scarica lo ZIP per `main.log` |
| HTTP 429 su `/run-pipeline` | Troppi job contemporanei | Attendi o aumenta `max_concurrent_jobs` via `/settings` |
| Pipeline killata dopo 1 ora | `pipeline_timeout` default 3600s | `POST /settings {"pipeline_timeout": 7200}` |
| Download Teams fallisce su video > 2h | `ffmpeg_timeout` default 7200s | `POST /settings {"ffmpeg_timeout": 14400}` |
| `RuntimeError: fp16 is not supported on CPU` | Whisper su CPU senza CUDA | Fix già incluso — `fp16=False` automatico su CPU |
| Tabelle PPTX assenti nel LaTeX | Tabella come immagine, non oggetto nativo | Le tabelle embedded come screenshot non sono estraibili |
| Allineamento slide↔audio spostato | Pause lunghe distorcevano la distribuzione | Fix già applicato: pause > 45s vengono cappate |
| Immagini non allegate a Claude | Nessun PPTX nella lezione | Le PNG PDF vengono allegate solo senza audio; quelle PPTX sempre |
| Debug `images_lezione_NN/` vuoto | Nessuna immagine trovata in `images/` | Verifica che slide_renderer abbia prodotto i PNG (log: `✓ Renderizzate N/N slide`) |
| Slide renderizzate con font sbagliati | LibreOffice non installato, uso Pillow | `sudo apt install libreoffice` — le slide torneranno pixel-perfect |
| Claude risponde in inglese su audio italiano | `WHISPER_LANG` non impostato | `export WHISPER_LANG=it` prima di avviare il server |
| `.tex` contiene testo prima di `\section` | Claude ha aggiunto un'introduzione | Fix automatico: `_clean_claude_output` lo rimuove; se persiste controlla `main.log` |
