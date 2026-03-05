# Appunti AI

Sistema per trasformare fonti miste di una lezione universitaria (audio, video, slide, PDF, documenti) in un libro LaTeX strutturato e compilabile.

---

## Installazione rapida (prima volta)

### 1. Dipendenze di sistema

```bash
sudo apt install ffmpeg texlive-full
```

### 2. Clona il progetto e crea il venv

```bash
git clone <repo_url> ~/appunti_ai
cd ~/appunti_ai
python3 -m venv venv
source venv/bin/activate
```

### 3. Installa le dipendenze Python

```bash
pip install openai-whisper pdfplumber python-pptx python-docx \
            Pillow numpy lxml fastapi uvicorn python-multipart \
            aiofiles pymupdf
```

> **pymupdf** è necessario per renderizzare le pagine PDF e le slide PPTX come immagini PNG.
> Se l'installazione fallisce: `pip install --user pymupdf`

### 4. pix2tex — OCR formule (opzionale, ambiente separato)

pix2tex converte immagini di formule matematiche in LaTeX. Ha dipendenze pesanti (PyTorch ~2GB) che possono confliggere con il venv principale, quindi va installato in un venv separato.

**Percorsi supportati** (ocr_math.py cerca in ordine):
- `~/pix2tex_venv/bin/python` ← **consigliato, funziona su tutti i sistemi**
- `~/Scrivania/venv/bin/python` ← alias Desktop italiano
- `~/venv/bin/python`
- `~/.venv/bin/python`
- `/opt/pix2tex_venv/bin/python`

```bash
# Crea il venv dedicato
python3 -m venv ~/pix2tex_venv
source ~/pix2tex_venv/bin/activate

# Installa pix2tex (scarica PyTorch + dipendenze, ~2GB)
pip install pix2tex

# Verifica che funzioni
python -c "from pix2tex.cli import LatexOCR; print('import OK'); m = LatexOCR(); print('modello OK')"
# Al primo avvio scarica automaticamente i pesi del modello (~116MB in ~/.cache o nel venv)
```

**Verifica che ocr_math.py lo trovi:**
```bash
source ~/appunti_ai/venv/bin/activate
python -c "from ocr_math import get_available_backends; print(get_available_backends())"
# Deve mostrare: ['pix2tex (subprocess)', 'heuristic']
```

> Se non è installato, le immagini-formula vengono saltate senza errori — la pipeline continua normalmente.
> Le formule scritte con l'editor equazioni di PowerPoint (OMML) vengono sempre convertite correttamente tramite `omml2latex.py`, indipendentemente da pix2tex.

### 5. API key Claude (opzionale)

```bash
export ANTHROPIC_API_KEY='sk-ant-...'
# Per renderlo permanente:
echo "export ANTHROPIC_API_KEY='sk-ant-...'" >> ~/.bashrc
```

Senza API key la pipeline funziona ugualmente con `--skip-ai`.

### 6. Verifica installazione

```bash
source venv/bin/activate
python -c "import fitz; print('pymupdf OK:', fitz.__version__)"
python -c "import pdfplumber; print('pdfplumber OK')"
python -c "import whisper; print('whisper OK')"
python -c "import fastapi, uvicorn; print('server OK')"
python pipeline.py --help

# Verifica pix2tex (se installato nel venv separato)
python -c "from ocr_math import get_available_backends; print(get_available_backends())"
```

Output atteso se tutto è installato:
```
['pix2tex (subprocess)', 'heuristic']   ← pix2tex trovato
['heuristic']                            ← pix2tex non trovato (funziona uguale)
```

---

## Architettura

