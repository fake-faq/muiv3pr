"""Модуль 6. Список литературы — базовая версия (правила).

Находит раздел «Список литературы / Литература / References», делит его на записи по меткам
«[12]» или «12.» и извлекает из записи DOI и год. Запись с DOI связывается с цитируемой работой
mp:work/doi-… — этот IRI одинаков у всех студентов, по нему строится граф цитирования группы.
Студент улучшает: записи без номеров (алфавитный список), разбор записи на авторов, название,
журнал (модель NER или языковая модель), работы без DOI (common.work_iri), дубль списка
литературы на латинице в русских журналах.
"""
from __future__ import annotations

import re

from ..common import doi_work_iri
from ..model import Context, Doc, Facts
from ..registry import tool

HEAD = re.compile(r"(?:^|\n\n)\s*(Список литературы|Список использованной литературы|Литература|References|"
                  r"Bibliography|REFERENCES|СПИСОК ЛИТЕРАТУРЫ)\s*\n\n")
ENTRY = re.compile(r"(?:^|(?<=\n\n)|(?<=\s))\[(\d{1,3})\]\s+|(?:^|(?<=\n\n))(\d{1,3})\.\s+(?=[A-ZА-ЯЁ])")
APPENDIX = re.compile(r"\n\n\s*(Appendix|APPENDIX|Приложение|ПРИЛОЖЕНИЕ)\b")
DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+[^\s\"<>.,;\]])")
YEAR = re.compile(r"\b(1[89]\d\d|20[0-3]\d)\b")


@tool(name="extract_bibliography", module="m06", produces=("moc:Bibliography", "moc:ReferenceEntry"))
def extract_bibliography(doc: Doc, ctx: Context) -> Facts:
    """Список литературы: записи с номерами, DOI и годом; связь записи с цитируемой работой
    mp:work/doi-… по DOI."""
    facts = Facts("extract_bibliography", doc.doc_id)
    heads = list(HEAD.finditer(doc.text))
    if not heads:
        return facts
    h = heads[-1] if heads[-1].start() > len(doc.text) * 0.4 else heads[0]
    start, end = h.end(), len(doc.text)
    app = APPENDIX.search(doc.text, start)              # приложение после списка литературы
    if app:
        end = app.start()
    facts.add_fragment(id="bib", cls="moc:Bibliography", start=start, end=end, page=doc.page_of(start))
    marks = [m for m in ENTRY.finditer(doc.text, start, end)]
    for k, m in enumerate(marks):
        num = m.group(1) or m.group(2)
        e = marks[k + 1].start() if k + 1 < len(marks) else end
        s = m.end()
        if e - s < 10:
            continue
        rid = f"ref-{int(num):02d}"
        if any(f.id == rid for f in facts.fragments):
            continue
        facts.add_fragment(id=rid, cls="moc:ReferenceEntry", start=s, end=e, number=num, page=doc.page_of(s))
        facts.add(rid, "moc:isPartOf", "bib")
        text = doc.text[s:e]
        d = DOI.search(text)
        if d:
            doi = d.group(1).lower()
            w = doi_work_iri(doi)
            facts.add(rid, "moc:hasDOI", doi, literal=True)
            facts.add(rid, "moc:mentions", w)
            facts.add(w, "rdf:type", "moc:Publication")
            facts.add(w, "moc:hasDOI", doi, literal=True)
            y = YEAR.findall(text)
            if y:
                facts.add(w, "scilibreco:hasPublicationYear", int(y[-1]), literal=True, confidence=0.7)
    return facts
