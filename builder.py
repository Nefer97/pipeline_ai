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
    '−': '$-$',           '∗': r'$\ast$',
    '≡': r'$\equiv$',     '≪': r'$\ll$',        '≫': r'$\gg$',
    '⊂': r'$\subset$',    '⊃': r'$\supset$',
    '⊆': r'$\subseteq$',  '⊇': r'$\supseteq$',
    '⊕': r'$\oplus$',     '⊗': r'$\otimes$',
    '∧': r'$\wedge$',     '∨': r'$\vee$',       '¬': r'$\neg$',
    '∀': r'$\forall$',    '∃': r'$\exists$',
    '∇': r'$\nabla$',     '△': r'$\triangle$',
    # Mathematical Italic Capital A–Z (U+1D434–U+1D44D)
    '𝐴': '$A$', '𝐵': '$B$', '𝐶': '$C$', '𝐷': '$D$', '𝐸': '$E$',
    '𝐹': '$F$', '𝐺': '$G$', '𝐻': '$H$', '𝐼': '$I$', '𝐽': '$J$',
    '𝐾': '$K$', '𝐿': '$L$', '𝑀': '$M$', '𝑁': '$N$', '𝑂': '$O$',
    '𝑃': '$P$', '𝑄': '$Q$', '𝑅': '$R$', '𝑆': '$S$', '𝑇': '$T$',
    '𝑈': '$U$', '𝑉': '$V$', '𝑊': '$W$', '𝑋': '$X$', '𝑌': '$Y$',
    '𝑍': '$Z$',
    # Mathematical Italic Small a–z (U+1D44E–U+1D467, h=U+210E)
    '𝑎': '$a$', '𝑏': '$b$', '𝑐': '$c$', '𝑑': '$d$', '𝑒': '$e$',
    '𝑓': '$f$', '𝑔': '$g$', 'ℎ': '$h$', '𝑖': '$i$', '𝑗': '$j$',
    '𝑘': '$k$', '𝑙': '$l$', '𝑚': '$m$', '𝑛': '$n$', '𝑜': '$o$',
    '𝑝': '$p$', '𝑞': '$q$', '𝑟': '$r$', '𝑠': '$s$', '𝑡': '$t$',
    '𝑢': '$u$', '𝑣': '$v$', '𝑤': '$w$', '𝑥': '$x$', '𝑦': '$y$',
    '𝑧': '$z$',
    # Mathematical Bold Italic Capital A–Z (U+1D468–U+1D481)
    '𝑨': r'$\boldsymbol{A}$', '𝑩': r'$\boldsymbol{B}$', '𝑪': r'$\boldsymbol{C}$',
    '𝑫': r'$\boldsymbol{D}$', '𝑬': r'$\boldsymbol{E}$', '𝑭': r'$\boldsymbol{F}$',
    '𝑮': r'$\boldsymbol{G}$', '𝑯': r'$\boldsymbol{H}$', '𝑰': r'$\boldsymbol{I}$',
    '𝑱': r'$\boldsymbol{J}$', '𝑲': r'$\boldsymbol{K}$', '𝑳': r'$\boldsymbol{L}$',
    '𝑴': r'$\boldsymbol{M}$', '𝑵': r'$\boldsymbol{N}$', '𝑶': r'$\boldsymbol{O}$',
    '𝑷': r'$\boldsymbol{P}$', '𝑸': r'$\boldsymbol{Q}$', '𝑹': r'$\boldsymbol{R}$',
    '𝑺': r'$\boldsymbol{S}$', '𝑻': r'$\boldsymbol{T}$', '𝑼': r'$\boldsymbol{U}$',
    '𝑽': r'$\boldsymbol{V}$', '𝑾': r'$\boldsymbol{W}$', '𝑿': r'$\boldsymbol{X}$',
    '𝒀': r'$\boldsymbol{Y}$', '𝒁': r'$\boldsymbol{Z}$',
    # Mathematical Bold Italic Small a–z (U+1D482–U+1D49B)
    '𝒂': r'$\boldsymbol{a}$', '𝒃': r'$\boldsymbol{b}$', '𝒄': r'$\boldsymbol{c}$',
    '𝒅': r'$\boldsymbol{d}$', '𝒆': r'$\boldsymbol{e}$', '𝒇': r'$\boldsymbol{f}$',
    '𝒈': r'$\boldsymbol{g}$', '𝒉': r'$\boldsymbol{h}$', '𝒊': r'$\boldsymbol{i}$',
    '𝒋': r'$\boldsymbol{j}$', '𝒌': r'$\boldsymbol{k}$', '𝒍': r'$\boldsymbol{l}$',
    '𝒎': r'$\boldsymbol{m}$', '𝒏': r'$\boldsymbol{n}$', '𝒐': r'$\boldsymbol{o}$',
    '𝒑': r'$\boldsymbol{p}$', '𝒒': r'$\boldsymbol{q}$', '𝒓': r'$\boldsymbol{r}$',
    '𝒔': r'$\boldsymbol{s}$', '𝒕': r'$\boldsymbol{t}$', '𝒖': r'$\boldsymbol{u}$',
    '𝒗': r'$\boldsymbol{v}$', '𝒘': r'$\boldsymbol{w}$', '𝒙': r'$\boldsymbol{x}$',
    '𝒚': r'$\boldsymbol{y}$', '𝒛': r'$\boldsymbol{z}$',
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

