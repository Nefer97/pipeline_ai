# pptx2latex

Converte file `.pptx` in notebook LaTeX strutturato.

## Pipeline

```
.pptx
  │
  ├─ Testo             → \section, paragrafi, itemize
  ├─ Formule OMML      → \begin{equation} (estrazione diretta XML)
  └─ Immagini PNG/JPG
       ├─ Formula?  ──→ pix2tex → \begin{equation}
       └─ No        ──→ \includegraphics
```

## Installazione dipendenze

```bash
# Dipendenze di sistema (fuori dal venv pix2tex)
pip install python-pptx Pillow numpy lxml

# pix2tex è già nel tuo venv: ~/Scrivania/venv
```

## Uso

```bash
# Base
python main.py mia_presentazione.pptx

# Con titolo personalizzato
python main.py mia_presentazione.pptx --title "Elettronica Applicata - PLL"

# Output personalizzato
python main.py mia_presentazione.pptx --output mia_cartella/note.tex

# Solo estrazione, senza OCR (veloce, per test)
python main.py mia_presentazione.pptx --skip-ocr
```

## Compilazione LaTeX

```bash
cd output
pdflatex output.tex
pdflatex output.tex   # seconda volta per TOC
```

## Struttura output

```
output/
├── output.tex
└── images/
    ├── slide001_a1b2c3d4.png
    ├── slide002_e5f6g7h8.png
    └── ...
```

## Note

- Le formule OMML (native PowerPoint) vengono convertite direttamente dall'XML.
  La qualità dipende dalla complessità della formula.
- pix2tex su CPU impiega ~5-15 secondi per immagine.
- Il rilevamento automatico delle formule usa euristiche su: aspect ratio,
  sfondo chiaro, bassa saturazione colore. Può avere falsi positivi/negativi
  su schemi a blocchi in B/N.
