# -*- coding: utf-8 -*-
r"""
omml2latex.py  —  OMML → LaTeX converter
=========================================
Converte formule OMML (Office Math Markup Language, usato in .pptx/.docx)
in LaTeX valido, senza dipendenze esterne oltre a lxml.

Struttura OMML → LaTeX supportata:
  m:f          \frac{num}{den}
  m:sSup       base^{sup}
  m:sSub       base_{sub}
  m:sSubSup    base_{sub}^{sup}
  m:rad        \sqrt{x}  o  \sqrt[n]{x}
  m:nary       \sum_{i}^{n}, \int_{a}^{b}, \prod
  m:func       \sin, \cos, \log, \lim ...
  m:limLow     \lim_{x \to 0}
  m:limUpp     upper limit (rare)
  m:d          delimitatori: ( ) [ ] { } |
  m:m + m:mr   \begin{pmatrix}...\end{pmatrix}
  m:eqArr      \begin{aligned}...\end{aligned}
  m:groupChr   \overbrace, \underbrace, \overline, \underline
  m:bar        \bar{x}
  m:acc        \hat{x}, \tilde{x}, \vec{x}, \dot{x}, \ddot{x}
  m:borderBox  \boxed{x}
  m:r + m:t    testo/simbolo (con mappatura unicode → LaTeX)

Uso:
    from omml2latex import omml_to_latex
    latex = omml_to_latex('<m:oMath ...>...</m:oMath>')
    # Ritorna stringa LaTeX o None in caso di fallimento
"""

from __future__ import annotations

import re
from lxml import etree
from typing import Optional

# ─────────────────────────────────────────────────────────────
# NAMESPACE
# ─────────────────────────────────────────────────────────────
_M  = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_W  = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

def _tag(local: str) -> str:
    return f"{{{_M}}}{local}"


# ─────────────────────────────────────────────────────────────
# MAPPATURA UNICODE → LaTeX  (simboli, lettere greche, operatori)
# ─────────────────────────────────────────────────────────────
_CHAR_MAP: dict[str, str] = {
    # Operatori greci
    "α": r"\alpha",    "β": r"\beta",     "γ": r"\gamma",    "δ": r"\delta",
    "ε": r"\epsilon",  "ζ": r"\zeta",     "η": r"\eta",      "θ": r"\theta",
    "ι": r"\iota",     "κ": r"\kappa",    "λ": r"\lambda",   "μ": r"\mu",
    "ν": r"\nu",       "ξ": r"\xi",       "π": r"\pi",       "ρ": r"\rho",
    "σ": r"\sigma",    "τ": r"\tau",      "υ": r"\upsilon",  "φ": r"\phi",
    "χ": r"\chi",      "ψ": r"\psi",      "ω": r"\omega",
    "Γ": r"\Gamma",    "Δ": r"\Delta",    "Θ": r"\Theta",    "Λ": r"\Lambda",
    "Ξ": r"\Xi",       "Π": r"\Pi",       "Σ": r"\Sigma",    "Υ": r"\Upsilon",
    "Φ": r"\Phi",      "Ψ": r"\Psi",      "Ω": r"\Omega",
    # Operatori matematici
    "∑": r"\sum",      "∫": r"\int",      "∬": r"\iint",     "∭": r"\iiint",
    "∮": r"\oint",     "∏": r"\prod",     "∂": r"\partial",  "∇": r"\nabla",
    "∞": r"\infty",    "√": r"\sqrt",     "∝": r"\propto",
    # Insiemi e logica
    "∈": r"\in",       "∉": r"\notin",    "∋": r"\ni",       "∅": r"\emptyset",
    "⊂": r"\subset",   "⊃": r"\supset",   "⊆": r"\subseteq", "⊇": r"\supseteq",
    "∪": r"\cup",      "∩": r"\cap",      "∀": r"\forall",   "∃": r"\exists",
    "¬": r"\neg",      "∧": r"\wedge",    "∨": r"\vee",
    # Frecce
    "→": r"\to",       "←": r"\leftarrow","↔": r"\leftrightarrow",
    "⇒": r"\Rightarrow","⇐": r"\Leftarrow","⇔": r"\Leftrightarrow",
    "↑": r"\uparrow",  "↓": r"\downarrow","↕": r"\updownarrow",
    "⇑": r"\Uparrow",  "⇓": r"\Downarrow",
    # Relazioni
    "≠": r"\neq",      "≤": r"\leq",      "≥": r"\geq",
    "≈": r"\approx",   "≡": r"\equiv",    "≅": r"\cong",     "∼": r"\sim",
    "≪": r"\ll",       "≫": r"\gg",       "∥": r"\parallel", "⊥": r"\perp",
    # Aritmetici
    "±": r"\pm",       "∓": r"\mp",       "×": r"\times",    "÷": r"\div",
    "·": r"\cdot",     "⊗": r"\otimes",   "⊕": r"\oplus",
    # Delimitatori
    "⟨": r"\langle",   "⟩": r"\rangle",   "‖": r"\|",        "⌈": r"\lceil",
    "⌉": r"\rceil",    "⌊": r"\lfloor",   "⌋": r"\rfloor",
    # Misc
    "…": r"\ldots",    "⋯": r"\cdots",    "⋮": r"\vdots",    "⋱": r"\ddots",
    "°": r"^{\circ}",  "′": r"'",         "″": r"''",
    "ℝ": r"\mathbb{R}","ℤ": r"\mathbb{Z}","ℕ": r"\mathbb{N}","ℚ": r"\mathbb{Q}",
    "ℂ": r"\mathbb{C}","ℓ": r"\ell",      "ℏ": r"\hbar",
    # Caratteri che LaTeX vuole escaped nel testo math
    "&": r"\&",        "%": r"\%",        "#": r"\#",
}

