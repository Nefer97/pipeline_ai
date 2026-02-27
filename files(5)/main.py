#!/usr/bin/env python3
"""
main.py
Entry point della pipeline pptx2latex.

Uso:
    python main.py <file.pptx> [--output output.tex] [--title "Titolo"]

Dipendenze (sistema):
    pip install python-pptx Pillow numpy lxml

Dipendenze (venv ~/Scrivania/venv):
    pix2tex (già installato)
"""

import argparse
import os
import sys
import time

from extractor import extract_slides
from formula_detector import is_formula_image
from ocr_math import image_to_latex
from omml2latex import omml_to_latex
from builder import build_latex


def main():
    parser = argparse.ArgumentParser(description='Converti .pptx in notebook LaTeX')
    parser.add_argument('pptx', help='File .pptx di input')
    parser.add_argument('--output', default='output/output.tex', help='File .tex di output')
    parser.add_argument('--title', default='Note del Corso', help='Titolo del documento')
    parser.add_argument('--skip-ocr', action='store_true', help='Salta pix2tex (solo estrazione)')
    args = parser.parse_args()

    if not os.path.exists(args.pptx):
        print(f"[ERRORE] File non trovato: {args.pptx}")
        sys.exit(1)

    # Directory di output
    output_dir = os.path.dirname(args.output) or '.'
    images_dir = os.path.join(output_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)

    print(f"\n{'='*50}")
    print(f"  pptx2latex")
    print(f"  Input:  {args.pptx}")
    print(f"  Output: {args.output}")
    print(f"{'='*50}\n")

    # --- FASE 1: Estrazione ---
    print("[1/4] Estrazione contenuti dal .pptx...")
    slides = extract_slides(args.pptx, images_dir)
    total_objects = sum(len(s.objects) for s in slides)
    print(f"      {len(slides)} slide, {total_objects} oggetti estratti")

    # Contatori per report finale
    n_text = n_image = n_omml = n_formula_ocr = 0

    # --- FASE 2: Conversione OMML → LaTeX ---
    print("\n[2/4] Conversione formule OMML...")
    for slide in slides:
        for obj in slide.objects:
            if obj.obj_type == 'omml_formula':
                n_omml += 1
                obj.latex_result = omml_to_latex(obj.content)
                print(f"      Slide {slide.slide_number}: OMML → LaTeX")

    # --- FASE 3: OCR formule PNG ---
    if not args.skip_ocr:
        # Conta candidati
        candidates = []
        for slide in slides:
            for obj in slide.objects:
                if obj.obj_type == 'image' and obj.image_path:
                    if is_formula_image(obj.image_path):
                        candidates.append((slide, obj))

        print(f"\n[3/4] OCR formule PNG ({len(candidates)} immagini candidate)...")

        for i, (slide, obj) in enumerate(candidates, 1):
            print(f"      [{i}/{len(candidates)}] Slide {slide.slide_number}: {obj.content} ...", end='', flush=True)
            t0 = time.time()
            latex = image_to_latex(obj.image_path)
            elapsed = time.time() - t0
            if latex:
                obj.latex_result = latex
                n_formula_ocr += 1
                print(f" ✓ ({elapsed:.1f}s)")
            else:
                obj.latex_result = ''
                print(f" ✗ non riconosciuta ({elapsed:.1f}s)")
    else:
        print("\n[3/4] OCR saltato (--skip-ocr)")

    # Contatori finali
    for slide in slides:
        for obj in slide.objects:
            if obj.obj_type == 'text':
                n_text += 1
            elif obj.obj_type == 'image':
                n_image += 1

    # --- FASE 4: Generazione .tex ---
    print(f"\n[4/4] Generazione file LaTeX...")
    build_latex(slides, args.output, title=args.title)

    # --- Report ---
    print(f"\n{'='*50}")
    print(f"  COMPLETATO")
    print(f"  Blocchi testo:        {n_text}")
    print(f"  Immagini:             {n_image}")
    print(f"  Formule OMML:         {n_omml}")
    print(f"  Formule OCR (pix2tex): {n_formula_ocr}")
    print(f"  Output: {args.output}")
    print(f"{'='*50}\n")
    print("Per compilare:")
    print(f"  cd {output_dir} && pdflatex output.tex\n")


if __name__ == '__main__':
    main()
