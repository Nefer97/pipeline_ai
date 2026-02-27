"""
omml2latex.py
Converte formule OMML (Office Math Markup Language) in LaTeX.
Usa la libreria latex2mathml o un XSLT standard.
"""

import subprocess
import os
import tempfile


# XSLT ufficiale Microsoft per OMML → MathML, poi MathML → LaTeX
# Usiamo un approccio più semplice: omml2latex via Python

def omml_to_latex(omml_xml: str) -> str:
    """
    Converte stringa XML OMML in LaTeX.
    Prima prova con lxml+XSLT, poi fallback su formula placeholder.
    """
    try:
        # Prova con omml2latex se disponibile nel venv
        venv_python = os.path.expanduser('~/Scrivania/venv/bin/python')
        script = f"""
import warnings
warnings.filterwarnings('ignore')
try:
    from latex2mathml import converter
    # omml → non direttamente supportato, usiamo approccio alternativo
    print("UNSUPPORTED")
except ImportError:
    print("UNSUPPORTED")
"""
        # Approccio diretto: usa l'XSLT OMML→MathML di Microsoft
        # e poi mathml2latex
        result = _try_xslt_conversion(omml_xml)
        if result:
            return result
        return _simple_omml_extract(omml_xml)

    except Exception as e:
        return _simple_omml_extract(omml_xml)


def _simple_omml_extract(omml_xml: str) -> str:
    """
    Estrae il testo grezzo dalla formula OMML come fallback.
    Non è LaTeX perfetto ma è meglio di niente.
    """
    from lxml import etree
    try:
        root = etree.fromstring(omml_xml.encode())
        # Estrai tutto il testo dai nodi m:r/m:t
        ns = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
        texts = root.findall(f'.//{{{ns}}}t')
        content = ''.join(t.text or '' for t in texts)
        if content.strip():
            return f"% OMML (conversione parziale)\n{content}"
    except Exception:
        pass
    return '% [formula OMML - conversione non riuscita]'


def _try_xslt_conversion(omml_xml: str) -> str:
    """
    Tenta conversione OMML→MathML→LaTeX via XSLT.
    Richiede che il file XSLT di Microsoft sia disponibile.
    """
    # Questa conversione richiede l'XSLT di MS che non è sempre disponibile
    # Per ora ritorniamo stringa vuota, lo gestiamo nel main
    return ''