# Funzioni standard che diventano \operatorname{...} o comandi diretti
_FUNC_MAP: dict[str, str] = {
    "sin": r"\sin",   "cos": r"\cos",   "tan": r"\tan",
    "cot": r"\cot",   "sec": r"\sec",   "csc": r"\csc",
    "arcsin": r"\arcsin", "arccos": r"\arccos", "arctan": r"\arctan",
    "sinh": r"\sinh", "cosh": r"\cosh", "tanh": r"\tanh",
    "exp": r"\exp",   "log": r"\log",   "ln": r"\ln",   "lg": r"\lg",
    "lim": r"\lim",   "inf": r"\inf",   "sup": r"\sup",
    "max": r"\max",   "min": r"\min",   "det": r"\det",
    "dim": r"\dim",   "ker": r"\ker",   "rank": r"\operatorname{rank}",
    "deg": r"\deg",   "gcd": r"\gcd",   "lcm": r"\operatorname{lcm}",
    "tr": r"\operatorname{tr}", "div": r"\operatorname{div}",
    "curl": r"\operatorname{curl}", "grad": r"\operatorname{grad}",
}

# Simboli accento per m:acc
_ACC_MAP: dict[str, str] = {
    "̂": r"\hat",     "̃": r"\tilde",  "̄": r"\bar",
    "̀": r"\grave",   "́": r"\acute",  "̈": r"\ddot",
    "̇": r"\dot",     "⃗": r"\vec",    "̆": r"\breve",
    "̌": r"\check",   "̊": r"\mathring",
    # Codici unicode hex per gli stessi accenti
    # Nota: le righe seguenti usano caratteri unicode diretti (combining chars)
    # che raramente appaiono in OMML reale; la mappatura principale è sopra

}


def _map_char(ch: str) -> str:
    """Mappa un singolo carattere unicode in LaTeX, se nella mappa."""
    return _CHAR_MAP.get(ch, ch)


def _map_text(text: str) -> str:
    """Applica _map_char a ogni carattere della stringa."""
    return "".join(_map_char(c) for c in text)