```
fonti grezze
(audio / video / pptx / pdf / docx / txt)
        │
        ▼
  pipeline.py                ← orchestratore principale
        │
        ├── ffmpeg            → estrae audio da video
        ├── Whisper           → trascrive audio in testo con timestamp
        │
        ├── extractor.py      → parsing .pptx (testo, immagini, formule OMML)
        ├── omml2latex.py     → converte formule PowerPoint in LaTeX
        ├── formula_detector.py → riconosce immagini-formula
        ├── ocr_math.py       → pix2tex: OCR formula immagine → LaTeX
        ├── slide_renderer.py → renderizza ogni slide PPTX come PNG
        │
        ├── pdfplumber        → estrae testo da PDF (con chunking auto)
        ├── pdf_renderer.py   → renderizza ogni pagina PDF come PNG
        │
        ├── preprocessor.py   → normalizza, pulisce, comprime prima di Claude
        │       ├── NormalizedDocument
        │       │       ├── RAW_CLEAN  (<80k token)
        │       │       ├── DENSE      (80k–180k token)
        │       │       └── OUTLINE    (>180k token)
        │       ├── detect_subject()             → rileva tipo materia
        │       ├── align_transcript_to_slides() → allineamento temporale
        │       ├── update_course_context()      → aggiorna memoria corso
        │       └── corso_context.json           → concetti, definizioni, simboli
        │
        ├── Claude API        → genera LaTeX semantico (opzionale)
        │
        └── builder.py        → assembla i file .tex finali
                │
                ▼
           output/
           ├── main.tex
           ├── lezione_01.tex
           ├── lezione_02.tex
           └── images/
                ├── slide_001.png          ← screenshot slide PPTX
                ├── nomepdf_pag_001.png    ← screenshot pagina PDF
                └── ...
```

---

## File del progetto

| File | Ruolo |
|------|-------|
| `pipeline.py` | Orchestratore principale — coordina tutti i moduli |
| `server.py` | Backend FastAPI — espone la pipeline via HTTP, gestisce download Teams |
| `index.htm` | Frontend web — drag & drop, opzioni, polling stato, download |
| `schema.htm` | Diagramma architettura interattivo — clicca su ogni blocco per i dettagli |
| `preprocessor.py` | Normalizza e comprime il testo; rileva la materia; allinea trascrizione e slide; gestisce il contesto corso |
| `extractor.py` | Parsing approfondito dei file `.pptx` |
| `slide_renderer.py` | Renderizza ogni slide PPTX come PNG (pymupdf) |
| `pdf_renderer.py` | Renderizza ogni pagina PDF come PNG (pymupdf) |
| `omml2latex.py` | Conversione formule OMML (PowerPoint) → LaTeX |
| `formula_detector.py` | Riconosce immagini che contengono formule matematiche |
| `ocr_math.py` | OCR su immagini-formula tramite pix2tex |
| `builder.py` | Costruisce il file `.tex` finale dalla struttura estratta |
| `TeamsHack.py` | Scarica video da Microsoft Teams tramite ffmpeg (anche standalone) |

---

## Gerarchia delle fonti

La pipeline assegna automaticamente un ruolo semantico a ogni file:

| Ruolo | File | Comportamento |
|-------|------|---------------|
| **SCHELETRO** | `.pptx` sempre; `.pdf` e `.docx` se c'è audio | Struttura ufficiale della lezione |
| **CARNE** | `.mp3` `.wav` `.mp4` ecc. | Spiegazione orale del professore |
| **SUPPORTO** | `.pdf` e `.docx` senza audio | Materiale di approfondimento |
| **CONTORNO** | `.txt` `.md` | Note informali, peso minore |

Claude riceve le fonti con questa gerarchia esplicita nel prompt. In assenza di Claude (`--skip-ai`), la struttura viene comunque rispettata per costruire il LaTeX.

---

## Uso da riga di comando

### Singola lezione

