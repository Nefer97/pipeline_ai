# Appunti AI

Sistema per trasformare fonti miste di una lezione universitaria (audio, video, slide, PDF, documenti) in un libro LaTeX strutturato e compilabile.

---

## Installazione rapida (prima volta)

### 1. Dipendenze di sistema

```bash
# Opzione A — minimale (pdflatex base, ~200 MB)
sudo apt install ffmpeg texlive-latex-base texlive-latex-recommended \
                 texlive-latex-extra texlive-lang-italian texlive-fonts-recommended

# Opzione B — completa, include tutti i pacchetti LaTeX (~2 GB)
sudo apt install ffmpeg texlive-full
```

> L'opzione A è sufficiente per compilare l'output di Appunti AI. Usa `texlive-full` solo se hai già poco spazio su disco occupato o hai bisogno di pacchetti LaTeX extra.
>
> **Altre lingue:** se le tue lezioni sono in inglese, francese, tedesco, spagnolo, ecc., aggiungi il pacchetto babel corrispondente — es. `texlive-lang-english` (di solito già incluso), `texlive-lang-european` per francese/tedesco/spagnolo/olandese. La lingua viene rilevata automaticamente da Whisper e il documento LaTeX usa `\usepackage[lingua]{babel}` in modo dinamico.

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
            aiofiles pymupdf anthropic
```

> **pymupdf** è necessario per renderizzare le pagine PDF e le slide PPTX come immagini PNG.
> **anthropic** è necessario per chiamare Claude. Senza API key la pipeline funziona ugualmente con `--skip-ai`.

### 4. pytesseract — OCR PDF scansionati (opzionale)

pytesseract permette di estrarre testo da PDF che non contengono testo selezionabile (PDF immagine, scansioni).

```bash
# Installazione librerie di sistema
sudo apt install tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng

# Installazione pacchetto Python
pip install pytesseract
```

La lingua usata per l'OCR segue automaticamente `WHISPER_LANG` (se impostata). Se non installato, i PDF scansionati vengono saltati senza errori — la pipeline continua normalmente con le altre fonti.

### 5. pix2tex — OCR formule (opzionale, ambiente separato)

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

### 6. API key Claude

```bash
export ANTHROPIC_API_KEY='sk-ant-...'
# Per renderlo permanente:
echo "export ANTHROPIC_API_KEY='sk-ant-...'" >> ~/.bashrc
```

Senza API key la pipeline funziona ugualmente con `--skip-ai`.

### 7. Verifica installazione

```bash
source venv/bin/activate
python -c "import fitz; print('pymupdf OK:', fitz.__version__)"
python -c "import pdfplumber; print('pdfplumber OK')"
python -c "import whisper; print('whisper OK')"
python -c "import fastapi, uvicorn; print('server OK')"
python -c "import anthropic; print('anthropic OK')"
python pipeline.py --help

# Verifica pix2tex (se installato nel venv separato)
python -c "from ocr_math import get_available_backends; print(get_available_backends())"

# Verifica pytesseract (se installato)
python -c "import pytesseract; print('pytesseract OK')"
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
        ├── pdfplumber        → estrae testo da PDF
        ├── pytesseract       → OCR fallback per PDF scansionati (0 testo da pdfplumber)
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
        │       └── corso_context.json           → concetti, definizioni, simboli, ultimo argomento
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
                ├── {stem}_slide_001.png   ← screenshot slide PPTX (prefisso = nome file .pptx)
                ├── {stem}_pag_001.png     ← screenshot pagina PDF (prefisso = nome file .pdf)
                └── ...