def _needs_braces(s: str) -> str:
    """Wrappa s in {} se ha più di un token."""
    s = s.strip()
    if not s:
        return "{}"
    if len(s) == 1:
        return s
    # Già in braces?
    if s.startswith("{") and s.endswith("}"):
        return s
    # Singolo comando LaTeX senza argomenti (es. \alpha)
    if re.match(r'^\\[a-zA-Z]+$', s):
        return s
    return "{" + s + "}"


# ─────────────────────────────────────────────────────────────
# FUNZIONI DI CONVERSIONE (ricorsive)
# ─────────────────────────────────────────────────────────────

def _convert_node(node: etree._Element) -> str:
    """
    Dispatch principale: sceglie il convertitore in base al tag OMML.
    Ritorna la stringa LaTeX corrispondente.
    """
    local = node.tag.split("}")[-1] if "}" in node.tag else node.tag
    fn = _CONVERTERS.get(local)
    if fn:
        return fn(node)
    # Tag sconosciuto: converti i figli ricorsivamente
    return _children(node)


def _children(node: etree._Element) -> str:
    """Converte tutti i figli e concatena."""
    return "".join(_convert_node(c) for c in node)


def _child_text(node: etree._Element, child_local: str) -> str:
    """Testo LaTeX del primo figlio con tag locale dato."""
    child = node.find(_tag(child_local))
    if child is None:
        return ""
    return _children(child)


# ── m:r  (run matematico: testo + stile) ──────────────────────
def _conv_r(node: etree._Element) -> str:
    t_node = node.find(_tag("t"))
    if t_node is None or t_node.text is None:
        return ""
    text = t_node.text

    # Stile: bold (mathbf) / italic (mathrm per upright, default italic)
    rpr = node.find(_tag("rPr"))
    is_bold   = False
    is_normal = False  # sty="p" → testo upright
    if rpr is not None:
        if rpr.find(_tag("b")) is not None:
            is_bold = True
        sty = rpr.get(_tag("sty"))
        if sty == "p":
            is_normal = True

    result = _map_text(text)

    if is_bold:
        result = r"\mathbf{" + result + "}"
    elif is_normal and result.isalpha():
        result = r"\mathrm{" + result + "}"

    return result


# ── m:t  (testo puro, fuori da m:r — raro) ───────────────────
def _conv_t(node: etree._Element) -> str:
    return _map_text(node.text or "")


# ── m:f  (frazione) ──────────────────────────────────────────
def _conv_f(node: etree._Element) -> str:
    num = _child_text(node, "num")
    den = _child_text(node, "den")
    # Controlla se è frazione a trattino (normal) o in-line (lin → \tfrac)
    fpr = node.find(_tag("fPr"))
    ftype = ""
    if fpr is not None:
        ft = fpr.find(_tag("type"))
        if ft is not None:
            ftype = ft.get(_tag("val"), "")
    if ftype == "lin":
        return f"{num}/{den}"
    if ftype == "skw":
        return f"{num}\\!/{den}"
    if ftype == "noBar":
        return r"\binom{" + num + "}{" + den + "}"
    return r"\frac{" + num + "}{" + den + "}"


# ── m:sSup  (apice: base^{sup}) ──────────────────────────────
def _conv_sSup(node: etree._Element) -> str:
    base = _child_text(node, "e")
    sup  = _child_text(node, "sup")
    return base + "^" + _needs_braces(sup)


# ── m:sSub  (pedice: base_{sub}) ─────────────────────────────
def _conv_sSub(node: etree._Element) -> str:
    base = _child_text(node, "e")
    sub  = _child_text(node, "sub")
    return base + "_" + _needs_braces(sub)


# ── m:sSubSup  (base_{sub}^{sup}) ────────────────────────────
def _conv_sSubSup(node: etree._Element) -> str:
    base = _child_text(node, "e")
    sub  = _child_text(node, "sub")
    sup  = _child_text(node, "sup")
    return base + "_" + _needs_braces(sub) + "^" + _needs_braces(sup)


