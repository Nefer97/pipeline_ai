"""
formula_detector.py
Euristica per decidere se un'immagine PNG è probabilmente una formula matematica.
"""

import os
from PIL import Image
import numpy as np


def is_formula_image(image_path: str) -> bool:
    # Skippa formati non supportati da Pillow
    ext = os.path.splitext(image_path)[1].lower()
    if ext in ('.wmf', '.emf', '.svg'):
        return False
    """
    Restituisce True se l'immagine ha caratteristiche tipiche di una formula:
    - Sfondo chiaro (bianco/quasi bianco)
    - Pochi colori (quasi monocromatica)
    - Aspect ratio orizzontale (più larga che alta)
    - Non troppo grande (non è un grafico a piena pagina)
    """
    try:
        img = Image.open(image_path).convert('RGB')
        arr = np.array(img)

        h, w = arr.shape[:2]

        # --- Criterio 1: aspect ratio orizzontale ---
        # Le formule tendono ad essere più larghe che alte
        aspect = w / h if h > 0 else 0
        if aspect < 1.2:
            return False

        # --- Criterio 2: non troppo grande (non è un grafico full-slide) ---
        # Se l'immagine è molto grande probabilmente è uno schema/grafico
        if w > 2000 and h > 800:
            return False

        # --- Criterio 3: sfondo prevalentemente chiaro ---
        # Calcola percentuale di pixel chiari (R,G,B > 200)
        light_pixels = np.all(arr > 200, axis=2)
        light_ratio = light_pixels.sum() / (h * w)
        if light_ratio < 0.5:
            return False

        # --- Criterio 4: pochi colori unici (formula è B/N) ---
        # Riduco a palette piccola e conto colori distinti
        img_small = img.resize((64, 64))
        arr_small = np.array(img_small)
        # Pixel scuri (probabilmente inchiostro/testo formula)
        dark_pixels = np.all(arr_small < 100, axis=2)
        dark_ratio = dark_pixels.sum() / (64 * 64)

        # Deve avere abbastanza pixel scuri (c'è testo/simboli)
        if dark_ratio < 0.02:
            return False

        # Saturazione bassa = quasi monocromatica
        img_hsv = img_small.convert('HSV') if hasattr(img_small, 'convert') else None
        # Uso approccio numpy per saturazione
        r, g, b = arr_small[:,:,0].astype(float), arr_small[:,:,1].astype(float), arr_small[:,:,2].astype(float)
        max_c = np.maximum(np.maximum(r, g), b)
        min_c = np.minimum(np.minimum(r, g), b)
        with np.errstate(invalid='ignore', divide='ignore'):
            sat = np.where(max_c > 0, (max_c - min_c) / np.where(max_c > 0, max_c, 1), 0)
        mean_sat = sat.mean()

        # Formula tipicamente ha saturazione bassa (è in B/N)
        if mean_sat > 0.25:
            return False

        return True

    except Exception as e:
        print(f"  [WARN] formula_detector errore su {image_path}: {e}")
        return False
