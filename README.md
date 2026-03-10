# Appunti AI

Trasforma fonti miste di una lezione universitaria — audio, video, slide, PDF, documenti — in un libro LaTeX strutturato e compilabile, usando Whisper per trascrivere e Claude per sintetizzare.

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

**macOS:**
```bash
brew install ffmpeg
brew install --cask basictex   # ~100 MB, sufficiente
# oppure scarica MacTeX completo da https://www.tug.org/mactex/
```

> **Altre lingue LaTeX:** per lezioni in inglese, francese, tedesco, spagnolo ecc. aggiungi il pacchetto babel corrispondente: `texlive-lang-european` copre francese/tedesco/spagnolo/olandese. La lingua viene auto-rilevata da Whisper e il documento LaTeX usa `\usepackage[lingua]{babel}` in modo dinamico.

### 2. Clona il repo e crea il venv

```bash
git clone <repo_url> ~/appunti_ai
cd ~/appunti_ai

python3 -m venv venv
source venv/bin/activate          # Linux/macOS
# oppure: venv\Scripts\activate   # Windows
```

### 3. Installa le dipendenze Python

```bash
pip install -r requirements.txt
```

### 4. (Opzionale) OCR per PDF scansionati

Se hai PDF senza testo selezionabile (scansioni, foto di libri):

```bash
pip install pytesseract

# Linux
sudo apt install tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng

# macOS
brew install tesseract
```

Senza pytesseract i PDF scansionati vengono saltati — la pipeline continua con le altre fonti.

### 5. (Opzionale) pix2tex — OCR formule matematiche

pix2tex converte immagini di formule in LaTeX. Ha dipendenze pesanti (PyTorch ~2 GB), quindi va installato in un **venv separato**:

```bash
python3 -m venv ~/pix2tex_venv
source ~/pix2tex_venv/bin/activate
pip install pix2tex

# Verifica (scarica i pesi ~116 MB al primo avvio)
python -c "from pix2tex.cli import LatexOCR; m = LatexOCR(); print('OK')"
```

`ocr_math.py` cerca automaticamente pix2tex nei path standard (`~/pix2tex_venv`, `~/venv`, `~/.venv`, `/opt/pix2tex_venv`). Se non trovato, le immagini-formula vengono saltate senza errori — le formule scritte con l'editor equazioni PowerPoint (OMML) vengono comunque convertite correttamente tramite `omml2latex.py`.

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

# Backend opzionali
python -c "from ocr_math import get_available_backends; print(get_available_backends())"
# ['pix2tex (subprocess)', 'heuristic']  ← pix2tex trovato
# ['heuristic']                          ← pix2tex assente (funziona uguale)
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
        ├── slide_renderer.py     → ogni slide PPTX → PNG
        │
        ├── pdfplumber            → estrae testo da PDF
        ├── pytesseract           → OCR fallback per PDF scansionati
        ├── pdf_renderer.py       → ogni pagina PDF → PNG
        │
        ├── preprocessor.py       → pulisce, comprime, allinea slide↔audio per Claude
        │
        ├── Claude API (cloud)    → genera LaTeX semantico strutturato
        │
        └── builder.py            → assembla i .tex finali
```

### Gerarchia delle fonti

La pipeline assegna automaticamente un ruolo a ogni file in base al tipo:

| Ruolo | File | Comportamento |
|-------|------|---------------|
| **SCHELETRO** | `.pptx` sempre; `.pdf` `.docx` se c'è audio | Struttura ufficiale della lezione |
| **CARNE** | `.mp3` `.wav` `.mp4` `.mkv` ecc. | Spiegazione orale del professore |
| **SUPPORTO** | `.pdf` `.docx` senza audio | Materiale di approfondimento |
| **CONTORNO** | `.txt` `.md` `.rtf` | Note informali, peso minore |

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
# Standard: Whisper + Claude
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
- Scaricare lo `.zip` con il risultato
- Vedere la history dei job con accesso diretto a ZIP e continuazione
- Controllare lo stato dei tool di sistema (API key, ffmpeg, pdflatex) in tempo reale
- Impostare l'API key Claude direttamente dall'interfaccia (persiste su `settings.json`)
- Alternare tema chiaro/scuro

La pagina `http://localhost:8000/schema.htm` mostra il diagramma interattivo dell'architettura.

### Continuità tra lezioni