# ── m:rad  (radice) ──────────────────────────────────────────
def _conv_rad(node: etree._Element) -> str:
    e   = _child_text(node, "e")
    deg = _child_text(node, "deg").strip()
    # degHide → radice quadrata
    rpr = node.find(_tag("radPr"))
    deg_hidden = False
    if rpr is not None:
        dh = rpr.find(_tag("degHide"))
        if dh is not None:
            val = dh.get(_tag("val"), "1")
            deg_hidden = val in ("1", "true", "on")
    if deg_hidden or not deg:
        return r"\sqrt{" + e + "}"
    return r"\sqrt[" + deg + "]{" + e + "}"


# ── m:nary  (operatori n-ary: ∑ ∫ ∏ ...) ────────────────────
def _conv_nary(node: etree._Element) -> str:
    sub = _child_text(node, "sub")
    sup = _child_text(node, "sup")
    e   = _child_text(node, "e")

    # Simbolo operatore
    npr  = node.find(_tag("naryPr"))
    sym  = ""
    limLoc = "undOvr"  # default: limiti sopra/sotto
    if npr is not None:
        chr_el = npr.find(_tag("chr"))
        if chr_el is not None:
            sym = chr_el.get(_tag("val"), "")
        ll = npr.find(_tag("limLoc"))
        if ll is not None:
            limLoc = ll.get(_tag("val"), "undOvr")

    op = _CHAR_MAP.get(sym, r"\sum" if not sym else sym)

    # Limiti inline (subSup) vs display (undOvr)
    if limLoc == "subSup":
        result = op
        if sub: result += "_" + _needs_braces(sub)
        if sup: result += "^" + _needs_braces(sup)
    else:
        result = op
        if sub: result += "_{" + sub + "}"
        if sup: result += "^{" + sup + "}"

    return result + " " + e


# ── m:func  (funzione: sin, cos, lim ...) ────────────────────
def _conv_func(node: etree._Element) -> str:
    fname_node = node.find(_tag("fName"))
    e_node     = node.find(_tag("e"))
    fname = _children(fname_node).strip() if fname_node is not None else ""
    arg   = _children(e_node).strip()    if e_node is not None else ""

    # Riconosci il nome funzione
    clean_fname = re.sub(r'\\?([a-zA-Z]+)', r'\1', fname).strip()
    latex_fn = _FUNC_MAP.get(clean_fname, fname)

    # Argomento tra parentesi
    return latex_fn + r"\left(" + arg + r"\right)"


# ── m:limLow  (limite basso, es. \lim_{x→0}) ─────────────────
def _conv_limLow(node: etree._Element) -> str:
    e   = _child_text(node, "e")
    lim = _child_text(node, "lim")
    return e + "_{" + lim + "}"


# ── m:limUpp  (limite alto) ──────────────────────────────────
def _conv_limUpp(node: etree._Element) -> str:
    e   = _child_text(node, "e")
    lim = _child_text(node, "lim")
    return e + "^{" + lim + "}"


# ── m:d  (delimitatori) ──────────────────────────────────────
_DELIM_LATEX: dict[str, str] = {
    "(": r"\left(",   ")": r"\right)",
    "[": r"\left[",   "]": r"\right]",
    "{": r"\left\{",  "}": r"\right\}",
    "|": r"\left|",   "‖": r"\left\|",
    "⌈": r"\left\lceil", "⌉": r"\right\rceil",
    "⌊": r"\left\lfloor","⌋": r"\right\rfloor",
    "⟨": r"\left\langle","⟩": r"\right\rangle",
    "": r"\left.",    # delimitatore vuoto (per left/right)
}