```

---

## File del progetto

| File | Ruolo |
|------|-------|
| `pipeline.py` | Orchestratore principale — coordina tutti i moduli |
| `server.py` | Backend FastAPI — espone la pipeline via HTTP, gestisce download Teams |
| `index.htm` | Frontend web — drag & drop, opzioni, polling stato, download |
| `assets/css/index.css` | Stili del frontend (estratti da index.htm) |
| `schema.htm` | Diagramma architettura interattivo — clicca su ogni blocco per i dettagli |
| `preprocessor.py` | Normalizza e comprime il testo; rileva la materia; allinea trascrizione e slide; gestisce il contesto corso |
| `extractor.py` | Parsing approfondito dei file `.pptx` |
| `slide_renderer.py` | Renderizza ogni slide PPTX come PNG (python-pptx + Pillow; pymupdf come fallback) |
| `pdf_renderer.py` | Renderizza ogni pagina PDF come PNG (pymupdf / fitz) |
| `omml2latex.py` | Conversione formule OMML (PowerPoint) → LaTeX |
| `formula_detector.py` | Riconosce immagini che contengono formule matematiche |
| `ocr_math.py` | OCR su immagini-formula: pix2tex (principale), latex-ocr, tesseract, euristico (fallback) |
| `builder.py` | Costruisce il file `.tex` finale dalla struttura estratta |
| `TeamsHack.py` | Scarica video da Microsoft Teams tramite ffmpeg (anche standalone) |

---

## Lavoro locale vs Claude — efficienza e costi

Appunti AI usa Claude **esattamente una volta per lezione**, per fare l'unica cosa che richiede intelligenza semantica: sintetizzare la trascrizione orale con le slide in prosa accademica LaTeX. Tutto il resto è elaborazione locale, gratuita e offline.

### Divisione del lavoro

| Fase | Strumento | Dove gira |
|------|-----------|-----------|
| Estrazione audio da video | ffmpeg | **locale** |
| Trascrizione audio → testo con timestamp | Whisper | **locale** |
| Parsing PPTX (testo, immagini, tabelle) | extractor.py + python-pptx | **locale** |
| Conversione formule PowerPoint (OMML) → LaTeX | omml2latex.py | **locale** |
| OCR immagini-formula | ocr_math.py (pix2tex / tesseract) | **locale** |
| Rendering slide → PNG | slide_renderer.py + Pillow | **locale** |
| Rendering pagine PDF → PNG | pdf_renderer.py + pymupdf | **locale** |
| Pulizia, compressione, allineamento slide↔audio | preprocessor.py | **locale** |
| **Sintesi accademica** → LaTeX strutturato | **Claude API** | **cloud (1 chiamata)** |
| Assemblaggio file `.tex` finale | builder.py | **locale** |
| Compilazione PDF | pdflatex | **locale** |

### Cosa fa Claude — e perché non si può sostituire facilmente

Claude riceve lo scheletro delle slide (già in LaTeX) + la trascrizione audio allineata per slide, e produce un capitolo LaTeX in cui la spiegazione del professore è integrata in modo semantico: capisce quando una frase orale espande un bullet point, quando un esempio va in `\begin{example}`, quando una precisazione va in `\begin{remark}`. Nessun modello locale di taglia ragionevole (LLaMA, Mistral) produce risultati comparabili sulla scrittura accademica strutturata.

### Costi API reali

Con **Claude 3 Sonnet** (modello di default):

| Componente | Token tipici | Costo |
|------------|-------------|-------|
| Input (trascrizione + slide + istruzioni) | ~40.000 | ~$0.12 |
| Output (capitolo LaTeX generato) | ~10.000 | ~$0.15 |
| **Totale per lezione** | ~50.000 | **~$0.27** |
| **Corso da 30 lezioni** | ~1.500.000 | **~$8** |

### Perché non si risparmiano molti più token

Il 90% dei token inviati a Claude è **contenuto informativo non comprimibile**: la trascrizione audio del professore (~60%) e il LaTeX estratto dalle slide (~25%). Ridurli significherebbe perdere informazione e abbassare la qualità dell'output.

Il 10% restante (istruzioni, contesto corso, metadati) è già ottimizzato:
- Il **system prompt** è cacheato da Anthropic — dalla seconda lezione in poi costa ~10% del normale
- Le istruzioni per tipo di fonte sono scritte **una volta per sezione**, non ripetute per ogni file
- Il contesto corso usa il **pruning automatico**: oltre 10 lezioni precedenti, le più vecchie vengono compresse

Il vero vantaggio economico di Appunti AI non è nel risparmio token, ma nell'**azzeramento del tempo umano**: nessuna copia-incolla, nessuna formattazione manuale, nessuna integrazione a mano di trascrizione e slide.

---

## Gerarchia delle fonti

La pipeline assegna automaticamente un ruolo semantico a ogni file:

| Ruolo | File | Comportamento |
|-------|------|---------------|
| **SCHELETRO** | `.pptx` sempre; `.pdf` e `.docx` se c'è audio | Struttura ufficiale della lezione |
| **CARNE** | `.mp3` `.wav` `.mp4` ecc. | Spiegazione orale del professore |
| **SUPPORTO** | `.pdf` e `.docx` senza audio | Materiale di approfondimento |
| **CONTORNO** | `.txt` `.md` `.rtf` | Note informali, peso minore |

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
python pipeline.py ./lezione_01/ --skip-ai --skip-ocr --title "Digital Control"

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
| `--title` | `"Appunti del Corso"` | Titolo per `main.tex` e per la `\chapter{}` della lezione |
| `--output` | `./output` | Cartella di output |
| `--subject` | auto-detect | Tipo materia: `ingegneria` `matematica` `fisica` `medicina` `economia` `giurisprudenza` `generico` |
| `--no-context` | off | Non usare/aggiornare `corso_context.json` |
| `--skip-ai` | off | Non chiamare Claude (usa struttura automatica con immagini) |
| `--skip-ocr` | off | Non usare pix2tex (più veloce) |
| `--whisper-model` | `base` | Modello Whisper: tiny/base/small/medium/large |
| `--batch` | off | Ogni sottocartella = una lezione |
| `--start-from` | auto | Numero iniziale lezioni (default: auto da `state.json`) |

### Continuità tra lezioni (frontend)

Il campo **Continua da job** nel frontend accetta il `job_id` di una sessione precedente. Il server copia automaticamente `state.json` e `corso_context.json` dall'output del job precedente prima di avviare la pipeline — in questo modo la numerazione delle lezioni e la memoria del corso proseguono da dove si era rimasti. Funziona anche dopo il riavvio del server: se il job non è più in memoria viene cercato su disco in `outputs/{job_id}/`.

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
- Impostare **titolo** (obbligatorio), Claude on/off, OCR, contesto corso, modello Whisper
- Avviare la pipeline e monitorare lo stato in tempo reale con percentuale
- Scaricare lo `.zip` con il risultato quando pronto (cleanup automatico dopo download)
- Consultare le **lezioni precedenti** (history panel) con accesso diretto a ZIP e continuazione
- Vedere lo **stato dei tool di sistema** (API key, ffmpeg, pdflatex) tramite badge in tempo reale
- Impostare la **API key Claude** direttamente dall'interfaccia (persiste su `settings.json`)
- Configurare la **retention degli output** (TTL giorni) nelle opzioni avanzate
- Visualizzare gli **errori pdflatex** in un pannello collassabile quando la compilazione fallisce
- Cambiare tra **tema chiaro e scuro** con il pulsante in navbar (preferenza salvata in localStorage)

> Il campo **Titolo corso / lezione** è obbligatorio — il pulsante Start rimane disattivo senza un titolo. La cartella di output viene generata automaticamente come slug del titolo (es. `"Digital Control 2"` → `./digital_control_2`).

La pagina **Schema** (`http://localhost:8000/schema.htm`) mostra un diagramma
interattivo dell'architettura del sistema — cliccando su ogni blocco si vedono
i dettagli del modulo corrispondente.