Il campo **Continua da job** accetta il `job_id` di una sessione precedente. Il server copia automaticamente `state.json` e `corso_context.json` — la numerazione e la memoria del corso proseguono da dove ci si era fermati.

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
| `/run-pipeline` | POST | Avvia pipeline, ritorna `job_id` |
| `/job/{job_id}` | GET | Stato job + progress, step, detail, pdf_errors |
| `/job/{job_id}` | DELETE | Elimina job (`?full=true` rimuove anche ZIP e output) |
| `/download/{job_id}` | GET | Scarica `.zip` output |
| `/preview/{job_id}` | GET | Contenuto `main.tex` in JSON |
| `/job/{job_id}/stream` | GET | Server-Sent Events: log pipeline in tempo reale |
| `/jobs` | GET | Lista tutti i job |
| `/health` | GET | Stato tool di sistema: `api_key`, `ffmpeg`, `pdflatex`, `whisper` |
| `/settings` | GET | Configurazione corrente |
| `/settings` | POST | Salva `api_key`, `ttl_days` (1–365), `ffmpeg_timeout`, `pipeline_timeout`, `max_concurrent_jobs` (1–10) |
| `/docs` | GET | Documentazione interattiva FastAPI |

---

## Struttura output

```
output/
├── main.tex                   # documento principale — include tutti i capitoli
├── lezione_01.tex             # capitolo 1
├── lezione_02.tex             # capitolo 2
├── corso_context.json         # memoria corso (concetti, simboli, ultimo argomento)
├── state.json                 # stato pipeline (prossimo numero lezione)
└── images/
    ├── nome_slide_001.png     # screenshot slide 1 ({stem}_slide_NNN.png)
    ├── dispense_pag_001.png   # screenshot pagina 1 PDF ({stem}_pag_NNN.png)
    ├── slide001_abc123.png    # immagine embedded estratta dal PPTX
    └── formula_def456.png     # immagine formula → pix2tex
```

`main.tex` include un preambolo LaTeX completo con `amsmath`, `amssymb`, `amsthm`, `graphicx`, `hyperref`, `fancyhdr`, `babel` (lingua dinamica), ambienti `theorem`, `definition`, `example`, `lemma`, `corollary`, `remark`, e `listings` per blocchi codice.

---

## Costi API

Con Claude Sonnet (modello di default):

| | Token tipici | Costo |
|--|-------------|-------|
| Input (trascrizione + slide + istruzioni) | ~40.000 | ~$0.12 |
| Output (capitolo LaTeX) | ~10.000 | ~$0.15 |
| **Per lezione** | ~50.000 | **~$0.27** |
| **Corso da 30 lezioni** | ~1.500.000 | **~$8** |

Il system prompt (~600 token) è cacheato con `cache_control: ephemeral` — dalla seconda lezione in poi costa ~10% del normale. Il log mostra `cache=hit` o `cache=miss` per ogni chiamata.

---

## Preprocessor

Prima di inviare il contenuto a Claude, `preprocessor.py` esegue quattro fasi automatiche:

**1. Pulizia** — rimozione header ripetuti, numeri di pagina, timestamp Whisper, frasi riempitive, deduplicazione paragrafi (hash MD5).

**2. Rilevamento materia** — analisi keyword → uno tra `ingegneria`, `matematica`, `fisica`, `medicina`, `economia`, `giurisprudenza`, `generico`. Ogni profilo inietta istruzioni LaTeX specifiche nel prompt (es. matematica → `\begin{proof}`, medicina → dosaggi in tabella). Forzabile con `--subject`.

**3. Allineamento trascrizione↔slide** — se Whisper ha i timestamp `[MM:SS]` e le slide hanno i marker `--- SLIDE N ---`, il preprocessor stima il range temporale di ogni slide e associa i segmenti audio corrispondenti. Il prompt che arriva a Claude ha slide e spiegazione orale affiancate. Le pause tra segmenti sono cappate a 45 secondi per non distorcere l'allineamento.

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
| `server.py` | Backend FastAPI |
| `index.htm` | Frontend web |
| `schema.htm` | Diagramma architettura interattivo |
| `preprocessor.py` | Normalizza e comprime testo; rileva materia; allinea trascrizione↔slide; gestisce contesto corso |
| `extractor.py` | Parsing approfondito `.pptx` (testo, immagini, tabelle, formule OMML) |
| `slide_renderer.py` | Ogni slide PPTX → PNG |
| `pdf_renderer.py` | Ogni pagina PDF → PNG |
| `omml2latex.py` | Formule OMML (PowerPoint) → LaTeX |
| `formula_detector.py` | Riconosce immagini-formula (aspect ratio, luminosità, saturazione) |
| `ocr_math.py` | OCR immagini-formula: pix2tex → latex-ocr → tesseract → euristico |
| `builder.py` | Assembla il file `.tex` finale |
| `TeamsHack.py` | Scarica video Microsoft Teams via ffmpeg (anche standalone) |
| `requirements.txt` | Dipendenze Python |
| `tests/test_core.py` | 54 test sui moduli core |