def _conv_d(node: etree._Element) -> str:
    dpr = node.find(_tag("dPr"))
    l_sym = "("
    r_sym = ")"
    sep   = ","
    if dpr is not None:
        l = dpr.find(_tag("begChr"))
        r = dpr.find(_tag("endChr"))
        s = dpr.find(_tag("sepChr"))
        if l is not None: l_sym = l.get(_tag("val"), "(")
        if r is not None: r_sym = r.get(_tag("val"), ")")
        if s is not None: sep   = s.get(_tag("val"), ",")

    l_latex = _DELIM_LATEX.get(l_sym, l_sym)
    r_latex = _DELIM_LATEX.get(r_sym, r_sym)
    sep_latex = _CHAR_MAP.get(sep, sep)

    # Elementi interni (uno per m:e)
    parts = [_children(e) for e in node.findall(_tag("e"))]
    inner = (" " + sep_latex + " ").join(parts)
    return l_latex + inner + r_latex


# ── m:m + m:mr  (matrice) ────────────────────────────────────
def _conv_m(node: etree._Element) -> str:
    mpr   = node.find(_tag("mPr"))
    l_sym = "("
    r_sym = ")"
    if mpr is not None:
        mcs = mpr.find(_tag("mcs"))
        # look for \begin{matrix} vs pmatrix vs bmatrix
        brkBinSub = mpr.find(_tag("brk"))
        begChr = mpr.find(_tag("begChr"))
        endChr = mpr.find(_tag("endChr"))
        if begChr is not None: l_sym = begChr.get(_tag("val"), "(")
        if endChr is not None: r_sym = endChr.get(_tag("val"), ")")

    env = {
        ("(", ")"): "pmatrix",
        ("[", "]"): "bmatrix",
        ("{", "}"): "Bmatrix",
        ("|", "|"): "vmatrix",
        ("‖","‖"): "Vmatrix",
        ("",  "" ): "matrix",
    }.get((l_sym, r_sym), "pmatrix")

    rows = []
    for mr in node.findall(_tag("mr")):
        cells = [_children(e) for e in mr.findall(_tag("e"))]
        rows.append(" & ".join(cells))

    body = " \\\\\n".join(rows)
    return r"\begin{" + env + "}\n" + body + "\n" + r"\end{" + env + "}"


# ── m:eqArr  (array di equazioni allineate) ──────────────────
def _conv_eqArr(node: etree._Element) -> str:
    rows = []
    for e in node.findall(_tag("e")):
        rows.append(_children(e))
    body = " \\\\\n".join(rows)
    return r"\begin{aligned}" + "\n" + body + "\n" + r"\end{aligned}"


# ── m:bar  (barra sopra/sotto) ───────────────────────────────
def _conv_bar(node: etree._Element) -> str:
    e = _child_text(node, "e")
    bpr = node.find(_tag("barPr"))
    pos = "top"
    if bpr is not None:
        p = bpr.find(_tag("pos"))
        if p is not None:
            pos = p.get(_tag("val"), "top")
    if pos == "bot":
        return r"\underline{" + e + "}"
    return r"\overline{" + e + "}"


# ── m:acc  (accento: hat, tilde, vec, dot ...) ───────────────
def _conv_acc(node: etree._Element) -> str:
    e = _child_text(node, "e")
    apr = node.find(_tag("accPr"))
    sym = "̂"  # default: hat
    if apr is not None:
        chr_el = apr.find(_tag("chr"))
        if chr_el is not None:
            sym = chr_el.get(_tag("val"), "̂")
    cmd = _ACC_MAP.get(sym, _ACC_MAP.get(sym.strip(), r"\hat"))
    return cmd + "{" + e + "}"


# ── m:groupChr  (overbrace, underbrace, overline ...) ────────
def _conv_groupChr(node: etree._Element) -> str:
    e = _child_text(node, "e")
    gpr = node.find(_tag("groupChrPr"))
    chr_val = "⏞"  # default: overbrace
    pos     = "top"
    if gpr is not None:
        c = gpr.find(_tag("chr"))
        p = gpr.find(_tag("pos"))
        if c is not None: chr_val = c.get(_tag("val"), "⏞")
        if p is not None: pos     = p.get(_tag("val"), "top")

    _GRP = {
        "⏞": (r"\overbrace",  "top"),
        "⏟": (r"\underbrace", "bot"),
        "⎵": (r"\underbrace", "bot"),
        "‾": (r"\overline",   "top"),
        "_": (r"\underline",  "bot"),
    }
    cmd_top, _ = _GRP.get(chr_val, (r"\overbrace", "top"))
    if pos == "bot":
        cmd_top = cmd_top.replace("over", "under")

    return cmd_top + "{" + e + "}"


