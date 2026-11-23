"""Общая основа base: PDF статьи -> Doc (текст, страницы, абзацы, предложения, отметки шрифта,
выключные формулы с номерами, внутренние гиперссылки) и распознавание формул в LaTeX.

Основу пишет и сопровождает руководитель; студенты её используют и сообщают об ошибках.
Главные функции:
    load_doc(pdf_path, doc_id)            -> Doc
    build_corpus(index_csv, pdf_dir, out_dir)  — весь набор с продолжением с места остановки
    recognize_formulas(doc, pdf_path)     — LaTeX выключных формул (нужен GPU, pix2tex)
    read_doc(out_dir, doc_id)             -> Doc, сохранённый build_corpus
"""
from __future__ import annotations

import csv
import json
import os
import re
import time
import unicodedata
from collections import Counter
from typing import Dict, List, Optional, Tuple

from ..model import Doc, Formula, Link, Span

# ---------------------------------------------------------------------------
# 1. Символы
# ---------------------------------------------------------------------------

LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
             "­": "", " ": " ", " ": " ", " ": " ", "﻿": ""}
MATH_FONT = re.compile(r"CMMI|CMSY|CMEX|CMBSY|MSAM|MSBM|EUFM|EURM|RSFS|Math|STIX|Symbol|LMMathItalic|"
                       r"rsfs|wasy|esint|txsy|pxsy|MTMI|MTSY|Cambria Math", re.I)
MATH_CHARS = set("=<>≤≥≠≈≡∼∝∈∉⊂⊃⊆⊇∪∩∧∨¬∀∃∅∞∂∇∑∏∫∮√±∓×÷·∘→←↔⇒⇐⇔↦‖|^_{}αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΦΨΩ")


# Шрифт Symbol в PDF из Word: символы лежат в частной зоне Unicode U+F020–U+F0FF
_SYM = dict(zip(range(0x41, 0x5B), "ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ"))
_SYM.update(zip(range(0x61, 0x7B), "αβχδεφγηιϕκλμνοπθρστυϖωξψζ"))
_SYM.update({0x22: "∀", 0x24: "∃", 0x27: "∋", 0x2D: "−", 0x40: "≅", 0x5C: "∴", 0x5E: "⊥", 0x7E: "∼",
             0xA3: "≤", 0xA5: "∞", 0xAB: "↔", 0xAC: "←", 0xAD: "↑", 0xAE: "→", 0xAF: "↓", 0xB0: "°",
             0xB1: "±", 0xB3: "≥", 0xB4: "×", 0xB5: "∝", 0xB6: "∂", 0xB7: "•", 0xB8: "÷", 0xB9: "≠",
             0xBA: "≡", 0xBB: "≈", 0xBC: "…", 0xBE: "—", 0xC4: "⊗", 0xC5: "⊕", 0xC6: "∅", 0xC7: "∩",
             0xC8: "∪", 0xC9: "⊃", 0xCA: "⊇", 0xCB: "⊄", 0xCC: "⊂", 0xCD: "⊆", 0xCE: "∈", 0xCF: "∉",
             0xD0: "∠", 0xD1: "∇", 0xD5: "∏", 0xD6: "√", 0xD7: "⋅", 0xD8: "¬", 0xD9: "∧", 0xDA: "∨",
             0xDB: "⇔", 0xDC: "⇐", 0xDE: "⇒", 0xE5: "∑", 0xF2: "∫"})
_SYM.update({c: chr(c) for c in range(0x20, 0x40) if c not in _SYM})   # цифры, скобки, знаки


def norm_char(ch: str) -> str:
    """Лигатуры и служебные пробелы — по таблице; математические буквы Unicode (𝑥, 𝜑, 𝔽) —
    в обычные. NFKC применяется только к этому диапазону: к остальному тексту он вредит
    (верхние индексы ², дроби ½ превратились бы в обычные цифры без следа)."""
    if ch in LIGATURES:
        return LIGATURES[ch]
    if 0x1D400 <= ord(ch) <= 0x1D7FF:
        return unicodedata.normalize("NFKC", ch)
    if 0xF020 <= ord(ch) <= 0xF0FF:
        return _SYM.get(ord(ch) - 0xF000, "")
    if ord(ch) < 32 and ch not in "\n\t":
        return ""                       # управляющие символы из шрифтов формул (\x10 и др.)
    return ch


