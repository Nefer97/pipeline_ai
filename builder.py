"""
builder.py
Genera il file .tex finale a partire dai dati estratti.
"""

import re

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
    return _PATTERN.sub(lambda m: _ESCAPE_MAP[m.group()], text)


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
