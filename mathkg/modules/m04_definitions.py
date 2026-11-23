"""Модуль 4. Определения и обозначения — базовая версия (правила).

Находит определения двух видов: блоки с заголовком «Определение 1.», «Definition 2.3
(Banach space).» и предложения внутри абзаца с оборотами «называется», «будем называть»,
«is called», «we say that». Определяемый термин — курсивный отрезок определения или имя в
скобках после заголовка; он становится понятием mp:concept/… (общий IRI для всех наборов).
Студент улучшает: термин без курсива (модель или языковая модель), обозначения
(«обозначим через», «denote by») как moc:NotationDefinition и moc:Notation, примеры и замечания.
"""
from __future__ import annotations

import re

from ..common import concept_iri, normalize_name
from ..model import Context, Doc, Facts
from ..registry import tool
from ._util import header_re, numbered, paragraph_at

HEAD = header_re(["определение", "definition"])
ATTRIBUTION = re.compile(r"\b(1[6-9]|20)\d\d\b|\bet al\b|\s(and|и)\s+[A-ZА-ЯЁ]")


def clean_term(name):
    """Имя в скобках после заголовка: без ссылок [BP98] и хвоста после ';'. Указание авторства
    «(Tanaka and Sugeno, 1992)» термином не считается."""
    if not name or ATTRIBUTION.search(name):
        return None
    name = re.sub(r"\[[^\]]*\]", "", name).split(";")[0].strip(" ,.:")
    return name if 2 < len(name) <= 60 and normalize_name(name) else None
INLINE = re.compile(r"\b(называ\w+|будем называть|назов[её]м|is called|are called|we say that|is said to be|"
                    r"we call|is referred to as)\b", re.I)


def _italic_term(doc: Doc, start: int, end: int):
    """Первый курсивный отрезок внутри [start, end) длиной 3–60 символов из букв."""
    for s in doc.styled:
        if s.kind == "italic" and start <= s.start < end:
            t = doc.text[s.start:min(s.end, end)].strip(" ,.;:()")
            if 3 <= len(t) <= 60 and re.search(r"[A-Za-zА-Яа-яЁё]{3}", t) and not re.search(r"[=<>∈]", t):
                return t
    return None


@tool(name="extract_definitions", module="m04", produces=("moc:Definition", "moc:MathematicalConcept"))
def extract_definitions(doc: Doc, ctx: Context) -> Facts:
    """Определения (блоки и предложения внутри абзаца) и определяемые понятия: связь
    moc:defines от определения к понятию mp:concept/…"""
    facts = Facts("extract_definitions", doc.doc_id)
    terms = {}
    for p in doc.paragraphs:
        t = doc.text[p.start:p.end]
        m = HEAD.match(t)
        if m:
            f = facts.add_fragment(id="def", cls="moc:Definition", start=p.start, end=p.end,
                                   number=m.group("num"), label=" ".join(x for x in (m.group("word"), m.group("num")) if x),
                                   page=p.page, confidence=0.9)
            terms[id(f)] = clean_term(m.group("name")) or _italic_term(doc, p.start + m.end(), p.end)
    covered = [(f.start, f.end) for f in facts.fragments]
    for s in doc.sentences:
        if any(a <= s.start < b for a, b in covered):
            continue
        if INLINE.search(doc.text[s.start:s.end]):
            term = _italic_term(doc, s.start, s.end)
            if term:                                  # без термина предложение не считается определением
                f = facts.add_fragment(id="def", cls="moc:Definition", start=s.start, end=s.end,
                                       page=s.page, confidence=0.6)
                terms[id(f)] = term
    numbered(facts.fragments, "def")
    for f in facts.fragments:
        par = paragraph_at(doc, f.start)
        if par:
            facts.add(f.id, "moc:isPartOf", par)
        term = terms.get(id(f))
        if term and normalize_name(term):
            c = concept_iri(term)
            facts.add(c, "rdf:type", "moc:MathematicalConcept")
            facts.add(c, "scilibreco:hasName", term, literal=True)
            facts.add(c, "scilibreco:hasNormalizedName", normalize_name(term), literal=True)
            facts.add(f.id, "moc:defines", c, confidence=f.confidence)
            facts.add(doc.iri, "scilibreco:hasConcept", c, confidence=f.confidence)
    return facts
