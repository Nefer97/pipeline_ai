"""
builder.py
Genera il file .tex finale a partire dai dati estratti.
"""

import re

# Mappa unicode → LaTeX compilata a livello modulo (single-pass, O(n) per chiamata)
_UNICODE_MAP: dict[str, str] = {
    # Subscript digits
    '₀': '$_{0}$', '₁': '$_{1}$', '₂': '$_{2}$', '₃': '$_{3}$',
    '₄': '$_{4}$', '₅': '$_{5}$', '₆': '$_{6}$', '₇': '$_{7}$',
    '₈': '$_{8}$', '₉': '$_{9}$',
    # Superscript digits
    '⁰': '$^{0}$', '¹': '$^{1}$', '²': '$^{2}$', '³': '$^{3}$',
    '⁴': '$^{4}$', '⁵': '$^{5}$', '⁶': '$^{6}$', '⁷': '$^{7}$',
    '⁸': '$^{8}$', '⁹': '$^{9}$',
    # Operatori e frecce
    '→': r'$\rightarrow$', '←': r'$\leftarrow$',
    '↑': r'$\uparrow$',   '↓': r'$\downarrow$',
    '⇒': r'$\Rightarrow$','⇐': r'$\Leftarrow$',
    '≈': r'$\approx$',    '≠': r'$\neq$',
    '≤': r'$\leq$',       '≥': r'$\geq$',
    '±': r'$\pm$',        '∓': r'$\mp$',
    '×': r'$\times$',     '÷': r'$\div$',
    '∞': r'$\infty$',     '∅': r'$\emptyset$',
    '∈': r'$\in$',        '∉': r'$\notin$',
    '∩': r'$\cap$',       '∪': r'$\cup$',
    '∑': r'$\sum$',       '∏': r'$\prod$',
    '∫': r'$\int$',       '∂': r'$\partial$',
    '√': r'$\sqrt{}$',    '∝': r'$\propto$',
    # Simboli testo
    '°': r'\textdegree{}',
    '·': r'$\cdot$',
    '–': '--', '—': '---',
    '\u00a0': '~',
    '…': r'\ldots{}',
    '©': r'\textcopyright{}',
    '®': r'\textregistered{}',
    '™': r'\texttrademark{}',
    '€': r'\texteuro{}',
    '£': r'\pounds{}',
    # Lettere greche nel testo
    'α': r'$\alpha$',   'β': r'$\beta$',    'γ': r'$\gamma$',
    'δ': r'$\delta$',   'ε': r'$\varepsilon$','ζ': r'$\zeta$',
    'η': r'$\eta$',     'θ': r'$\theta$',   'λ': r'$\lambda$',
    'μ': r'$\mu$',      'ν': r'$\nu$',      'ξ': r'$\xi$',
    'π': r'$\pi$',      'ρ': r'$\rho$',     'σ': r'$\sigma$',
    'τ': r'$\tau$',     'φ': r'$\varphi$',  'χ': r'$\chi$',
    'ψ': r'$\psi$',     'ω': r'$\omega$',
    'Γ': r'$\Gamma$',   'Δ': r'$\Delta$',   'Θ': r'$\Theta$',
    'Λ': r'$\Lambda$',  'Π': r'$\Pi$',      'Σ': r'$\Sigma$',
    'Φ': r'$\Phi$',     'Ψ': r'$\Psi$',     'Ω': r'$\Omega$',
}
_UNICODE_RE = re.compile('[' + re.escape(''.join(_UNICODE_MAP.keys())) + ']')

_LATEX_HEADER_TMPL = r"""\documentclass[12pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{textcomp}
\usepackage[{babel_lang}]{babel}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{amsfonts}
\usepackage{graphicx}
\usepackage{float}
\usepackage{geometry}
\usepackage{hyperref}
\usepackage{xcolor}
\usepackage{booktabs}
\usepackage{enumitem}
\usepackage{fancyhdr}

\geometry{margin=2.5cm}
\pagestyle{fancy}
\fancyhf{}
\rhead{\thepage}
\lhead{\leftmark}

\graphicspath{{./images/}}

\begin{document}

"""

LATEX_FOOTER = r"""
\end{document}
"""

# Mappa codici Whisper/BCP-47 → nomi babel
_WHISPER_TO_BABEL: dict[str, str] = {
    "it": "italian",   "en": "english",   "fr": "french",
    "de": "ngerman",   "es": "spanish",   "pt": "portuguese",
    "nl": "dutch",     "ru": "russian",   "pl": "polish",
    "cs": "czech",     "el": "greek",     "zh": "chinese",
    "ja": "japanese",  "ar": "arabic",
}


def _make_header(lang: str = "italian") -> str:
    """Restituisce LATEX_HEADER con la lingua babel corretta."""
    babel_lang = _WHISPER_TO_BABEL.get(lang, lang) if len(lang) <= 3 else lang
    return _LATEX_HEADER_TMPL.replace("{babel_lang}", babel_lang)


# Header default per compatibilità backward
LATEX_HEADER: str = _make_header("italian")


def _escape_latex(text: str) -> str:
    """Escapa caratteri speciali LaTeX nel testo (single-pass, no cascading)."""
    # Step 1: escape caratteri speciali LaTeX nel testo originale
    _ESCAPE_MAP = {
        '\\': r'\textbackslash{}',
        '&':  r'\&',
        '%':  r'\%',
        '$':  r'\$',
        '#':  r'\#',
        '^':  r'\^{}',
        '_':  r'\_',
        '{':  r'\{',
        '}':  r'\}',
        '~':  r'\textasciitilde{}',
        '<':  r'\textless{}',
        '>':  r'\textgreater{}',
    }
    _PATTERN = re.compile(r'[\\&%$#^_{}~<>]')
    text = _PATTERN.sub(lambda m: _ESCAPE_MAP[m.group()], text)

    # Step 2: converti unicode → LaTeX via regex compilata (single-pass, O(n))
    # Eseguito dopo l'escape così i comandi inseriti ($, {}, \) non vengono ri-escapati
    return _UNICODE_RE.sub(lambda m: _UNICODE_MAP[m.group()], text)


def _format_text_block(text: str) -> str:
    """Formatta un blocco di testo in LaTeX."""
    lines = text.strip().split('\n')
    result = []
    in_itemize = False  # flag esplicito — evita doppio \begin{itemize} dopo riga vuota
    for line in lines:
        line = line.strip()
        if not line:
            if in_itemize:
                result.append(r'\end{itemize}')
                result.append('')
                in_itemize = False
            else:
                result.append('')
            continue
        # Bullet point
        if line.startswith(('•', '-', '*', '–')):
            if not in_itemize:
                result.append(r'\begin{itemize}')
                in_itemize = True
            result.append(r'  \item ' + _escape_latex(line.lstrip('•-*– ').strip()))
        else:
            if in_itemize:
                result.append(r'\end{itemize}')
                result.append('')
                in_itemize = False
            result.append(_escape_latex(line))
    # Chiudi itemize finale se aperto
    if in_itemize:
        result.append(r'\end{itemize}')
    return '\n'.join(result)
