"""
extractor.py
Estrae testo, immagini e formule OMML da un file .pptx
"""

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional
from pptx import Presentation
from pptx.util import Emu
from lxml import etree
import hashlib

# Formati non supportati da LaTeX/Pillow → da convertire in PNG
UNSUPPORTED_FORMATS = {'.wmf', '.emf', '.gif', '.bmp', '.tiff', '.tif'}


def _convert_to_png(src_path: str) -> str:
    """
    Converte un'immagine in PNG.
    wmf/emf: usa LibreOffice o Inkscape.
    gif/bmp/tiff: usa Pillow.
    Ritorna il path del PNG, o src_path originale se la conversione fallisce.
    """
    ext = os.path.splitext(src_path)[1].lower()
    png_path = src_path.rsplit('.', 1)[0] + '.png'

    if ext in ('.wmf', '.emf'):
        # Prova LibreOffice
        try:
            result = subprocess.run(
                ['libreoffice', '--headless', '--convert-to', 'png',
                 '--outdir', os.path.dirname(src_path), src_path],
                capture_output=True, timeout=30
            )
            if result.returncode == 0 and os.path.exists(png_path):
                try:
                    os.remove(src_path)
                except OSError:
                    pass  # file in uso o già rimosso — non critico
                return png_path
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        # Prova Inkscape
        try:
            result = subprocess.run(
                ['inkscape', '--export-type=png', f'--export-filename={png_path}', src_path],
                capture_output=True, timeout=30
            )
            if result.returncode == 0 and os.path.exists(png_path):
                try:
                    os.remove(src_path)
                except OSError:
                    pass
                return png_path
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        print(f"  [WARN] {os.path.basename(src_path)}: wmf/emf non convertibile (installa LibreOffice o Inkscape)")
        return src_path
    else:
        # gif, bmp, tiff → Pillow
        try:
            from PIL import Image
            img = Image.open(src_path)
            img.convert('RGBA').save(png_path, 'PNG')
            try:
                os.remove(src_path)
            except OSError:
                pass
            return png_path
        except Exception as e:
            print(f"  [WARN] Conversione {os.path.basename(src_path)} fallita: {e}")
            return src_path


# Namespace XML usati in pptx
NSMAP = {
    'a':   'http://schemas.openxmlformats.org/drawingml/2006/main',
    'p':   'http://schemas.openxmlformats.org/presentationml/2006/main',
    'r':   'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'm':   'http://schemas.openxmlformats.org/officeDocument/2006/math',
    'pic': 'http://schemas.openxmlformats.org/drawingml/2006/picture',
}


@dataclass
class SlideObject:
    """Un oggetto estratto da una slide, con posizione."""
    obj_type: str          # 'text', 'image', 'omml_formula'
    content: str           # testo o path immagine o OMML xml string
    top: float             # coordinata Y in EMU
    left: float            # coordinata X in EMU
    width: float = 0.0
    height: float = 0.0
    image_path: Optional[str] = None  # solo per immagini


@dataclass
class SlideData:
    slide_number: int
    title: str
    objects: list = field(default_factory=list)  # lista di SlideObject ordinati per Y


def _emu_to_pt(emu: int) -> float:
    return emu / 12700.0


def _get_position(shape):
    """Restituisce (top, left, width, height) in EMU."""
    try:
        return shape.top or 0, shape.left or 0, shape.width or 0, shape.height or 0
    except Exception:
        return 0, 0, 0, 0


def _extract_omml(shape_xml: etree._Element) -> Optional[str]:
    """Cerca formula OMML (m:oMath) nell'elemento XML della shape."""
    math_elems = shape_xml.findall('.//' + '{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath')
    if math_elems:
        return etree.tostring(math_elems[0], encoding='unicode')
    return None


def _extract_text(shape) -> str:
    """Estrae testo pulito da una shape testuale."""
    try:
        if shape.has_text_frame:
            lines = []
            for para in shape.text_frame.paragraphs:
                line = ''.join(run.text for run in para.runs)
                if line.strip():
                    lines.append(line)
            return '\n'.join(lines)
    except Exception:
        pass
    return ''


def extract_slides(pptx_path: str, image_output_dir: str) -> list:
    """
    Estrae tutti gli oggetti da ogni slide.
    Ritorna lista di SlideData.
    """
    os.makedirs(image_output_dir, exist_ok=True)
    prs = Presentation(pptx_path)
    slides_data = []

    for slide_idx, slide in enumerate(prs.slides, start=1):
        # Titolo
        title = ''
        try:
            title = slide.shapes.title.text if slide.shapes.title else ''
        except Exception:
            pass

        objects = []

        for shape in slide.shapes:
            top, left, width, height = _get_position(shape)

            # --- Controlla prima se è formula OMML ---
            omml = _extract_omml(shape._element)
            if omml:
                objects.append(SlideObject(
                    obj_type='omml_formula',
                    content=omml,
                    top=top, left=left, width=width, height=height
                ))
                continue

            # --- Immagine ---
            if shape.shape_type == 13:  # MSO_SHAPE_TYPE.PICTURE
                try:
                    image = shape.image
                    ext = image.ext  # png, jpg, ...
                    # Nome file basato su hash per evitare duplicati
                    img_hash = hashlib.md5(image.blob).hexdigest()[:8]
                    img_filename = f"slide{slide_idx:03d}_{img_hash}.{ext}"
                    img_path = os.path.join(image_output_dir, img_filename)
                    with open(img_path, 'wb') as f:
                        f.write(image.blob)
                    # Converti formati non supportati da LaTeX
                    if ext.lower() in UNSUPPORTED_FORMATS:
                        img_path = _convert_to_png(img_path)
                        img_filename = os.path.basename(img_path)
                    objects.append(SlideObject(
                        obj_type='image',
                        content=img_filename,
                        top=top, left=left, width=width, height=height,
                        image_path=img_path
                    ))
                except Exception as e:
                    print(f"  [WARN] Immagine non estratta slide {slide_idx}: {e}")
                continue

            # --- Testo ---
            text = _extract_text(shape)
            if text.strip():
                objects.append(SlideObject(
                    obj_type='text',
                    content=text,
                    top=top, left=left, width=width, height=height
                ))

        # Ordina per posizione verticale (Y crescente)
        objects.sort(key=lambda o: o.top)

        slides_data.append(SlideData(
            slide_number=slide_idx,
            title=title,
            objects=objects
        ))

    return slides_data