# ── m:borderBox  (riquadro attorno a espressione) ────────────
def _conv_borderBox(node: etree._Element) -> str:
    e = _child_text(node, "e")
    return r"\boxed{" + e + "}"


# ── m:phant  (phantom: spazio trasparente) ───────────────────
def _conv_phant(node: etree._Element) -> str:
    e = _child_text(node, "e")
    return r"\phantom{" + e + "}"


# ── m:sPre  (prescritto: ^{a}_{b} base) ─────────────────────
def _conv_sPre(node: etree._Element) -> str:
    sub  = _child_text(node, "sub")
    sup  = _child_text(node, "sup")
    base = _child_text(node, "e")
    result = ""
    if sup: result += "^" + _needs_braces(sup)
    if sub: result += "_" + _needs_braces(sub)
    return result + base


# ── m:oMath / m:oMathPara  (radice documento) ────────────────
def _conv_oMath(node: etree._Element) -> str:
    return _children(node)


# ── m:r run con stile (intercetta per gestire spazi) ─────────
def _conv_ctrlPr(node: etree._Element) -> str:
    return ""  # solo metadati di stile, nessun contenuto


# ─────────────────────────────────────────────────────────────
# TABELLA DISPATCH
# ─────────────────────────────────────────────────────────────
_CONVERTERS: dict[str, callable] = {
    "oMath":      _conv_oMath,
    "oMathPara":  _conv_oMath,
    "r":          _conv_r,
    "t":          _conv_t,
    "f":          _conv_f,
    "sSup":       _conv_sSup,
    "sSub":       _conv_sSub,
    "sSubSup":    _conv_sSubSup,
    "rad":        _conv_rad,
    "nary":       _conv_nary,
    "func":       _conv_func,
    "limLow":     _conv_limLow,
    "limUpp":     _conv_limUpp,
    "d":          _conv_d,
    "m":          _conv_m,
    "eqArr":      _conv_eqArr,
    "bar":        _conv_bar,
    "acc":        _conv_acc,
    "groupChr":   _conv_groupChr,
    "borderBox":  _conv_borderBox,
    "phant":      _conv_phant,
    "sPre":       _conv_sPre,
    # Contenitori trasparenti
    "num":        _children,
    "den":        _children,
    "e":          _children,
    "sub":        _children,
    "sup":        _children,
    "deg":        _children,
    "lim":        _children,
    "fName":      _children,
    "mr":         _children,
    # Proprietà (nessun output)
    "fPr":        lambda n: "",
    "naryPr":     lambda n: "",
    "radPr":      lambda n: "",
    "dPr":        lambda n: "",
    "mPr":        lambda n: "",
    "barPr":      lambda n: "",
    "accPr":      lambda n: "",
    "groupChrPr": lambda n: "",
    "sSupPr":     lambda n: "",
    "sSubPr":     lambda n: "",
    "sSubSupPr":  lambda n: "",
    "funcPr":     lambda n: "",
    "limLowPr":   lambda n: "",
    "limUppPr":   lambda n: "",
    "sPrePr":     lambda n: "",
    "ctrlPr":     _conv_ctrlPr,
    "rPr":        lambda n: "",
    "chr":        lambda n: "",
    "pos":        lambda n: "",
    "type":       lambda n: "",
    "val":        lambda n: "",
    "begChr":     lambda n: "",
    "endChr":     lambda n: "",
    "sepChr":     lambda n: "",
    "degHide":    lambda n: "",
    "limLoc":     lambda n: "",
    "mcs":        lambda n: "",
    "mc":         lambda n: "",
    "mcPr":       lambda n: "",
    "count":      lambda n: "",
    "mcJc":       lambda n: "",
    "b":          lambda n: "",
    "sty":        lambda n: "",
    "brk":        lambda n: "",
}


