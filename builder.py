"""
builder.py
Genera il file .tex finale a partire dai dati estratti.
"""

LATEX_HEADER = r"""\documentclass[12pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[italian]{babel}
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


def _escape_latex(text: str) -> str:
    """Escapa caratteri speciali LaTeX nel testo."""
    replacements = [
        ('\\', r'\textbackslash{}'),
        ('&', r'\&'),
        ('%', r'\%'),
        ('$', r'\$'),
        ('#', r'\#'),
        ('^', r'\^{}'),
        ('_', r'\_'),
        ('{', r'\{'),
        ('}', r'\}'),
        ('~', r'\textasciitilde{}'),
        ('<', r'\textless{}'),
        ('>', r'\textgreater{}'),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


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


def build_latex(slides: list, output_path: str, title: str = "Note del Corso", images_rel_path: str = "images"):
    """
    Genera il file .tex finale.
    slides: lista di SlideData con obj.latex_result popolato
    images_rel_path: percorso relativo alla cartella immagini (usato in \\graphicspath)
    """
    header = LATEX_HEADER.replace(
        r"\graphicspath{{./images/}}",
        f"\\graphicspath{{{{{images_rel_path}/}}}}"
    )
    lines = []
    lines.append(header)

    # Title page
    lines.append(r'\begin{titlepage}')
    lines.append(r'\centering')
    lines.append(r'\vspace*{2cm}')
    lines.append(r'{\Huge\bfseries ' + _escape_latex(title) + r'\\[0.5cm]}')
    lines.append(r'\vspace{1cm}')
    lines.append(r'{\large Generato automaticamente da pptx2latex}\\')
    lines.append(r'\vspace{0.5cm}')
    lines.append(r'\today')
    lines.append(r'\end{titlepage}')
    lines.append('')
    lines.append(r'\tableofcontents')
    lines.append(r'\newpage')
    lines.append('')

    for slide in slides:
        # Section con titolo slide
        sec_title = (slide.title or '').strip() or f"Slide {slide.slide_number}"
        lines.append(f'\\section{{{_escape_latex(sec_title)}}}')
        lines.append('')

        for obj in slide.objects:
            if obj.obj_type == 'text':
                # Salta testo uguale al titolo
                if (obj.content or '').strip() == (slide.title or '').strip():
                    continue
                lines.append(_format_text_block(obj.content))
                lines.append('')

            elif obj.obj_type == 'omml_formula':
                latex_formula = getattr(obj, 'latex_result', None)
                if latex_formula and not latex_formula.startswith('%'):
                    lines.append(r'\begin{equation}')
                    lines.append(latex_formula)
                    lines.append(r'\end{equation}')
                else:
                    # Fallback: mostra testo estratto come commento
                    lines.append(r'% Formula OMML (conversione parziale):')
                    if latex_formula:
                        lines.append(r'\begin{equation}')
                        lines.append(r'% ' + latex_formula.replace('\n', '\n% '))
                        lines.append(r'\end{equation}')
                lines.append('')

            elif obj.obj_type == 'image':
                latex_formula = getattr(obj, 'latex_result', None)
                img_filename = obj.content or ''

                if latex_formula and latex_formula.strip():
                    # È una formula riconosciuta da pix2tex
                    lines.append(r'% Formula da immagine (pix2tex):')
                    lines.append(r'\begin{equation}')
                    lines.append(latex_formula)
                    lines.append(r'\end{equation}')
                else:
                    # Immagine normale → includegraphics
                    lines.append(r'\begin{figure}[H]')
                    lines.append(r'\centering')
                    lines.append(f'\\includegraphics[width=0.8\\textwidth]{{{img_filename}}}')
                    lines.append(r'\end{figure}')
                lines.append('')

    lines.append(LATEX_FOOTER)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"[OK] File LaTeX generato: {output_path}")
