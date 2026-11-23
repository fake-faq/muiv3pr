"""Модуль 2. Логическая структура статьи — базовая версия (правила).

Находит заголовки разделов («1. Введение», «2.1 Preliminaries», «Заключение»), определяет тип
раздела по названию (moc:Introduction, moc:Preliminaries, moc:ResultsSection,
moc:ConclusionSection, moc:Appendix, moc:Subsection, moc:Section) и связывает раздел с его
абзацами. Подписи к рисункам и таблицам — moc:Caption. Студент улучшает: заголовки без номера
по шрифту, типы разделов классификатором, рисунки и таблицы, сноски.
"""
from __future__ import annotations

import re

from ..model import Context, Doc, Facts
from ..registry import tool
from ._util import is_bold, numbered

HEADING = re.compile(r"^\s*(?P<num>\d+(?:\.\d+)*|[IVX]{1,4}|[A-Z])\.?\s+(?P<title>[A-ZА-ЯЁ][^\n]{1,100})$")
UNNUMBERED = re.compile(r"^\s*(Введение|Заключение|Выводы|Introduction|Conclusions?|Preliminaries|"
                        r"Acknowledg(e)?ments?|Благодарности|Приложение|Appendix)\s*\.?$", re.I)
TYPES = [  # (слова названия, класс)
    (r"введени|introduc", "moc:Introduction"),
    (r"предварительн|обозначени|preliminar|notation|background", "moc:Preliminaries"),
    (r"основн\w* результат|main result|результат", "moc:ResultsSection"),
    (r"заключени|вывод|conclu|discussion", "moc:ConclusionSection"),
    (r"приложени|appendix", "moc:Appendix"),
    (r"обзор|related work|литератур\w* обзор", "moc:RelatedWork"),
    (r"метод|method", "moc:MethodSection"),
]
CAPTION = re.compile(r"^\s*(Рис\.|Рисунок|Fig\.|Figure|Таблица|Табл\.|Table)\s*\d+", re.I)
BIB = re.compile(r"^\s*(Список литературы|Литература|References|Bibliography)\s*$", re.I)


@tool(name="extract_structure", module="m02",
      produces=("moc:Section", "moc:Subsection", "moc:Introduction", "moc:ConclusionSection", "moc:Caption"))
def extract_structure(doc: Doc, ctx: Context) -> Facts:
    """Разделы статьи с номерами и названиями, их тип и состав (абзацы раздела), подписи к
    рисункам и таблицам."""
    facts = Facts("extract_structure", doc.doc_id)
    heads = []
    for i, p in enumerate(doc.paragraphs):
        t = doc.text[p.start:p.end]
        if len(t) > 120 or t.rstrip().endswith((".", ",", ";")) and not UNNUMBERED.match(t):
            if not CAPTION.match(t):
                continue
        if CAPTION.match(t):
            facts.add_fragment(id="cap", cls="moc:Caption", start=p.start, end=p.end, page=p.page)
            continue
        if BIB.match(t):
            heads.append((i, None, t.strip(), "bib"))
            continue
        m = HEADING.match(t)
        if m and (is_bold(doc, p.start, p.end) or len(t) < 70):
            heads.append((i, m.group("num"), m.group("title").strip(), None))
        elif UNNUMBERED.match(t):
            heads.append((i, None, t.strip().rstrip("."), None))
    secs = []
    for k, (i, num, title, kind) in enumerate(heads):
        if kind == "bib":
            continue                                   # раздел литературы размечает модуль 6
        p = doc.paragraphs[i]
        nxt = heads[k + 1][0] if k + 1 < len(heads) else len(doc.paragraphs)
        cls = "moc:Subsection" if num and "." in num else "moc:Section"
        for rx, c in TYPES:
            if re.search(rx, title, re.I) and cls == "moc:Section":
                cls = c
                break
        end = doc.paragraphs[nxt - 1].end if nxt > i else p.end
        f = facts.add_fragment(id="sec", cls=cls, start=p.start, end=end, number=num, label=title,
                               page=p.page, confidence=0.8 if num else 0.6)
        secs.append((f, i, nxt))
    numbered(secs and [s[0] for s in secs] or [], "sec")
    numbered(facts.find("moc:Caption"), "cap")
    for f, i, nxt in secs:
        facts.add(f.id, "scilibreco:hasTitle", f.label, literal=True)
        facts.add(f.id, "moc:hasOrderIndex", int(f.id.split("-")[1]), literal=True)
        # абзацы раздела связью не перечисляются: раздел хранит свои границы в тексте, и абзац
        # относится к разделу, если его позиции лежат внутри них (запрос по moc:hasStartOffset)
    # подраздел входит в раздел с номером-префиксом
    by_num = {f.number: f.id for f, _, _ in secs if f.number}
    for f, _, _ in secs:
        if f.number and "." in f.number and f.number.rsplit(".", 1)[0] in by_num:
            facts.add(f.id, "moc:isPartOf", by_num[f.number.rsplit(".", 1)[0]])
    return facts