```bash
# Con tutto (Claude + Whisper + OCR)
python pipeline.py ./lezione_01/ --title "Digital Control"

# Con tipo materia esplicito (altrimenti auto-detect)
python pipeline.py ./lezione_01/ --title "Analisi 1" --subject matematica

# Offline rapido (no Claude, no OCR) — produce comunque LaTeX strutturato con immagini
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
| `--subject` | auto-detect | Tipo materia: `ingegneria` `matematica` `fisica` `medicina` `economia` `giurisprudenza` `generico` |
| `--no-context` | off | Non usare/aggiornare `corso_context.json` |
| `--skip-ai` | off | Non chiamare Claude (usa struttura automatica con immagini) |
| `--skip-ocr` | off | Non usare pix2tex (più veloce) |
| `--whisper-model` | `base` | Modello Whisper: tiny/base/small/medium/large |
| `--batch` | off | Ogni sottocartella = una lezione |
| `--start-from` | auto | Numero iniziale lezioni (default: auto da `state.json`) |

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
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

### Apri il frontend

Apri `http://localhost:8000` nel browser (il server serve automaticamente `index.htm`).
Da remoto usa l'IP o hostname del server al posto di `localhost`.

Il frontend permette di:

- Trascinare file audio, video, slide, documenti
- Incollare URL manifest di Microsoft Teams (scaricati automaticamente via ffmpeg)
- Scegliere la materia tra 7 profili disciplinari (o auto-detect)
- Impostare titolo, Claude on/off, OCR, contesto corso, modello Whisper, output dir
- Avviare la pipeline e monitorare lo stato in tempo reale con percentuale
- Scaricare lo `.zip` con il risultato quando pronto (cleanup automatico dopo download)

La pagina **Schema** (`http://localhost:8000/schema.htm`) mostra un diagramma
interattivo dell'architettura del sistema — cliccando su ogni blocco si vedono
i dettagli del modulo corrispondente.

### Endpoint API

| Endpoint | Metodo | Descrizione |
|----------|--------|-------------|
| `/` | GET | Serve `index.htm` (pipeline frontend) |
| `/schema.htm` | GET | Serve `schema.htm` (diagramma architettura interattivo) |
| `/run-pipeline` | POST | Avvia pipeline, ritorna `job_id` immediatamente. Parametri: `title`, `files[]`, `teams_url[]`, `skip_ai`, `skip_ocr`, `no_context`, `whisper_model`, `output`, `start_from`, `subject` |
| `/job/{job_id}` | GET | Stato del job: `queued / running / done / error` + progress, step, detail |
| `/download/{job_id}` | GET | Scarica lo `.zip` con i file `.tex` + `images/` |
| `/job/{job_id}` | DELETE | Elimina uploads temporanei. Aggiungere `?full=true` per eliminare anche zip e output definitivi |
| `/jobs` | GET | Lista tutti i job (debug) |
| `/docs` | GET | Documentazione interattiva FastAPI |

### Accesso remoto

Il server ascolta su `0.0.0.0` quindi è raggiungibile da qualsiasi dispositivo nella stessa rete locale usando l'IP del server (es. `http://192.168.1.x:8000`).

Per accesso da reti esterne (es. Mac fuori casa → Linux a casa) il modo più semplice è **Tailscale**:

```bash
# Sul server Linux
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Sul Mac client
# Scarica Tailscale da https://tailscale.com/download
```

Dopo aver fatto login con lo stesso account su entrambi i dispositivi, il server è raggiungibile tramite il suo Tailscale IP o hostname (es. `http://nome-macchina:8000`). Il Tailscale IP del server si trova con `tailscale ip -4`.

> Tutti gli URL nel frontend si adattano automaticamente: usano `window.location.origin` invece di indirizzi hardcoded.

---

## Fonti supportate

| Tipo | Estensioni | Come viene processato |
|------|-----------|----------------------|
| Audio | `.mp3` `.wav` `.m4a` `.ogg` `.flac` | Whisper → testo con timestamp [MM:SS] |
| Video | `.mp4` `.mkv` `.avi` `.mov` `.webm` | ffmpeg → mp3 → Whisper |
| Teams URL | URL manifest (incollato in UI) | server.py → ffmpeg → mp3 mono → Whisper |
| Slide | `.pptx` | extractor → testo + OMML + immagini; slide_renderer → PNG per slide |
| Word | `.docx` | python-docx → testo plain |
| PDF | `.pdf` | pdfplumber → testo; pdf_renderer → PNG per pagina |
| Testo | `.txt` `.md` `.rtf` | lettura diretta (RTF con strip automatico) |