# ─────────────────────────────────────────────────────────────
# POST-PROCESSING  — pulizia del LaTeX prodotto
# ─────────────────────────────────────────────────────────────

def _postprocess(latex: str) -> str:
    """
    Pulisce e normalizza il LaTeX grezzo prodotto dalla conversione.
    """
    # 1. Collassa spazi multipli in uno
    latex = re.sub(r'[ \t]+', ' ', latex)

    # 2. Rimuovi spazi inutili attorno a apici/pedici
    latex = re.sub(r'\s+([_^])\s*', r'\1', latex)

    # 3. Normalizza braces vuote ridondanti: {}{} → {}
    latex = re.sub(r'\{\}\{\}', '{}', latex)

    # 4. Rimuovi \left.\right. se entrambi vuoti
    latex = re.sub(r'\\left\.\s*\\right\.', '', latex)

    # 5. Trim
    latex = latex.strip()

    # 6. Se il risultato contiene solo caratteri non-LaTeX/non-math, segnala
    if latex and not any(c.isalnum() or c in r'\{}^_' for c in latex):
        return ""

    return latex


# ─────────────────────────────────────────────────────────────
# API PUBBLICA
# ─────────────────────────────────────────────────────────────

def omml_to_latex(omml_xml: str) -> Optional[str]:
    """
    Converte una stringa XML OMML (m:oMath o m:oMathPara) in LaTeX.

    Parametri:
        omml_xml  — stringa XML come prodotta da extractor.py

    Ritorna:
        Stringa LaTeX (senza $...$ o \\begin{equation}) pronta per essere
        inserita in un ambiente matematico.
        None se la conversione fallisce completamente.

    Esempio:
        latex = omml_to_latex('<m:oMath ...><m:f><m:num>...</m:num>...</m:f></m:oMath>')
        # → r'\frac{a}{b}'
    """
    if not omml_xml or not omml_xml.strip():
        return None

    try:
        root = etree.fromstring(omml_xml.encode("utf-8") if isinstance(omml_xml, str) else omml_xml)
    except etree.XMLSyntaxError:
        # Prova ad aggiungere namespace mancante
        try:
            wrapped = (
                '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
                ' xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                + omml_xml + "</m:oMath>"
            )
            root = etree.fromstring(wrapped.encode("utf-8"))
        except Exception:
            return _fallback_text_extract(omml_xml)

    try:
        raw = _convert_node(root)
        result = _postprocess(raw)
        if result:
            return result
        # Se la conversione strutturata produce stringa vuota, usa fallback
        return _fallback_text_extract(omml_xml)
    except Exception as exc:
        # Non propagare eccezioni — usa fallback testuale
        import sys
        print(f"    [omml2latex] conversione fallita: {exc}", file=sys.stderr)
        return _fallback_text_extract(omml_xml)


