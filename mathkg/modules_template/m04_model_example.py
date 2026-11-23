"""Пример модуля с двумя ступенями (правила + модель) — для этапа 4. Модуль 4 (m04): определения.

Студент модуля 4 копирует файл в modules/m04_definitions.py репозитория группы; студенты других модулей
берут его как образец устройства ступени модели. Ступени:

* rules — базовая версия модуля (правила этапа 3);
* model — правила, затем классификатор на предложениях-кандидатах, которые правила не покрыли.

Ступень и порог задаются переменными окружения, чтобы сравнивать их без правки кода:
MATHKG_M04 = rules | model (по умолчанию model), MATHKG_M04_THR = порог уверенности (0.8).
Классификатор (логистическая регрессия на векторах e5) лежит рядом: m04_clf.joblib; если файла нет,
ступень model молча совпадает с rules.
"""
from __future__ import annotations

import os
import re

from mathkg.common import concept_iri, normalize_name
from mathkg.model import Context, Doc, Facts
from mathkg.modules._util import numbered, paragraph_at
from mathkg.modules.m04_definitions import _italic_term, extract_definitions as rules_version
from mathkg.registry import tool

LEVEL = os.environ.get("MATHKG_M04", "model")
THRESHOLD = float(os.environ.get("MATHKG_M04_THR", "0.8"))
CLF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "m04_clf.joblib")
# опорные слова кандидатов: предложение без них модель не проверяет (время модуля ≤ 2 с на статью)
CUE = re.compile(r"\b(называ\w+|назов[её]м|будем называть|означает|определ\w+|is called|are called|we say|"
                 r"is said to be|we call|is defined|denote[sd]?|is referred to as)\b", re.I)

_clf = None


def _classifier():
    """Классификатор загружается один раз за сеанс; кодировщик берётся из общего кэша mathkg.models."""
    global _clf
    if _clf is None and os.path.exists(CLF_PATH):
        import joblib
        _clf = joblib.load(CLF_PATH)
    return _clf


def model_candidates(doc: Doc, facts: Facts, threshold: float = THRESHOLD) -> int:
    """Предложения с опорными словами, не покрытые правилами, проверяет классификатор; найденные
    определения добавляются фрагментами method='model' с уверенностью модели. Возвращает число находок."""
    clf = _classifier()
    if clf is None:
        return 0
    covered = [(f.start, f.end) for f in facts.fragments]
    cand = [s for s in doc.sentences
            if not any(a <= s.start < b for a, b in covered) and CUE.search(doc.text[s.start:s.end])]
    if not cand:
        return 0
    from mathkg.models import get_sentence_model
    enc = get_sentence_model("intfloat/multilingual-e5-base")
    X = enc.encode(["passage: " + doc.text[s.start:s.end] for s in cand], batch_size=32,
                   normalize_embeddings=True, show_progress_bar=False)
    found = 0
    for s, p in zip(cand, clf.predict_proba(X)[:, 1]):
        if p >= threshold:
            facts.add_fragment(id="def-tmp", cls="moc:Definition", start=s.start, end=s.end, page=s.page,
                               method="model", confidence=round(float(p), 3))
            found += 1
    return found


@tool(name="extract_definitions", module="m04", produces=("moc:Definition", "moc:MathematicalConcept"),
      author="пример этапа 4")
def extract_definitions(doc: Doc, ctx: Context) -> Facts:
    """Определения статьи (блоки «Определение 1.» и предложения внутри абзаца) и определяемые понятия.
    Находки правил помечены method='rule', находки классификатора — method='model' с уверенностью модели."""
    facts = rules_version(doc, ctx)
    if LEVEL == "rules" or model_candidates(doc, facts) == 0:
        return facts
    # находки модели добавлены с id 'def-tmp': фрагменты перенумеровываются, связи фрагментов
    # (isPartOf, defines) строятся заново, тройки понятий и статьи остаются как были
    by_id = {f.id: f for f in facts.fragments if f.id != "def-tmp"}
    concept_of = {id(by_id[r.subj]): r.obj for r in facts.relations
                  if r.pred == "moc:defines" and r.subj in by_id}
    facts.relations = [r for r in facts.relations if r.subj not in by_id]
    numbered(facts.fragments, "def")
    for f in facts.fragments:
        par = paragraph_at(doc, f.start)
        if par:
            facts.add(f.id, "moc:isPartOf", par)
        c = concept_of.get(id(f))
        if c is None and f.method == "model":           # понятие для находки модели — курсивный термин
            term = _italic_term(doc, f.start, f.end)
            if term and normalize_name(term):
                c = concept_iri(term)
                facts.add(c, "rdf:type", "moc:MathematicalConcept")
                facts.add(c, "scilibreco:hasName", term, literal=True)
                facts.add(c, "scilibreco:hasNormalizedName", normalize_name(term), literal=True)
                facts.add(doc.iri, "scilibreco:hasConcept", c, confidence=f.confidence)
        if c:
            facts.add(f.id, "moc:defines", c, confidence=f.confidence)
    return facts
