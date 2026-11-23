"""Модуль 9. Понятия и именованные результаты — базовая версия (правила). Зависит от модулей 1, 4.

Словарь понятий статьи: термины определений (модуль 4) и ключевые слова (модуль 1). Упоминание
понятия ищется по леммам: ключ common.normalize_name для всех словосочетаний из 1–4 слов абзаца
сравнивается с ключами словаря. Именованные результаты («теорема Банаха — Штейнгауза»,
«неравенство Гёльдера», «Cauchy–Schwarz inequality») получают общий IRI mp:statement/….
Студент улучшает: словарь всего набора (а не одной статьи), отсев слишком общих понятий,
связь русских и английских названий одного понятия по векторам, модель NER для терминов.
"""
from __future__ import annotations

import re

from ..common import normalize_name, statement_iri
from ..model import Context, Doc, Facts, paragraph_id
from ..registry import tool

NAMED_RU = re.compile(r"\b(теорем[аеуыой]*|лемм[аеуыой]*|неравенств[оауе]?|формул[аеуыой]*|уравнени[еяю]|"
                      r"принцип[аеу]?|гипотез[аеуыой]*|критери[йяю]|тождеств[оау]?)\s+"
                      r"((?:[А-ЯЁ][а-яё]+(?:\s*[–—-]\s*|\s+и\s+)?){1,3})")
NAMED_EN = re.compile(r"\b((?:[A-Z][a-z]+(?:[–—-]|\s+and\s+)?){1,3})(?:'s)?\s+"
                      r"(theorem|lemma|inequality|formula|equation|principle|conjecture|criterion|identity)\b")
WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+")


@tool(name="extract_concepts", module="m09", requires=("extract_metadata", "extract_definitions"),
      produces=("moc:mentionsConcept", "moc:mentions", "moc:MathematicalStatement"))
def extract_concepts(doc: Doc, ctx: Context) -> Facts:
    """Упоминания понятий статьи в абзацах (moc:mentionsConcept) и именованные результаты
    («теорема Банаха — Штейнгауза») с общим IRI mp:statement/…"""
    facts = Facts("extract_concepts", doc.doc_id)
    vocab = {}                                                   # ключ -> IRI понятия
    for up in ("extract_definitions", "extract_metadata"):
        for r in ctx.facts(up).relations:
            if r.pred == "scilibreco:hasNormalizedName":
                vocab[r.obj] = r.subj
    for r in ctx.facts("extract_metadata").relations:            # ключевые слова как понятия
        if r.pred == "scilibreco:hasName" and "/keyword/" in r.subj:
            vocab.setdefault(normalize_name(r.obj), r.subj)
    maxlen = max((len(k.split()) for k in vocab), default=0)
    for i, p in enumerate(doc.paragraphs):
        t = doc.text[p.start:p.end]
        if vocab:
            words = WORD.findall(t)
            found = set()
            for n in range(1, min(4, maxlen) + 1):
                for k in range(len(words) - n + 1):
                    key = normalize_name(" ".join(words[k:k + n]))
                    if key in vocab:
                        found.add(vocab[key])
            for c in found:
                if "/keyword/" not in c:
                    facts.add(paragraph_id(i), "moc:mentionsConcept", c, confidence=0.7)
        for m in list(NAMED_RU.finditer(t)) + list(NAMED_EN.finditer(t)):
            name = " ".join(m.group(0).split())
            s = statement_iri(name)
            facts.add(s, "rdf:type", "moc:MathematicalStatement")
            facts.add(s, "rdfs:label", name, literal=True)
            facts.add(paragraph_id(i), "moc:mentions", s, start=p.start + m.start(), end=p.start + m.end(),
                      confidence=0.7)
    return facts