I PDF con più di 20 pagine vengono divisi automaticamente in chunk da 10 pagine. Ogni chunk genera un `lezione_NN.tex` separato (es. 275 pagine → 27 file).

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

Il preprocessor analizza le keyword presenti nelle slide e nella trascrizione e assegna automaticamente un profilo tra: `ingegneria`, `matematica`, `fisica`, `medicina`, `economia`, `giurisprudenza`, `generico`. Ogni profilo include istruzioni LaTeX specifiche iniettate nel prompt — per esempio per matematica richiede `\begin{proof}` dopo ogni teorema, per medicina impone dosaggi in tabella, per ingegneria la notazione vettoriale `$\mathbf{x}$`. Il profilo può essere forzato da CLI con `--subject`.

**3. Allineamento temporale trascrizione ↔ slide**

Se la trascrizione Whisper ha i timestamp `[MM:SS]` e le slide hanno i marker `--- SLIDE N ---`, il preprocessor stima il range temporale di ogni slide e associa i segmenti audio corrispondenti. Il prompt che arriva a Claude non è più due blocchi separati, ma slide per slide: `[CONTENUTO SLIDE]` seguito dalla `[SPIEGAZIONE ORALE]` corrispondente. Senza timestamp Whisper, la distribuzione avviene in modo uniforme.

**4. Contesto corso — `corso_context.json`**

Dopo ogni lezione generata, il preprocessor estrae automaticamente dal LaTeX prodotto i concetti chiave (titoli di section/subsection), le definizioni (`\begin{definition}`), e i simboli introdotti. Questi vengono salvati in `output/corso_context.json`. Dalla lezione successiva in poi, il prompt include una sezione `## CONTESTO DEL CORSO` con la lista delle lezioni precedenti e l'istruzione esplicita "Concetti già introdotti — NON ri-spiegare da zero". Si disabilita con `--no-context`.

**Compressione automatica** in base ai token stimati:

| Token stimati | Modalità | Comportamento |
|--------------|----------|--------------|
| < 80.000 | `RAW_CLEAN` | Testo pulito, struttura completa |
| 80.000 – 180.000 | `DENSE` | Rimozione esempi ridondanti e frasi riempitive |
| > 180.000 | `OUTLINE` | Solo struttura gerarchica + prime righe per sezione |

---

## Struttura output