### Endpoint API

| Endpoint | Metodo | Descrizione |
|----------|--------|-------------|
| `/` | GET | Serve `index.htm` (pipeline frontend) |
| `/schema.htm` | GET | Serve `schema.htm` (diagramma architettura interattivo) |
| `/run-pipeline` | POST | Avvia pipeline, ritorna `job_id` immediatamente. Parametri: `title`, `files[]`, `teams_url[]`, `skip_ai`, `skip_ocr`, `no_context`, `whisper_model`, `output`, `start_from`, `subject`, `continue_from` |
| `/job/{job_id}` | GET | Stato del job: `queued / running / done / error` + progress, step, detail, pdf_errors |
| `/download/{job_id}` | GET | Scarica lo `.zip` con i file `.tex` + `images/` |
| `/job/{job_id}` | DELETE | Elimina uploads temporanei. Aggiungere `?full=true` per eliminare anche zip e output definitivi |
| `/jobs` | GET | Lista tutti i job con `created_at`, `has_pdf`, `has_zip` |
| `/health` | GET | Stato dei tool di sistema: `api_key`, `ffmpeg`, `pdflatex`, `whisper` |
| `/settings` | GET | Configurazione corrente: `api_key` (bool), `ttl_days`, `ffmpeg_timeout`, `pipeline_timeout` |
| `/settings` | POST | Salva `api_key`, `ttl_days` (1–365), `ffmpeg_timeout` (300–86400 s), `pipeline_timeout` (300–86400 s) — persiste su `settings.json` |
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
| Slide | `.pptx` | extractor → testo + OMML + immagini + **tabelle**; slide_renderer → PNG per slide |
| Word | `.docx` | python-docx → testo plain |
| PDF | `.pdf` | pdfplumber → testo; pytesseract fallback se 0 testo (PDF scansionato); pdf_renderer → PNG per pagina |
| Testo | `.txt` `.md` `.rtf` | lettura diretta (RTF con strip automatico dei tag) |