---

## Note pratiche

**Whisper su CPU:** `base` impiega ~1 minuto per 10 minuti di audio. Per test usa `--whisper-model tiny` (4× più veloce). La trascrizione viene salvata in cache `.transcript.txt` — esecuzioni successive la riusano.

**`--skip-ai` non salta Whisper:** `--skip-ai` disattiva solo Claude. Whisper gira sempre (è locale). Il contesto corso non viene aggiornato se Claude non viene chiamato.

**PDF grandi (chunking automatico):** PDF > 20 pagine senza audio → suddiviso in chunk da 10 pagine, ognuno elaborato da Claude separatamente. Con audio associato il PDF viene usato come scheletro strutturale senza chunking.

**PDF scansionati:** pytesseract viene applicato automaticamente sulle pagine senza testo digitale. Copre sia PDF 100% scansionati sia PDF misti. La lingua OCR segue `WHISPER_LANG`; senza variabile usa `ita+eng`.

**Formule PowerPoint:** le formule scritte con l'editor equazioni di Office (OMML) vengono convertite direttamente da `omml2latex.py` senza OCR. Le formule come immagini (screenshot, foto di lavagna) vengono rilevate da `formula_detector.py` e passate a pix2tex.

**WHISPER_LANG:** imposta la lingua per Whisper, OCR e LaTeX babel.
```bash
export WHISPER_LANG=it   # italiano
export WHISPER_LANG=en   # inglese
export WHISPER_LANG=fr   # francese
```

**Timeout configurabili** (senza riavviare il server):
```bash
curl -X POST http://localhost:8000/settings \
  -H "Content-Type: application/json" \
  -d '{"ffmpeg_timeout": 14400, "pipeline_timeout": 7200}'
```

**Concorrenza job:** default 2 job contemporanei. HTTP 429 se si supera il limite. Configurabile via `max_concurrent_jobs` in `/settings`.

**Debug:** ogni job scrive in `output/{job_id}/{nome}/debug/`:
- `prompt_lezione_NN.txt` — prompt completo inviato a Claude
- `riepilogo_lezione_NN.txt` — classificazione sorgenti con char count

**Test suite:**
```bash
source venv/bin/activate
python -m pytest tests/ -v   # 54 test, ~1 secondo
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
| Frontend non raggiungibile da remoto | Server non su `0.0.0.0` | Avvia con `--host 0.0.0.0`; usa IP del server o Tailscale IP |
| pix2tex non trovato (`['heuristic']`) | Venv non nei path standard | Installa in `~/pix2tex_venv` (path cercato per primo) |
| `NNPACK: Unsupported hardware` in stderr | CPU senza istruzioni NNPACK | Warning innocuo — pix2tex funziona ugualmente |
| pix2tex lento (30-60s per formula) | Modello ML su CPU | Normale; la cache `.ocr_cache.json` evita rielaborazioni |
| Raccordo inter-lezione non attivo | Contesto corso assente | Usa il campo "Continua da job" nel frontend |
| PDF non compilato — badge rosso | Errori LaTeX nel sorgente | Clicca sul badge per vedere gli errori; scarica lo ZIP per `main.log` |
| HTTP 429 su `/run-pipeline` | Troppi job contemporanei | Attendi o aumenta `max_concurrent_jobs` via `/settings` |
| `settings.json` — warning permessi | File leggibile da altri utenti | Il server imposta `chmod 600` al salvataggio; esegui manualmente se il warning persiste |
| Pipeline killata dopo 1 ora | `pipeline_timeout` default 3600s | `POST /settings {"pipeline_timeout": 7200}` |
| Download Teams fallisce su video > 2h | `ffmpeg_timeout` default 7200s | `POST /settings {"ffmpeg_timeout": 14400}` |
| `RuntimeError: fp16 is not supported on CPU` | Whisper su CPU senza CUDA | Fix già incluso — `fp16=False` automatico su CPU |
| Tabelle PPTX assenti nel LaTeX | Tabella come immagine, non oggetto nativo | Le tabelle embedded come screenshot non sono estraibili |
| Allineamento slide↔audio spostato | Pause lunghe distorcevano la distribuzione | Fix già applicato: pause > 45s vengono cappate |