def norm_text(s: str) -> str:
    return "".join(norm_char(c) for c in s)


def garbage_share(text: str) -> float:
    """Доля символов, характерных для сломанной кодировки шрифта: замены U+FFFD, частная зона
    Unicode, управляющие символы, латиница с диакритикой вида 'Ã', 'Ð' в русском тексте."""
    if not text:
        return 1.0
    bad = 0
    for c in text:
        o = ord(c)
        if c == "�" or 0xE000 <= o <= 0xF8FF or (o < 32 and c not in "\n\t") or 0xC0 <= o <= 0xFF:
            bad += 1
    return bad / len(text)


# ---------------------------------------------------------------------------
# 2. Строки PDF со шрифтами
# ---------------------------------------------------------------------------

def _line_record(page_no: int, block_no: int, line: dict) -> Optional[dict]:
    spans = []
    for s in line["spans"]:
        t = norm_text(s["text"])
        if not t:
            continue
        is_math = bool(MATH_FONT.search(s["font"])) or (
            len(t.strip()) > 0 and sum(c in MATH_CHARS for c in t) / max(1, len(t.strip())) > 0.5)
        spans.append({"text": t, "bbox": list(s["bbox"]), "size": round(s["size"], 1), "font": s["font"],
                      "bold": bool(s["flags"] & 16) or "Bold" in s["font"] or "bold" in s["font"],
                      "italic": bool(s["flags"] & 2) or "Italic" in s["font"] or "Oblique" in s["font"],
                      "math": is_math})
    if not spans:
        return None
    text = "".join(s["text"] for s in spans)
    if not text.strip():
        return None
    x0 = min(s["bbox"][0] for s in spans); y0 = min(s["bbox"][1] for s in spans)
    x1 = max(s["bbox"][2] for s in spans); y1 = max(s["bbox"][3] for s in spans)
    size = Counter()
    for s in spans:
        size[s["size"]] += len(s["text"])
    return {"page": page_no, "block": block_no, "text": text, "bbox": [x0, y0, x1, y1],
            "size": size.most_common(1)[0][0], "bold": all(s["bold"] for s in spans if s["text"].strip()),
            "italic": all(s["italic"] for s in spans if s["text"].strip()),
            "math_share": sum(len(s["text"]) for s in spans if s["math"]) / max(1, len(text)),
            "spans": spans}


def _column_order(blocks: List[dict], page_width: float) -> List[dict]:
    """Порядок чтения: страница делится на полосы полноширинными блоками; внутри полосы
    сначала левая колонка, затем правая. Для одноколоночной вёрстки порядок не меняется."""
    mid = page_width / 2
    def side(b):
        x0, _, x1, _ = b["bbox"]
        if x1 < mid + 10:
            return 0
        if x0 > mid - 10:
            return 1
        return 2
    two_col = sum(side(b) == 1 for b in blocks) >= 2 and sum(side(b) == 0 for b in blocks) >= 2
    if not two_col:
        return sorted(blocks, key=lambda b: (round(b["bbox"][1]), b["bbox"][0]))
    out, band = [], []
    for b in sorted(blocks, key=lambda b: b["bbox"][1]):
        if side(b) == 2:
            out += sorted(band, key=lambda x: (side(x), x["bbox"][1])); band = []
            out.append(b)
        else:
            band.append(b)
    return out + sorted(band, key=lambda x: (side(x), x["bbox"][1]))


def read_pdf(path: str) -> Tuple[List[List[dict]], list, dict, List[float]]:
    """Строки всех страниц в порядке чтения, сырые ссылки, метаданные PDF, ширины страниц."""
    import fitz  # PyMuPDF
    pdf = fitz.open(path)
    pages, links, widths = [], [], []
    for pno, page in enumerate(pdf, start=1):
        widths.append(page.rect.width)
        raw = page.get_text("dict")["blocks"]
        blocks = [b for b in raw if b.get("type", 0) == 0]
        lines = []
        for bno, b in enumerate(_column_order(blocks, page.rect.width)):
            for ln in b.get("lines", []):
                r = _line_record(pno, bno, ln)
                if r:
                    lines.append(r)
        pages.append(lines)
        for l in page.get_links():
            target, tpage, ty = l.get("nameddest") or "", int(l.get("page", -1)) + 1, 0.0
            if l.get("to") is not None:
                try:
                    ty = float(l["to"].y)
                except Exception:
                    ty = 0.0
            if target or l.get("kind") == fitz.LINK_GOTO:
                links.append({"page": pno, "rect": list(l["from"]), "target": target or f"page.{tpage}",
                              "target_page": tpage, "target_y": ty,
                              "text": norm_text(page.get_textbox(l["from"])).strip()})
    meta = {k: v for k, v in (pdf.metadata or {}).items() if v}
    return pages, links, meta, widths