Ogni sessione di upload genera esattamente **un** `lezione_NN.tex`, indipendentemente dalla dimensione del PDF. I contenuti grandi vengono gestiti dal budget token (`_trunc()`) prima dell'invio a Claude.

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

L'algoritmo usa il **tempo di discorso effettivo**: le pause tra segmenti consecutivi vengono cappate a 45 secondi. In questo modo una pausa del professore (cambio slide, domande, interruzione) non distorce l'allineamento dei segmenti successivi.

**4. Contesto corso — `corso_context.json`**

Dopo ogni lezione generata, il preprocessor estrae automaticamente dal LaTeX prodotto i concetti chiave (titoli di section/subsection), le definizioni (`\begin{definition}`), i simboli introdotti, e **l'ultimo argomento trattato verbalmente dal professore** (`last_verbal_topic` — ultima `\section` non conclusiva del LaTeX). Questi dati vengono salvati in `output/corso_context.json`.

Dalla lezione successiva in poi, il prompt include:
- Una sezione `## CONTESTO DEL CORSO` con la lista delle lezioni precedenti ("Concetti già introdotti — NON ri-spiegare da zero")
- Un blocco `## RACCORDO CON LEZIONE PRECEDENTE` che indica esattamente dove il professore si è fermato nell'ultima lezione, con istruzione a iniziare da quel punto

Si disabilita con `--no-context`.

**Pruning automatico su corsi lunghi:** le lezioni oltre le ultime 10 vengono compresse (si conservano solo numero, titolo e ultimo argomento, rimuovendo key_concepts/definitions/symbols). I simboli globali sono limitati a 50 voci. Questo evita che `corso_context.json` cresca illimitatamente su corsi da 30+ lezioni.

**Compressione automatica** in base ai token stimati:

| Token stimati | Modalità | Comportamento |
|--------------|----------|--------------|
| < 80.000 | `RAW_CLEAN` | Testo pulito, struttura completa |
| 80.000 – 180.000 | `DENSE` | Rimozione esempi ridondanti e frasi riempitive |
| > 180.000 | `OUTLINE` | Solo struttura gerarchica + prime righe per sezione |

---

## Continuità inter-lezione

La pipeline mantiene automaticamente la continuità tra lezioni consecutive dello stesso corso.

**Come funziona:**
1. Al termine di ogni lezione, `update_course_context()` estrae dal LaTeX generato l'ultimo argomento spiegato verbalmente dal professore (ultima `\section` numerata, escluse sezioni di chiusura come Note, Conclusioni, Bibliografia).
2. Questo valore (`last_verbal_topic`) viene salvato in `corso_context.json`.
3. Alla lezione successiva, `context_to_prompt()` inietta nel prompt un blocco strutturato:

