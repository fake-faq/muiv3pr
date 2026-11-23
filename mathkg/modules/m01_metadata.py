"""Модуль 1. Метаданные статьи — базовая версия (правила).

Находит заголовок, аннотацию, ключевые слова, УДК, коды MSC, DOI, год, авторов и их e-mail.
Базовая версия опирается на первую страницу: самый крупный кегль, слова «Аннотация», «Abstract»,
«Ключевые слова», «Keywords» и регулярные выражения. Студент улучшает: строка авторов и
организации по верхним индексам, модель NER для имён, дубль метаданных на английском, GROBID
для сравнения.
"""
from __future__ import annotations

import re

from ..common import MP, normalize_name, person_iri, slug
from ..model import Context, Doc, Facts
from ..registry import tool

UDC = re.compile(r"(?:УДК|UDC)\s*:?\s*([0-9][0-9.:()\-+/ ]{0,40}[0-9)])")
MSC = re.compile(r"(?:MSC|Mathematics Subject Classification)[^:\n]{0,30}:?\s*((?:\d{2}[A-Z]\d{2}|\d{2}[A-Z]xx|\d{2}-XX)"
                 r"(?:\s*[,;]\s*(?:\d{2}[A-Z]\d{2}|\d{2}[A-Z]xx))*)")
DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+[^\s\"<>.,;])")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
YEAR = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")
ABSTRACT = re.compile(r"(Аннотация|Abstract|Резюме|Summary)\s*[.:—-]?\s*", re.I)
KEYWORDS = re.compile(r"(Ключевые слова|Keywords|Key words)\s*[.:—-]?\s*", re.I)
STOP_ABSTRACT = re.compile(r"\n\n(Ключевые слова|Keywords|Key words|1\.?\s+(Введение|Introduction)|Введение|Introduction)", re.I)
NAME = re.compile(r"([A-ZА-ЯЁ]\.\s*(?:[A-ZА-ЯЁ]\.\s*)?)([A-ZА-ЯЁ][a-zа-яё'’-]+)|"
                  r"([A-ZА-ЯЁ][a-zа-яё'’-]+)\s+([A-ZА-ЯЁ]\.\s*(?:[A-ZА-ЯЁ]\.)?)|"
                  r"([A-Z][a-z]+)\s+(?:[A-Z]\.\s+)?([A-Z][a-z'’-]+)")


@tool(name="extract_metadata", module="m01",
      produces=("moc:Title", "moc:Abstract", "moc:KeywordList", "scilibreco:Author", "scilibreco:Keyword"))