# ---------------------------------------------------------------------------
# 3. Колонтитулы, сноски, формулы
# ---------------------------------------------------------------------------

def _shape(t: str) -> str:
    return re.sub(r"\d+", "#", t.strip().lower())


def find_running_lines(pages: List[List[dict]]) -> set:
    """Колонтитулы и номера страниц: строки из двух верхних и двух нижних на странице, которые
    после замены цифр на '#' повторяются хотя бы на трети страниц (не меньше трёх)."""
    if len(pages) < 3:
        return set()
    cnt = Counter()
    for lines in pages:
        if not lines:
            continue
        by_y = sorted(lines, key=lambda l: l["bbox"][1])
        cand = {_shape(l["text"]) for l in by_y[:2] + by_y[-2:]}
        cnt.update(cand)
    thr = max(3, len(pages) // 3)
    shapes = {s for s, c in cnt.items() if c >= thr}
    shapes |= {"#", "- # -", "– # –"}
    return shapes


def words_in(t: str) -> int:
    return len(re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", t))


CODE_LIKE = re.compile(r"^(</?[\w:.-]+[^>]*>|.*:=.*\bby\b\s*$|(def|import|return|theorem|lemma)\s)")
NUM_ONLY = re.compile(r"^\(\s*(\d+(?:\.\d+)*[a-z]?|[A-Z]\.\d+)\s*\)$")
NUM_RIGHT = re.compile(r"\(\s*(\d+(?:\.\d+)*[a-z]?|[A-Z]\.\d+|\*+)\s*\)\s*$")


def is_display_formula(line: dict, body_left: float, body_right: float) -> bool:
    """Выключная формула: строка в основном из математики, отступ слева от края текста,
    либо номер в скобках у правого края при заметной доле математики."""
    t = line["text"].strip()
    if len(t) < 2 or line["bold"] or CODE_LIKE.match(t):
        return False
    x0, _, x1, _ = line["bbox"]
    indented = x0 > body_left + 25
    numbered = bool(NUM_RIGHT.search(t)) and x1 > body_right - 15
    letters = sum(c.isalpha() for c in t)
    words = len(re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", t))
    if line["math_share"] >= 0.6 and words <= 3:
        return indented or numbered
    if numbered and line["math_share"] >= 0.25 and words <= 4:
        return True
    return indented and words == 0 and letters < len(t) * 0.7 and any(c in MATH_CHARS for c in t)


# ---------------------------------------------------------------------------
# 4. Сборка текста
# ---------------------------------------------------------------------------

HEADER_START = re.compile(
    r"^\s*(\d+(\.\d+)*\.?\s+[A-ZА-ЯЁ]|(Теорема|Лемма|Предложение|Следствие|Определение|Замечание|Пример|"
    r"Доказательство|Утверждение|Гипотеза|Theorem|Lemma|Proposition|Corollary|Definition|Remark|Example|"
    r"Proof|Claim|Conjecture|Аннотация|Abstract|Ключевые слова|Keywords|Список литературы|Литература|"
    r"References|Bibliography)\b)")


def _sentences(text: str, lang: str) -> List[Tuple[int, int]]:
    if lang == "ru":
        from razdel import sentenize
        return [(s.start, s.stop) for s in sentenize(text)]
    import pysbd
    seg = pysbd.Segmenter(language="en", clean=False, char_span=True)
    return [(s.start, s.end) for s in seg.segment(text)]


def detect_lang(text: str) -> str:
    cyr = sum("а" <= c.lower() <= "я" or c in "ёЁ" for c in text)
    lat = sum("a" <= c.lower() <= "z" for c in text)
    return "ru" if cyr > 0.3 * (cyr + lat) else "en"


def assemble(doc_id: str, pages: List[List[dict]], links_raw: list, widths: List[float],
             meta: dict) -> Doc:
    """Строки -> Doc: убирает колонтитулы и сноски, склеивает переносы, собирает абзацы,
    выделяет выключные формулы, переносит отметки шрифта и ссылки в позиции текста."""
    all_lines = [l for p in pages for l in p]
    sizes = Counter()
    for l in all_lines:
        sizes[l["size"]] += len(l["text"])
    body = sizes.most_common(1)[0][0] if sizes else 10.0
    lefts = Counter(round(l["bbox"][0]) for l in all_lines if abs(l["size"] - body) < 0.6)
    body_left = lefts.most_common(1)[0][0] if lefts else 70
    rights = Counter(round(l["bbox"][2]) for l in all_lines if abs(l["size"] - body) < 0.6)
    body_right = max((r for r, c in rights.items() if c >= 5), default=500)
    running = find_running_lines(pages)

    text_parts: List[str] = []
    pos = 0
    doc_pages, paragraphs, styled, formulas, footnotes = [], [], [], [], []
    span_index = []  # (page, bbox, start, end) — для сопоставления ссылок
    pending_number = {}  # номер формулы, встреченный раньше самой формулы: {page: (номер, y)}
    first_page_lines = []

    def emit(s: str) -> Tuple[int, int]:
        nonlocal pos
        start = pos
        text_parts.append(s)
        pos += len(s)
        return start, pos

    for pno, lines in enumerate(pages, start=1):
        page_start = pos
        if pno == 1:
            first_page_lines = [{k: l[k] for k in ("text", "bbox", "size", "bold", "italic")} for l in lines]
        page_h = max((l["bbox"][3] for l in lines), default=800)
        by_y = sorted(lines, key=lambda l: l["bbox"][1])
        edge = {id(l) for l in by_y[:2] + by_y[-2:]}       # кандидаты в колонтитулы
        para: List[dict] = []
        prev = None

        def flush_para():
            nonlocal para
            if not para:
                return
            # склейка строк абзаца: перенос «простран-» + «ство» -> «пространство»
            buf, marks = "", []
            for k, ln in enumerate(para):
                t = ln["text"].rstrip()
                joiner = ""
                if buf:
                    if re.search(r"[A-Za-zА-Яа-яЁё]-$", buf) and re.match(r"[a-zа-яё]", t.lstrip()):
                        buf = buf[:-1]
                    else:
                        joiner = " "
                base_off = len(buf) + len(joiner)
                local = 0
                for sp in ln["spans"]:
                    st = sp["text"]
                    if k > 0 and local == 0:
                        st2 = st.lstrip()
                        local += len(st) - len(st2)
                        st = st2
                    marks.append((base_off + local, base_off + local + len(st.rstrip()), sp))
                    local += len(st)
                buf += joiner + t.lstrip() if k > 0 else t
            if text_parts:
                emit("\n\n")
            s0, s1 = emit(buf)
            paragraphs.append(Span(s0, s1, pno))
            for a, b, sp in marks:
                a = min(s0 + a, s1); b = min(s0 + b, s1)
                if b <= a:
                    continue
                span_index.append((pno, sp["bbox"], a, b))
                for kind in ("bold", "italic", "math"):
                    if sp[kind]:
                        styled.append(Span(a, b, pno, kind))
            para = []

        for ln in lines:
            t = ln["text"].strip()
            if _shape(t) in running and id(ln) in edge:
                continue
            # сноска: мелкий кегль в нижней четверти страницы
            if ln["size"] < body - 1.2 and ln["bbox"][1] > page_h * 0.75 and not ln["math_share"] > 0.5:
                flush_para(); footnotes.append(t); prev = ln
                continue
            mnum = NUM_ONLY.match(t)
            if mnum and ln["bbox"][0] > body_right - 80:
                # номер формулы отдельной строкой у правого края: к формуле на той же высоте
                yc = (ln["bbox"][1] + ln["bbox"][3]) / 2
                for f in reversed(formulas[-3:]):
                    if f.page == pno and f.bbox[1] - 4 <= yc <= f.bbox[3] + 4 and not f.number:
                        f.number = mnum.group(1)
                        break
                else:
                    pending_number[pno] = (mnum.group(1), yc)
                continue
            # обломок многострочной формулы («r», «i=1», «˙x(t) =») рядом с формулой — её часть
            frag_math = (len(t) <= 30 and words_in(t) <= 1 and (ln["math_share"] >= 0.3 or len(t) <= 3)) \
                and (prev is not None and prev.get("_formula") or ln["math_share"] >= 0.5)
            if frag_math or is_display_formula(ln, body_left, body_right):
                flush_para()
                m = NUM_RIGHT.search(t)
                number = m.group(1) if m else None
                ftxt = NUM_RIGHT.sub("", t).strip() if m else t
                if formulas and formulas[-1].page == pno and prev is not None \
                        and prev.get("_formula") and ln["bbox"][1] - prev["bbox"][3] < ln["size"] * 1.2:
                    f = formulas[-1]                       # продолжение многострочной формулы
                    emit(" "); _, e = emit(ftxt)
                    f.end, f.text, f.number = e, f.text + " " + ftxt, f.number or number
                    f.bbox = [min(f.bbox[0], ln["bbox"][0]), f.bbox[1], max(f.bbox[2], ln["bbox"][2]), ln["bbox"][3]]
                else:
                    if text_parts:
                        emit("\n\n")
                    s0, s1 = emit(ftxt)
                    formulas.append(Formula(len(formulas), s0, s1, pno, list(ln["bbox"]), ftxt, number))
                pn = pending_number.get(pno)
                if pn and not formulas[-1].number and formulas[-1].bbox[1] - 4 <= pn[1] <= ln["bbox"][3] + 4:
                    formulas[-1].number = pn[0]; pending_number.pop(pno)
                ln = dict(ln, _formula=True); prev = ln
                continue
            new_par = False
            if prev is None or prev.get("_formula"):
                new_par = True
            else:
                gap = ln["bbox"][1] - prev["bbox"][3]
                if ln["block"] != prev["block"] and gap > -2:
                    new_par = True
                elif gap > ln["size"] * 0.9:
                    new_par = True
                elif ln["bbox"][0] > body_left + 6 and prev["bbox"][2] < body_right - 30:
                    new_par = True                         # красная строка после короткой строки
                elif HEADER_START.match(t) and (ln["bold"] or ln["spans"][0]["bold"]):
                    new_par = True
            if new_par:
                flush_para()
            para.append(ln); prev = ln
        flush_para()
        doc_pages.append(Span(page_start, pos, pno))

    text = "".join(text_parts)
    lang = detect_lang(text[:20000])
    sentences = []
    for p in paragraphs:
        for a, b in _sentences(text[p.start:p.end], lang):
            sentences.append(Span(p.start + a, p.start + b, p.page))

    links = []
    for l in links_raw:
        x0, y0, x1, y1 = l["rect"]
        hits = [(a, b) for (pg, bb, a, b) in span_index
                if pg == l["page"] and bb[0] < x1 and x0 < bb[2] and bb[1] < y1 and y0 < bb[3]]
        start, end = (min(h[0] for h in hits), max(h[1] for h in hits)) if hits else (-1, -1)
        lt = l.get("text", "")
        if start >= 0 and lt:                       # уточнение: текст ссылки внутри найденных отрезков
            for cand in (lt, lt.strip("[]() ,.;")):
                k = text.find(cand, max(0, start - 2), end + len(cand) + 2) if cand else -1
                if k >= 0:
                    start, end = k, k + len(cand)
                    break
        links.append(Link(start, end, l["page"], l["target"], l["target_page"], l["target_y"]))

    quality = {"chars": len(text), "chars_per_page": len(text) / max(1, len(pages)),
               "garbage_share": round(garbage_share(text), 4), "body_size": body}
    return Doc(doc_id=doc_id, lang=lang, text=text, pages=doc_pages, paragraphs=paragraphs,
               sentences=sentences, styled=_merge_styled(styled), formulas=formulas, links=links,
               footnotes=footnotes, first_page_lines=first_page_lines,
               meta={"n_pages": len(pages), "quality": quality, "pdf_metadata": meta})


def _merge_styled(styled: List[Span]) -> List[Span]:
    """Соседние отрезки одного стиля, разделённые не более чем пробелом, сливаются."""
    out: List[Span] = []
    for s in sorted(styled, key=lambda s: (s.kind, s.start)):
        if out and out[-1].kind == s.kind and s.start - out[-1].end <= 1:
            out[-1].end = max(out[-1].end, s.end)
        else:
            out.append(Span(s.start, s.end, s.page, s.kind))
    return sorted(out, key=lambda s: s.start)


# ---------------------------------------------------------------------------
# 5. Публичные функции
# ---------------------------------------------------------------------------

def check_text_layer(doc: Doc, min_chars_per_page: int = 200, max_garbage: float = 0.05) -> Optional[str]:
    """None, если текстовый слой пригоден; иначе причина: 'scan' или 'broken_encoding'."""
    q = doc.meta["quality"]
    if q["chars_per_page"] < min_chars_per_page:
        return "scan"
    if q["garbage_share"] > max_garbage:
        return "broken_encoding"
    return None


def load_doc(pdf_path: str, doc_id: str) -> Doc:
    """PDF статьи -> Doc."""
    pages, links, meta, widths = read_pdf(pdf_path)
    doc = assemble(doc_id, pages, links, widths, meta)
    doc.meta["file"] = os.path.basename(pdf_path)
    return doc


def save_doc(doc: Doc, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{doc.doc_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc.to_json())
    return path


def read_doc(out_dir: str, doc_id: str) -> Doc:
    with open(os.path.join(out_dir, f"{doc_id}.json"), encoding="utf-8") as f:
        return Doc.from_json(f.read())


def build_corpus(index_csv: str, pdf_dir: str, out_dir: str, limit: Optional[int] = None,
                 log_every: int = 50) -> Dict[str, int]:
    """Обрабатывает набор из index.csv (поля doc_id, file). Уже готовые статьи пропускает,
    поэтому после обрыва сеанса запускается повторно. Пишет out_dir/{doc_id}.json и сводку
    out_dir/docs.jsonl (doc_id, file, lang, n_pages, chars, garbage_share, status, sec)."""
    os.makedirs(out_dir, exist_ok=True)
    summary_path = os.path.join(out_dir, "docs.jsonl")
    done = set()
    if os.path.exists(summary_path):
        with open(summary_path, encoding="utf-8") as f:
            done = {json.loads(l)["doc_id"] for l in f if l.strip()}
    with open(index_csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if limit:
        rows = rows[:limit]
    stats = Counter()
    with open(summary_path, "a", encoding="utf-8") as out:
        for k, r in enumerate(rows, 1):
            if r["doc_id"] in done:
                stats["skipped"] += 1
                continue
            t0 = time.time()
            rec = {"doc_id": r["doc_id"], "file": r["file"]}
            try:
                doc = load_doc(os.path.join(pdf_dir, r["file"]), r["doc_id"])
                status = check_text_layer(doc) or "ok"
                if status == "ok":
                    save_doc(doc, out_dir)
                rec.update(lang=doc.lang, n_pages=doc.meta["n_pages"], chars=len(doc.text),
                           garbage_share=doc.meta["quality"]["garbage_share"], status=status)
            except Exception as e:  # повреждённый PDF не должен останавливать прогон
                rec.update(status="error", error=str(e)[:200])
            rec["sec"] = round(time.time() - t0, 2)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n"); out.flush()
            stats[rec["status"]] += 1
            if k % log_every == 0:
                print(k, dict(stats))
    return dict(stats)


def recognize_formulas(doc: Doc, pdf_path: str, limit: Optional[int] = None, zoom: float = 3.0,
                       model=None) -> int:
    """Распознаёт LaTeX выключных формул: вырезает формулу по координатам из изображения
    страницы и передаёт модели pix2tex (LaTeX-OCR). Записывает Formula.latex, возвращает число
    распознанных. Модель создаётся один раз и передаётся параметром model при обработке набора:
        from pix2tex.cli import LatexOCR; ocr = LatexOCR()
    На GPU T4 — доли секунды на формулу."""
    import fitz
    from PIL import Image
    import io
    if model is None:
        from pix2tex.cli import LatexOCR
        model = LatexOCR()
    pdf = fitz.open(pdf_path)
    n = 0
    for f in doc.formulas[:limit]:
        if f.latex:
            continue
        page = pdf[f.page - 1]
        x0, y0, x1, y1 = f.bbox
        clip = fitz.Rect(x0 - 2, y0 - 2, x1 + 2, y1 + 2) & page.rect
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        try:
            f.latex = model(img)
            n += 1
        except Exception:
            f.latex = None
    return n