```
## RACCORDO CON LEZIONE PRECEDENTE
Nella Lezione N il professore si è fermato verbalmente su:
  "ultimo argomento..."
REGOLE DI RACCORDO:
• Questa lezione DEVE iniziare esattamente da dove il professore si era fermato
• LIMITE CRITICO: fermati dove si ferma la trascrizione audio — NON anticipare argomenti non spiegati verbalmente
• Non inventare contenuto non presente nella trascrizione
```

4. Il sistema prompt include inoltre due regole esplicite:
   - **Regola 18 — LIMITE ORALE**: la lezione termina dove termina la spiegazione verbale. Argomenti nelle slide non ancora spiegati non vengono inclusi.
   - **Regola 19 — RACCORDO INTER-LEZIONE**: se il contesto corso indica l'ultimo argomento trattato, inizia con un raccordo fluido di 1-2 righe.

Per usare la continuità dal frontend, usa il campo **Continua da job** per collegare la nuova sessione alla precedente.

---

## Struttura output

```
output/
├── main.tex                   # documento principale, include tutti i capitoli
├── lezione_01.tex             # capitolo 1
├── lezione_02.tex             # capitolo 2
├── ...
├── corso_context.json         # memoria del corso (concetti, definizioni, simboli, ultimo argomento)
├── state.json                 # stato pipeline (numero prossima lezione)
└── images/
    ├── lezione_slide_001.png  # screenshot slide 1 ({stem}_slide_NNN.png)
    ├── lezione_slide_002.png  # screenshot slide 2
    ├── dispense_pag_001.png   # screenshot pagina 1 PDF ({stem}_pag_NNN.png)
    ├── dispense_pag_002.png   # screenshot pagina 2 PDF
    ├── slide001_abc123.png    # immagine embedded estratta dal PPTX
    └── formula_def456.png     # immagine formula estratta (→ pix2tex)
```

`main.tex` include un preambolo LaTeX completo con:
- `amsmath`, `amssymb`, `amsthm` — matematica
- `graphicx`, `float` — immagini
- `hyperref` — link navigabili nel PDF
- `fancyhdr` — intestazioni pagina
- `babel` con lingua dinamica (basata su `WHISPER_LANG`)
- Ambienti: `theorem`, `definition`, `example`, `lemma`, `corollary`, `remark`
- `listings` — blocchi codice

---

## Note pratiche

**Velocità Whisper su CPU:**
Il modello `base` su CPU impiega circa 1 minuto ogni 10 minuti di audio. Per test usa `--whisper-model tiny` (4× più veloce). La trascrizione viene salvata in cache `.transcript.txt` — esecuzioni successive saltano Whisper se il file esiste già. Durante la trascrizione la UI aggiorna il progresso ogni 15 secondi stimando la percentuale completata in base alla durata del file (rilevata con ffprobe).

**`--skip-ai` non salta Whisper:**
`--skip-ai` disattiva solo Claude. Whisper gira sempre perché è trascrizione locale. Il contesto corso (`corso_context.json`) non viene aggiornato se Claude non viene chiamato.

**PDF grandi:**
Un PDF da 275 pagine viene processato come un'unica lezione. Il testo viene troncato in base al budget token (`_trunc()`) per rispettare i limiti del contesto Claude. Le immagini PNG delle pagine vengono generate e salvate in `images/` con naming `nomefile_pag_001.png`.

**PDF scansionati e PDF misti:**
La pipeline tenta automaticamente l'OCR tramite pytesseract su ogni pagina che pdfplumber non riesce a leggere. Questo copre sia PDF 100% scansionati (nessuna pagina ha testo) sia PDF misti (alcune pagine hanno testo digitale, altre sono immagini scansionate). L'OCR viene eseguito sui PNG già renderizzati da pdf_renderer. La lingua OCR segue `WHISPER_LANG`; se non impostata usa `ita+eng`. Richiede `pip install pytesseract` + `sudo apt install tesseract-ocr`.