def extract_metadata(doc: Doc, ctx: Context) -> Facts:
    """Метаданные статьи: заголовок, аннотация, ключевые слова, авторы и их e-mail, УДК, MSC,
    DOI, год. Возвращает фрагменты title, abstract, keywords и связи статьи с авторами,
    ключевыми словами и литералами метаданных."""
    facts = Facts("extract_metadata", doc.doc_id)
    d = doc.iri
    head = doc.text[: doc.pages[0].end] if doc.pages else doc.text[:5000]

    # заголовок: строки верхней половины 1-й страницы самого крупного кегля; без штампа arXiv и
    # вертикального текста; если крупнее основного текста ничего нет — полужирные или прописные строки
    page_h = max((l["bbox"][3] for l in doc.first_page_lines), default=800)
    body = doc.meta.get("quality", {}).get("body_size", 10)
    lines = [l for l in doc.first_page_lines
             if len(l["text"].strip()) > 3 and l["bbox"][1] < page_h * 0.5
             and re.search(r"[A-Za-zА-Яа-я]{3}", l["text"]) and not l["text"].lstrip().startswith("arXiv:")
             and (l["bbox"][2] - l["bbox"][0]) > (l["bbox"][3] - l["bbox"][1])
             and not UDC.search(l["text"])]
    if lines:
        big = max(l["size"] for l in lines)
        cand = [l for l in lines if abs(l["size"] - big) < 0.6]
        if big <= body + 0.5:
            cand = [l for l in lines if l["bold"] or l["text"].strip().isupper()][:4]
        pos, start, end = 0, -1, -1
        for l in cand:                               # строки заголовка ищутся в тексте по порядку
            k = doc.text.find(l["text"].strip(), pos)
            if k < 0 or (start >= 0 and k - end > 10):
                break
            start = k if start < 0 else start
            end = pos = k + len(l["text"].strip())
        if start >= 0:
            facts.add_fragment(id="title", cls="moc:Title", start=start, end=end, page=1, confidence=0.8)
            clean = " ".join(doc.text[start:end].split())
            facts.add(d, "scilibreco:hasTitle", clean, literal=True)
            facts.add(d, "rdfs:label", clean, literal=True)

    m = ABSTRACT.search(head)
    if m:
        stop = STOP_ABSTRACT.search(doc.text, m.end())
        end = stop.start() if stop and stop.start() - m.end() < 4000 else doc.text.find("\n\n", m.end())
        if end > m.end():
            facts.add_fragment(id="abstract", cls="moc:Abstract", start=m.end(), end=end, page=1, confidence=0.8)
            facts.add(d, "scilibreco:hasAbstract", " ".join(doc.text[m.end():end].split()), literal=True)

    m = KEYWORDS.search(doc.text[: (doc.pages[1].end if len(doc.pages) > 1 else len(doc.text))])
    if m:
        end = doc.text.find("\n\n", m.end())
        end = end if end > 0 else m.end() + 300
        facts.add_fragment(id="keywords", cls="moc:KeywordList", start=m.end(), end=end, confidence=0.8)
        for kw in re.split(r"\s*[;,]\s*", doc.text[m.end():end].rstrip(". ")):
            kw = " ".join(kw.split())
            if 2 < len(kw) < 80:
                ki = MP + "keyword/" + slug(normalize_name(kw))
                facts.add(ki, "rdf:type", "scilibreco:Keyword")
                facts.add(ki, "scilibreco:hasName", kw, literal=True)
                facts.add(d, "scilibreco:hasKeyword", ki)

    for rx, pred in ((UDC, "scilibreco:hasUDC"), (DOI, "moc:hasDOI")):
        m = rx.search(head)
        if m:
            v = m.group(1).strip()
            facts.add(d, pred, v.lower() if pred == "moc:hasDOI" else v, literal=True, start=m.start(1), end=m.end(1))
    m = MSC.search(doc.text[:20000])
    if m:
        for code in re.split(r"\s*[,;]\s*", m.group(1)):
            facts.add(d, "moc:hasMSCCode", code, literal=True, start=m.start(1), end=m.end(1))
    years = [int(y) for y in YEAR.findall(head[:3000])]
    if years:
        facts.add(d, "scilibreco:hasPublicationYear", max(years), literal=True, confidence=0.6)

    # авторы: имена в строках 1-й страницы между заголовком и аннотацией
    a_end = (m.start() if (m := ABSTRACT.search(head)) else min(len(head), 1500))
    t_end = facts.fragments[0].end if facts.fragments and facts.fragments[0].id == "title" else 0
    zone = doc.text[t_end:a_end]
    emails = EMAIL.findall(head)
    seen = set()
    for mm in NAME.finditer(zone):
        if mm.group(2):
            initials, surname = mm.group(1), mm.group(2)
        elif mm.group(3):
            surname, initials = mm.group(3), mm.group(4)
        else:
            initials, surname = mm.group(5)[0] + ".", mm.group(6)
        pi = person_iri(surname, initials)
        if pi in seen or len(surname) < 3:
            continue
        seen.add(pi)
        facts.add(pi, "rdf:type", "scilibreco:Author", confidence=0.6)
        facts.add(pi, "scilibreco:hasName", " ".join(mm.group(0).split()), literal=True)
        facts.add(pi, "scilibreco:hasNormalizedName", slug(f"{surname} {initials}").replace("-", " "), literal=True)
        facts.add(d, "scilibreco:hasAuthor", pi, confidence=0.6, start=t_end + mm.start(), end=t_end + mm.end())
        sl = slug(surname)
        for e in emails:
            if sl[:5] and sl[:5] in e.lower():
                facts.add(pi, "scilibreco:hasEmail", e, literal=True)
    return facts