```
output/
├── main.tex                   # documento principale, include tutti i capitoli
├── lezione_01.tex             # capitolo 1
├── lezione_02.tex             # capitolo 2
├── ...
├── corso_context.json         # memoria del corso (concetti, definizioni, simboli)
└── images/
    ├── slide_001.png          # screenshot slide 1 del PPTX
    ├── slide_002.png          # screenshot slide 2 del PPTX
    ├── nomepdf_pag_001.png    # screenshot pagina 1 del PDF
    ├── nomepdf_pag_002.png    # screenshot pagina 2 del PDF
    ├── slide001_abc123.png    # immagine embedded estratta dal PPTX
    └── formula_def456.png     # immagine formula estratta (→ pix2tex)
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
Il modello `base` su CPU impiega circa 1 minuto ogni 10 minuti di audio. Per test usa `--whisper-model tiny` (4× più veloce). La trascrizione viene salvata in cache `.transcript.txt` — esecuzioni successive saltano Whisper se il file esiste già. Durante la trascrizione la UI aggiorna il progresso ogni 15 secondi stimando la percentuale completata in base alla durata del file (rilevata con ffprobe).

**`--skip-ai` non salta Whisper:**
`--skip-ai` disattiva solo Claude. Whisper gira sempre perché è trascrizione locale. Il contesto corso (`corso_context.json`) non viene aggiornato se Claude non viene chiamato.

**PDF grandi:**
Un PDF da 275 pagine genera automaticamente 27-28 file `lezione_NN.tex` (chunk da 10 pagine), tutti inclusi in `main.tex`. Le immagini PNG delle pagine vengono generate una sola volta e salvate in `images/` con naming `nomefile_pag_001.png`.

**Cache immagini:**
Se i PNG esistono già in `images/`, non vengono rirenderizzati. Per forzare il rirenderizzamento cancella i file PNG dalla cartella.

**TeamsHack — URL manifest:**
Il frontend accetta URL di videomanifest Teams (es. `.m3u8` o URL stream). Incollati nella zona Teams dell'UI, vengono inviati a `server.py` che li scarica via `ffmpeg -i <url> -vn ... .mp3` prima di avviare la pipeline. Non è necessario scaricare manualmente il video. `TeamsHack.py` rimane disponibile anche come script standalone da terminale con modalità video+mp3 e contatori automatici.

**pix2tex e formule:**
Le formule nei file `.pptx` vengono gestite in due modi distinti: quelle create con l'editor equazioni di PowerPoint (OMML) vengono convertite direttamente dall'XML tramite `omml2latex.py` — zero OCR, qualità alta. Le formule inserite come immagini (screenshot, foto di lavagna, PNG incollati) vengono rilevate da `formula_detector.py` in base ad aspect ratio, sfondo chiaro e bassa saturazione, e poi passate a pix2tex. Usa `--skip-ocr` per saltare questo step e velocizzare l'esecuzione. I risultati sono salvati in cache `.ocr_cache.json` accanto all'immagine.

**Moduli opzionali:**
Se `extractor.py`, `slide_renderer.py`, `pdf_renderer.py` e gli altri moduli collega non sono presenti, la pipeline usa un fallback base che funziona comunque. La qualità dell'output (immagini slide, formule OMML) è però significativamente migliore con i moduli completi.

**Cartella lezione — nome:**
Il nome della cartella della lezione viene usato come titolo. Usa nomi leggibili tipo `lezione_01_limiti` invece di hash o nomi generici.

**Debug prompt:**
Ogni chiamata a Claude salva il prompt completo in `debug/prompt_lezione_NN.txt`. Utile per verificare cosa riceve Claude e stimare i token.

**API key Claude:**
```bash
export ANTHROPIC_API_KEY='sk-ant-...'
# Per renderlo permanente:
echo "export ANTHROPIC_API_KEY='sk-ant-...'" >> ~/.bashrc
```

---

## Troubleshooting

| Problema | Causa | Soluzione |
|----------|-------|-----------|
| `ModuleNotFoundError: fitz` | pymupdf non installato nel venv attivo | `pip install pymupdf` con il venv attivo |
| `images/` vuota | pymupdf non trovato al momento dell'esecuzione | Verifica `python -c "import fitz"` nel venv attivo |
| Unico `lezione_01.tex` per PDF grande | Chunking non attivato | Assicurarsi che il PDF non abbia audio associato nella stessa cartella |
| Titolo lezione incomprensibile | Nome cartella hash o generico | Rinomina la cartella con un nome descrittivo |
| `ffprobe: command not found` | ffprobe non installato | `sudo apt install ffmpeg` (include ffprobe) |
| Claude non risponde | API key mancante o errata | `echo $ANTHROPIC_API_KEY` per verificare |
| Frontend non raggiungibile da remoto | Server non in ascolto su `0.0.0.0` | Avvia con `--host 0.0.0.0`; da remoto usa l'IP del server (o Tailscale IP) |
| `pdflatex` fallisce | Pacchetti LaTeX mancanti | `sudo apt install texlive-full` |
| pix2tex non trovato (`['heuristic']` only) | Venv non nei path cercati | Usa `~/pix2tex_venv` oppure `~/Scrivania/venv` (italiano) o `~/venv` |
| `NNPACK: Unsupported hardware` in stderr | CPU senza istruzioni NNPACK | Warning innocuo — pix2tex funziona ugualmente su CPU normale |
| pix2tex lento (30-60s per formula) | Modello ML su CPU, nessuna GPU | Normale su CPU; la cache `.ocr_cache.json` evita di riprocessare le stesse immagini |
| Prima esecuzione pix2tex scarica pesi | Download automatico ~116MB | Attendi il download; successive esecuzioni usano la cache |