**WHISPER_LANG e lingua del documento:**
Per default Whisper auto-rileva la lingua. Per forzarla: `export WHISPER_LANG=it` (o qualsiasi codice BCP-47). La variabile influenza anche:
- La lingua dell'OCR pytesseract (es. `it` → `ita`)
- La lingua principale del documento LaTeX: `main.tex` genera `\selectlanguage{italian}` (o english/french/german/spanish/portuguese secondo il codice)

**Cache immagini:**
Se i PNG esistono già in `images/`, non vengono rirenderizzati. Per forzare il rirenderizzamento cancella i file PNG dalla cartella.

**TeamsHack — URL manifest:**
Il frontend accetta URL di videomanifest Teams (es. `.m3u8` o URL stream). Incollati nella zona Teams dell'UI, vengono inviati a `server.py` che li scarica via `ffmpeg -i <url> -vn ... .mp3` prima di avviare la pipeline. Non è necessario scaricare manualmente il video. `TeamsHack.py` rimane disponibile anche come script standalone da terminale con modalità video+mp3 e contatori automatici.

**pix2tex e formule:**
Le formule nei file `.pptx` vengono gestite in due modi distinti: quelle create con l'editor equazioni di PowerPoint (OMML) vengono convertite direttamente dall'XML tramite `omml2latex.py` — zero OCR, qualità alta. Le formule inserite come immagini (screenshot, foto di lavagna, PNG incollati) vengono rilevate da `formula_detector.py` in base ad aspect ratio (≥ 0.5, incluse matrici verticali), sfondo chiaro e bassa saturazione, e poi passate a pix2tex. Usa `--skip-ocr` per saltare questo step e velocizzare l'esecuzione. I risultati sono salvati in cache `.ocr_cache.json` accanto all'immagine.

**Tabelle PPTX:**
Le tabelle nelle slide PowerPoint vengono estratte automaticamente da `extractor.py` e convertite in `\begin{tabular}` LaTeX con escape dei caratteri speciali nelle celle. La prima riga è trattata come intestazione (doppio `\hline`). Il LaTeX della tabella viene inviato a Claude nel prompt — Claude può così migliorarne la formattazione in base al contesto della lezione.

**Validazione LaTeX pre-compilazione:**
Prima dei due pass `pdflatex` che generano il PDF finale, il server esegue un pass in modalità `--draftmode` (nessun PDF prodotto, solo analisi degli errori). Questo aggiorna `main.log` prima della compilazione reale: gli errori vengono rilevati e surfacati nell'UI anche se i pass successivi producono un PDF parziale.

**Moduli opzionali:**
Se `extractor.py`, `slide_renderer.py`, `pdf_renderer.py` e gli altri moduli collega non sono presenti, la pipeline usa un fallback base che funziona comunque. La qualità dell'output (immagini slide, formule OMML) è però significativamente migliore con i moduli completi.

**Timeout configurabili:**
I timeout di ffmpeg (download Teams) e della pipeline (Whisper + Claude) sono configurabili via `/settings` POST senza riavviare il server. Default: `ffmpeg_timeout=7200s` (2h), `pipeline_timeout=3600s` (1h). Utile per video molto lunghi o reti lente.

**Prompt caching Anthropic:**
Il system prompt inviato a Claude (19 regole LaTeX, ~600 token) è identico per ogni lezione e viene marcato con `cache_control: ephemeral`. Dalla seconda lezione in poi Anthropic lo restituisce dalla cache al ~10% del costo normale. Il log mostra `cache=hit (r:625 w:0)` o `cache=miss` per ogni chiamata.

**Debug prompt e upload:**
Ogni job scrive nella cartella `output/{job_id}/{output_name}/debug/`:
- `prompt_lezione_NN.txt` — prompt completo inviato a Claude (testo + slide allineate)
- `riepilogo_lezione_NN.txt` — classificazione sorgenti (SCHELETRO/CARNE/SUPPORTO/CONTORNO) con nomi file e char count
- `uploads/` — copia dei file caricati, disponibile anche se la pipeline fallisce

