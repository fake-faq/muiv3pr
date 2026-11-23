"""Модуль 10. Предметные области — базовая версия (правила). Зависит от модуля 1.

Относит статью к разделам arXiv по кодам MSC (первые две цифры кода) и, если MSC нет, по УДК.
Разделы — существующие экземпляры рабочего графа SciLibReco (scilibreco:subject_math-ap и др.),
новых областей модуль не заводит. Студент улучшает: статьи без кодов (zero-shot классификация или
близость эмбеддинга аннотации к описаниям разделов), уверенность отнесения, близость статей
набора (scilibreco:semanticallySimilarTo) отдельной функцией по всему набору.
"""
from __future__ import annotations

from ..model import Context, Doc, Facts
from ..registry import tool

SLR = "https://scilib.ai/ontology/scilibreco#"
MSC2ARXIV = {"03": "math-lo", "05": "math-co", "06": "math-ra", "08": "math-ra", "11": "math-nt", "12": "math-ra",
             "13": "math-ac", "14": "math-ag", "15": "math-ra", "16": "math-ra", "17": "math-ra", "18": "math-ct",
             "19": "math-kt", "20": "math-gr", "22": "math-gr", "26": "math-ca", "28": "math-ca", "30": "math-cv",
             "31": "math-ap", "32": "math-cv", "33": "math-ca", "34": "math-ca", "35": "math-ap", "37": "math-ds",
             "39": "math-ca", "40": "math-ca", "41": "math-ca", "42": "math-ca", "43": "math-ca", "44": "math-ca",
             "45": "math-fa", "46": "math-fa", "47": "math-fa", "49": "math-oc", "51": "math-mg", "52": "math-mg",
             "53": "math-dg", "54": "math-gn", "55": "math-at", "57": "math-gt", "58": "math-dg", "60": "math-pr",
             "62": "math-st", "65": "math-na", "70": "math-ph", "74": "math-ph", "76": "math-ph", "78": "math-ph",
             "80": "math-ph", "81": "math-ph", "82": "math-ph", "83": "math-ph", "90": "math-oc", "93": "math-oc",
             "94": "math-it"}
UDC2ARXIV = [("517.9", "math-ap"), ("517.5", "math-ca"), ("517.98", "math-fa"), ("517", "math-ca"),
             ("519.2", "math-pr"), ("519.6", "math-na"), ("519.8", "math-oc"), ("519.1", "math-co"),
             ("512", "math-ra"), ("511", "math-nt"), ("514", "math-dg"), ("510", "math-lo"), ("53", "math-ph")]


@tool(name="classify_topics", module="m10", requires=("extract_metadata",),
      produces=("scilibreco:hasSubjectArea",))
def classify_topics(doc: Doc, ctx: Context) -> Facts:
    """Разделы arXiv, к которым относится статья (scilibreco:hasSubjectArea), по кодам MSC
    или УДК из метаданных."""
    facts = Facts("classify_topics", doc.doc_id)
    meta = ctx.facts("extract_metadata").relations
    areas = {}
    for r in meta:
        if r.pred == "moc:hasMSCCode":
            a = MSC2ARXIV.get(str(r.obj)[:2])
            if a:
                areas[a] = max(areas.get(a, 0), 0.8)
    if not areas:
        for r in meta:
            if r.pred == "scilibreco:hasUDC":
                for prefix, a in UDC2ARXIV:
                    if str(r.obj).startswith(prefix):
                        areas[a] = 0.6
                        break
    for a, conf in areas.items():
        facts.add(doc.iri, "scilibreco:hasSubjectArea", SLR + "subject_" + a, confidence=conf)
    return facts
