"""
ocr_math.py
Chiama pix2tex (nel venv ~/Scrivania/venv) per convertire immagini di formule in LaTeX.
"""

import subprocess
import tempfile
import os
import sys


VENV_PYTHON = os.path.expanduser('~/Scrivania/venv/bin/python')


def image_to_latex(image_path: str) -> str:
    """
    Usa pix2tex per convertire un'immagine di formula in LaTeX.
    Ritorna stringa LaTeX o stringa vuota in caso di errore.
    """
    script = f"""
import warnings
warnings.filterwarnings('ignore')
import os
os.environ['NO_ALBUMENTATIONS_UPDATE'] = '1'
from pix2tex.cli import LatexOCR
model = LatexOCR()
from PIL import Image
img = Image.open({repr(image_path)})
result = model(img)
print(result)
"""
    try:
        result = subprocess.run(
            [VENV_PYTHON, '-c', script],
            capture_output=True,
            text=True,
            timeout=120
        )
        if result.returncode == 0:
            latex = result.stdout.strip()
            # Rimuovi eventuali warning che finiscono nello stdout
            lines = [l for l in latex.split('\n') if not l.startswith('[') and 'Warning' not in l and 'UserWarning' not in l]
            return '\n'.join(lines).strip()
        else:
            print(f"  [pix2tex ERROR] {result.stderr[-300:]}")
            return ''
    except subprocess.TimeoutExpired:
        print(f"  [pix2tex TIMEOUT] {image_path}")
        return ''
    except Exception as e:
        print(f"  [pix2tex EXCEPTION] {e}")
        return ''