Utile per verificare cosa riceve Claude, stimare i token e riprodurre il job.

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
| `ModuleNotFoundError: anthropic` | anthropic SDK non installato | `pip install anthropic` con il venv attivo |
| `images/` vuota | pymupdf non trovato al momento dell'esecuzione | Verifica `python -c "import fitz"` nel venv attivo |
| Più di un `lezione_NN.tex` generato | Modalità batch attiva (`--batch`) | In modalità normale ogni sessione produce sempre un unico file |
| PDF scansionato senza testo | pdfplumber estrae 0 testo | Installa pytesseract: `pip install pytesseract && sudo apt install tesseract-ocr tesseract-ocr-ita` |
| `ffprobe: command not found` | ffprobe non installato | `sudo apt install ffmpeg` (include ffprobe) |
| Claude non risponde | API key mancante o errata | `echo $ANTHROPIC_API_KEY` per verificare |
| Frontend non raggiungibile da remoto | Server non in ascolto su `0.0.0.0` | Avvia con `--host 0.0.0.0`; da remoto usa l'IP del server (o Tailscale IP) |
| `pdflatex` fallisce | pdflatex non installato o pacchetti mancanti | `sudo apt install texlive-latex-base texlive-latex-recommended texlive-latex-extra texlive-lang-italian texlive-fonts-recommended` (oppure `texlive-full` per installazione completa) |
| pix2tex non trovato (`['heuristic']` only) | Venv non nei path cercati | Usa `~/pix2tex_venv` oppure `~/Scrivania/venv` (italiano) o `~/venv` |
| `NNPACK: Unsupported hardware` in stderr | CPU senza istruzioni NNPACK | Warning innocuo — pix2tex funziona ugualmente su CPU normale |
| pix2tex lento (30-60s per formula) | Modello ML su CPU, nessuna GPU | Normale su CPU; la cache `.ocr_cache.json` evita di riprocessare le stesse immagini |
| Prima esecuzione pix2tex scarica pesi | Download automatico ~116MB | Attendi il download; successive esecuzioni usano la cache |
| Raccordo inter-lezione non attivo | Lezione precedente senza contesto | Assicurati di usare il campo "Continua da job" o che `corso_context.json` esista nell'output |
| `RuntimeError: "slow_conv2d_cpu" not implemented for 'Half'` o `fp16 is not supported on CPU` | Whisper prova fp16 su CPU senza CUDA | Aggiorna il codice — il fix è già incluso (imposta `fp16=False` automaticamente su CPU) |
| PDF non compilato — badge rosso in UI | Errori LaTeX nel sorgente generato | Clicca sul badge "✗ PDF non compilato" per vedere gli errori estratti da `main.log`; scarica lo ZIP per il log completo |
| Tabelle nelle slide non compaiono nel LaTeX | python-pptx non trova `shape.table` | Verifica che la tabella sia un oggetto tabella nativo PPTX (non uno screenshot); le tabelle embedded come immagini non sono estraibili |
| `corso_context.json` molto grande su corsi lunghi | Accumulo dati lezione per lezione | Il pruning automatico mantiene solo le ultime 10 lezioni complete; le più vecchie conservano solo titolo e ultimo argomento |
| Pipeline killata dopo 1 ora su video molto lunghi | `pipeline_timeout` default 3600s | Aumenta il timeout via `/settings` POST: `{"pipeline_timeout": 7200}` |
| Download Teams fallisce su video >2h | `ffmpeg_timeout` default 7200s | Aumenta il timeout via `/settings` POST: `{"ffmpeg_timeout": 14400}` |
| Allineamento slide↔audio spostato dopo una pausa | Pause lunghe distorcevano la distribuzione lineare | Fix già applicato: le pause >45s vengono cappate nel calcolo del tempo di discorso |
| TTL output non cambia dopo modifica in UI | Il campo è nelle opzioni avanzate | Espandi "▾ opzioni avanzate" e modifica il campo "Retention output" — il salvataggio è automatico con feedback "✓ salvato" |