# ── Mappa per sanitizzare OUTPUT formula (pix2tex / OMML) ────────────────
# In math mode i char unicode vanno sostituiti con plain ASCII/LaTeX senza
# wrapper $...$. Usata da sanitize_formula_unicode().
_FORMULA_UNICODE_MAP: dict[str, str] = {
    # Segno meno matematico U+2212 → trattino ASCII (ok in math mode)
    '−': '-',
    # Operatori
    '×': r'\times ',   '÷': r'\div ',
    '±': r'\pm ',      '∓': r'\mp ',
    '≤': r'\leq ',     '≥': r'\geq ',
    '≠': r'\neq ',     '≈': r'\approx ',
    '≡': r'\equiv ',   '≪': r'\ll ',     '≫': r'\gg ',
    '∞': r'\infty ',   '∂': r'\partial ',
    '∑': r'\sum ',     '∏': r'\prod ',   '∫': r'\int ',
    '√': r'\sqrt',     '∝': r'\propto ',
    '→': r'\rightarrow ', '←': r'\leftarrow ',
    '↑': r'\uparrow ', '↓': r'\downarrow ',
    '⇒': r'\Rightarrow ', '⇐': r'\Leftarrow ',
    '∈': r'\in ',      '∉': r'\notin ',
    '∩': r'\cap ',     '∪': r'\cup ',
    '⊂': r'\subset ',  '⊃': r'\supset ',
    '⊆': r'\subseteq ','⊇': r'\supseteq ',
    '⊕': r'\oplus ',   '⊗': r'\otimes ',
    '∧': r'\wedge ',   '∨': r'\vee ',    '¬': r'\neg ',
    '∀': r'\forall ',  '∃': r'\exists ',
    '∇': r'\nabla ',   '·': r'\cdot ',
    # Lettere greche (simboli, non comandi → li riscriviamo come \alpha ecc.)
    'α': r'\alpha ',   'β': r'\beta ',   'γ': r'\gamma ',
    'δ': r'\delta ',   'ε': r'\varepsilon ', 'ζ': r'\zeta ',
    'η': r'\eta ',     'θ': r'\theta ',  'λ': r'\lambda ',
    'μ': r'\mu ',      'ν': r'\nu ',     'ξ': r'\xi ',
    'π': r'\pi ',      'ρ': r'\rho ',    'σ': r'\sigma ',
    'τ': r'\tau ',     'φ': r'\varphi ', 'χ': r'\chi ',
    'ψ': r'\psi ',     'ω': r'\omega ',
    'Γ': r'\Gamma ',   'Δ': r'\Delta ',  'Θ': r'\Theta ',
    'Λ': r'\Lambda ',  'Π': r'\Pi ',     'Σ': r'\Sigma ',
    'Φ': r'\Phi ',     'Ψ': r'\Psi ',    'Ω': r'\Omega ',
    # Mathematical italic capitals U+1D434–U+1D44D → ASCII
    '𝐴': 'A', '𝐵': 'B', '𝐶': 'C', '𝐷': 'D', '𝐸': 'E',
    '𝐹': 'F', '𝐺': 'G', '𝐻': 'H', '𝐼': 'I', '𝐽': 'J',
    '𝐾': 'K', '𝐿': 'L', '𝑀': 'M', '𝑁': 'N', '𝑂': 'O',
    '𝑃': 'P', '𝑄': 'Q', '𝑅': 'R', '𝑆': 'S', '𝑇': 'T',
    '𝑈': 'U', '𝑉': 'V', '𝑊': 'W', '𝑋': 'X', '𝑌': 'Y',
    '𝑍': 'Z',
    # Mathematical italic small a–z U+1D44E–U+1D467 → ASCII
    '𝑎': 'a', '𝑏': 'b', '𝑐': 'c', '𝑑': 'd', '𝑒': 'e',
    '𝑓': 'f', '𝑔': 'g', 'ℎ': 'h', '𝑖': 'i', '𝑗': 'j',
    '𝑘': 'k', '𝑙': 'l', '𝑚': 'm', '𝑛': 'n', '𝑜': 'o',
    '𝑝': 'p', '𝑞': 'q', '𝑟': 'r', '𝑠': 's', '𝑡': 't',
    '𝑢': 'u', '𝑣': 'v', '𝑤': 'w', '𝑥': 'x', '𝑦': 'y',
    '𝑧': 'z',
    # Mathematical bold italic capitals U+1D468–U+1D481
    '𝑨': r'\boldsymbol{A}', '𝑩': r'\boldsymbol{B}', '𝑪': r'\boldsymbol{C}',
    '𝑫': r'\boldsymbol{D}', '𝑬': r'\boldsymbol{E}', '𝑭': r'\boldsymbol{F}',
    '𝑮': r'\boldsymbol{G}', '𝑯': r'\boldsymbol{H}', '𝑰': r'\boldsymbol{I}',
    '𝑱': r'\boldsymbol{J}', '𝑲': r'\boldsymbol{K}', '𝑳': r'\boldsymbol{L}',
    '𝑴': r'\boldsymbol{M}', '𝑵': r'\boldsymbol{N}', '𝑶': r'\boldsymbol{O}',
    '𝑷': r'\boldsymbol{P}', '𝑸': r'\boldsymbol{Q}', '𝑹': r'\boldsymbol{R}',
    '𝑺': r'\boldsymbol{S}', '𝑻': r'\boldsymbol{T}', '𝑼': r'\boldsymbol{U}',
    '𝑽': r'\boldsymbol{V}', '𝑾': r'\boldsymbol{W}', '𝑿': r'\boldsymbol{X}',
    '𝒀': r'\boldsymbol{Y}', '𝒁': r'\boldsymbol{Z}',
    # Mathematical bold italic small a–z U+1D482–U+1D49B
    '𝒂': r'\boldsymbol{a}', '𝒃': r'\boldsymbol{b}', '𝒄': r'\boldsymbol{c}',
    '𝒅': r'\boldsymbol{d}', '𝒆': r'\boldsymbol{e}', '𝒇': r'\boldsymbol{f}',
    '𝒈': r'\boldsymbol{g}', '𝒉': r'\boldsymbol{h}', '𝒊': r'\boldsymbol{i}',
    '𝒋': r'\boldsymbol{j}', '𝒌': r'\boldsymbol{k}', '𝒍': r'\boldsymbol{l}',
    '𝒎': r'\boldsymbol{m}', '𝒏': r'\boldsymbol{n}', '𝒐': r'\boldsymbol{o}',
    '𝒑': r'\boldsymbol{p}', '𝒒': r'\boldsymbol{q}', '𝒓': r'\boldsymbol{r}',
    '𝒔': r'\boldsymbol{s}', '𝒕': r'\boldsymbol{t}', '𝒖': r'\boldsymbol{u}',
    '𝒗': r'\boldsymbol{v}', '𝒘': r'\boldsymbol{w}', '𝒙': r'\boldsymbol{x}',
    '𝒚': r'\boldsymbol{y}', '𝒛': r'\boldsymbol{z}',
    # Subscript/superscript digits
    '₀': '_0', '₁': '_1', '₂': '_2', '₃': '_3', '₄': '_4',
    '₅': '_5', '₆': '_6', '₇': '_7', '₈': '_8', '₉': '_9',
    '⁰': '^0', '¹': '^1', '²': '^2', '³': '^3', '⁴': '^4',
    '⁵': '^5', '⁶': '^6', '⁷': '^7', '⁸': '^8', '⁹': '^9',
}
_FORMULA_UNICODE_RE = re.compile(
    '[' + re.escape(''.join(_FORMULA_UNICODE_MAP.keys())) + ']'
)


def sanitize_formula_unicode(text: str) -> str:
    """
    Sostituisce unicode math chars nell'output di pix2tex/OMML con i loro
    equivalenti LaTeX plain (senza wrapper $...$).
    Da usare PRIMA di embeddare latex_result in \\begin{equation}..\\end{equation}.
    """
    return _FORMULA_UNICODE_RE.sub(lambda m: _FORMULA_UNICODE_MAP[m.group()], text)


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