def _fallback_text_extract(omml_xml: str) -> Optional[str]:
    """
    Fallback: estrae il testo grezzo dai nodi m:t e lo mappa con _map_text.
    Non produce LaTeX strutturato, ma almeno i simboli sono corretti.
    """
    try:
        # Prova parsing
        xml = omml_xml
        if not xml.strip().startswith("<"):
            return None
        # Aggiungi namespace se mancante
        if "xmlns:m=" not in xml:
            xml = xml.replace("<m:oMath", '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"', 1)
            if "<m:oMath" not in xml:
                xml = f'<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">{xml}</m:oMath>'
        root = etree.fromstring(xml.encode("utf-8"))
        ns   = _M
        texts = root.findall(f".//{{{ns}}}t")
        content = " ".join(t.text or "" for t in texts if t.text)
        if content.strip():
            return _map_text(content.strip())
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────
# TEST SELF-CONTAINED
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    _NS = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

    tests = [
        ("Frazione semplice",
         f'<m:oMath {_NS}>'
         '<m:f><m:num><m:r><m:t>a</m:t></m:r></m:num>'
         '<m:den><m:r><m:t>b</m:t></m:r></m:den></m:f></m:oMath>',
         r'\frac{a}{b}'),

        ("Apice",
         f'<m:oMath {_NS}>'
         '<m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e>'
         '<m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup></m:oMath>',
         'x^2'),

        ("Pedice",
         f'<m:oMath {_NS}>'
         '<m:sSub><m:e><m:r><m:t>a</m:t></m:r></m:e>'
         '<m:sub><m:r><m:t>n</m:t></m:r></m:sub></m:sSub></m:oMath>',
         'a_n'),

        ("Radice quadrata",
         f'<m:oMath {_NS}><m:rad>'
         '<m:radPr><m:degHide m:val="1"/></m:radPr><m:deg/>'
         '<m:e><m:r><m:t>x</m:t></m:r></m:e></m:rad></m:oMath>',
         r'\sqrt{x}'),

        ("Radice n-esima",
         f'<m:oMath {_NS}><m:rad>'
         '<m:deg><m:r><m:t>3</m:t></m:r></m:deg>'
         '<m:e><m:r><m:t>x</m:t></m:r></m:e></m:rad></m:oMath>',
         r'\sqrt[3]{x}'),

        ("Sommatoria",
         f'<m:oMath {_NS}><m:nary>'
         '<m:naryPr><m:chr m:val="∑"/></m:naryPr>'
         '<m:sub><m:r><m:t>i=0</m:t></m:r></m:sub>'
         '<m:sup><m:r><m:t>n</m:t></m:r></m:sup>'
         '<m:e><m:r><m:t>x</m:t></m:r></m:e></m:nary></m:oMath>',
         r'\sum_{i=0}^{n} x'),

        ("Simbolo greco",
         f'<m:oMath {_NS}><m:r><m:t>α</m:t></m:r></m:oMath>',
         r'\alpha'),

        ("Binomio",
         f'<m:oMath {_NS}><m:f>'
         '<m:fPr><m:type m:val="noBar"/></m:fPr>'
         '<m:num><m:r><m:t>n</m:t></m:r></m:num>'
         '<m:den><m:r><m:t>k</m:t></m:r></m:den></m:f></m:oMath>',
         r'\binom{n}{k}'),

        ("Delimitatori",
         f'<m:oMath {_NS}><m:d>'
         '<m:dPr><m:begChr m:val="["/><m:endChr m:val="]"/></m:dPr>'
         '<m:e><m:r><m:t>x</m:t></m:r></m:e></m:d></m:oMath>',
         r'\left[x\right]'),

        ("Matrice 2x2",
         f'<m:oMath {_NS}><m:m>'
         '<m:mr><m:e><m:r><m:t>a</m:t></m:r></m:e><m:e><m:r><m:t>b</m:t></m:r></m:e></m:mr>'
         '<m:mr><m:e><m:r><m:t>c</m:t></m:r></m:e><m:e><m:r><m:t>d</m:t></m:r></m:e></m:mr>'
         '</m:m></m:oMath>',
         None),  # solo check che non crashi

        ("Hat accento",
         f'<m:oMath {_NS}><m:acc>'
         '<m:accPr><m:chr m:val="̂"/></m:accPr>'
         '<m:e><m:r><m:t>x</m:t></m:r></m:e></m:acc></m:oMath>',
         r'\hat{x}'),
    ]

    print("=" * 60)
    print("  omml2latex — test suite")
    print("=" * 60)
    passed = failed = 0
    for name, xml, expected in tests:
        result = omml_to_latex(xml)
        if expected is None:
            # Solo verifica che non crashi e produca qualcosa
            ok = result is not None
        else:
            ok = result is not None and expected in (result or "")
        status = "✓" if ok else "✗"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  {status} {name}")
        if not ok:
            print(f"      atteso : {expected!r}")
            print(f"      ottenuto: {result!r}")

    print(f"\n  {passed}/{passed+failed} test passati")
    print("=" * 60)