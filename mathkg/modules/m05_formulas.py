"""Модуль 5. Формулы — базовая версия (правила).

Выключные формулы, их номера, координаты и LaTeX (если распознан) уже даёт base
(Doc.formulas, id f-0001 …). Модуль добавляет: класс moc:Equation для формул со знаком
равенства верхнего уровня, нормализованную запись, строчные формулы из отметок
математического шрифта (id if-0001 …) и абзац, в котором стоит формула. Студент улучшает:
нормализацию, переменные и символы (moc:Variable, moc:Symbol), наименование формулы по
контексту языковой моделью, отсев таблиц и кода, ошибочно принятых за формулы.
"""
from __future__ import annotations

import re

from ..model import Context, Doc, Facts, formula_id
from ..registry import tool
from ._util import numbered, paragraph_at

RELATION_INLINE = re.compile(r"[=<>≤≥≠≈≡∈∉⊂⊆⊃⊇→↦]")
RELATION = re.compile(r"(?<![<>!=≤≥])(=|≡|≤|≥|<|>)(?![=])")


def normalize_formula(s: str) -> str:
    """Пробелы вокруг знаков, без хвостовой пунктуации."""
    s = re.sub(r"\s+", " ", s).strip().rstrip(".,;")
    return re.sub(r"\s*([=<>≤≥+\-−×·])\s*", r" \1 ", s).strip()


@tool(name="extract_formulas", module="m05", produces=("moc:Equation", "moc:Formula"))
def extract_formulas(doc: Doc, ctx: Context) -> Facts:
    """Формулы статьи: уравнения среди выключных формул, нормализованная запись, строчные
    формулы, абзацы, в которых стоят формулы."""
    facts = Facts("extract_formulas", doc.doc_id)
    for f in doc.formulas:
        fid = formula_id(f.i)
        src = f.latex or f.text
        if "=" in src and not re.search(r"[<>≤≥]", src):
            facts.add(fid, "rdf:type", "moc:Equation")
        facts.add(fid, "moc:hasNormalizedText", normalize_formula(f.text), literal=True)
        par = paragraph_at(doc, max(0, f.start - 3))
        if par:
            facts.add(fid, "moc:occursIn", par)
            facts.add(par, "moc:containsExpression", fid)
    display = [(f.start, f.end) for f in doc.formulas]
    for s in doc.styled:
        if s.kind != "math" or any(a <= s.start < b for a, b in display):
            continue
        t = doc.text[s.start:s.end].strip()
        # строчная формула — только выражение с отношением: «x_n → 0», «f ∈ L^2», «a ≤ b»;
        # одиночные обозначения (x, f, n) графу не нужны
        if len(t) >= 3 and RELATION_INLINE.search(t) and re.search(r"[A-Za-zα-ωΑ-Ω0-9]", t):
            facts.add_fragment(id="if", cls="moc:Formula", start=s.start, end=s.end, page=s.page,
                               confidence=0.7, attrs={"scilibreco:isDisplayFormula": False})
    numbered(facts.fragments, "if")
    for f in facts.fragments:
        par = paragraph_at(doc, f.start)
        if par:
            facts.add(f.id, "moc:occursIn", par)
        facts.add(doc.iri, "scilibreco:containsFormula", f.id)
    return facts
