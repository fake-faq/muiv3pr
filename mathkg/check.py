"""Проверка контракта модуля. Её запускает сборка на общем тестовом наборе, и её же студент
запускает перед сдачей:

    from mathkg.check import check_module
    report = check_module("extract_proofs", base_dir, out_dir, ontology_files)
    print(report["errors"][:10], report["counts"])

Модуль проходит сборку, если ошибок нет, а среднее время на статью не больше limit_ms.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import Dict, List, Optional, Set

from . import registry
from .base import read_doc
from .model import METHODS, formula_id, paragraph_id
from .pipeline import _done_ids, load_facts

PREFIX = {"moc": "https://w3id.org/mocassinplus/ontology#",
          "scilibreco": "https://scilib.ai/ontology/scilibreco#"}


def ontology_terms(ttl_files: List[str]) -> Dict[str, Set[str]]:
    """Классы и свойства, объявленные в онтологиях (по строкам вида 'moc:Theorem a owl:Class')."""
    classes, props = set(), set()
    for p in ttl_files:
        with open(p, encoding="utf-8") as f:
            for m in re.finditer(r"^((?:moc|scilibreco):\w+)\s+a\s+([^;.]+)", f.read(), re.M):
                kinds = m.group(2)
                if "owl:Class" in kinds:
                    classes.add(m.group(1))
                if "Property" in kinds:
                    props.add(m.group(1))
    return {"classes": classes, "properties": props}


ALLOWED_PRED = {"rdf:type", "rdfs:label"}


def check_facts(facts, doc, terms, known_ids: Set[str]) -> List[str]:
    """Ошибки одного результата модуля на одной статье."""
    err = []
    n = len(doc.text)
    ids = Counter(f.id for f in facts.fragments)
    for i, c in ids.items():
        if c > 1:
            err.append(f"повтор id {i}")
    for f in facts.fragments:
        if not (0 <= f.start < f.end <= n):
            err.append(f"{f.id}: позиции [{f.start},{f.end}) вне текста длиной {n}")
        if f.cls not in terms["classes"]:
            err.append(f"{f.id}: класса {f.cls} нет в онтологии")
        if f.method not in METHODS:
            err.append(f"{f.id}: метод {f.method}")
        if not (0 <= f.confidence <= 1):
            err.append(f"{f.id}: уверенность {f.confidence}")
        if ":" in f.id or "/" in f.id:
            err.append(f"{f.id}: локальный id не должен содержать ':' и '/'")
    local = known_ids | set(ids)
    for r in facts.relations:
        if r.pred not in terms["properties"] and r.pred not in ALLOWED_PRED:
            err.append(f"связь {r.subj} {r.pred}: свойства нет в онтологии")
        for side in ("subj",) + (() if r.literal else ("obj",)):
            v = getattr(r, side)
            if not isinstance(v, str):
                err.append(f"связь {r.pred}: {side} должен быть строкой")
            elif not (v.startswith("http") or ":" in v) and v not in local:
                err.append(f"связь {r.pred}: {side}={v} — нет такого фрагмента")
        if r.pred == "rdf:type" and r.obj not in terms["classes"]:
            err.append(f"rdf:type {r.obj}: класса нет в онтологии")
        if r.start >= 0 and not (r.start < r.end <= n):
            err.append(f"связь {r.pred}: позиции основания вне текста")
    return err


def check_module(name: str, base_dir: str, out_dir: str, ontology_files: List[str],
                 doc_ids: Optional[List[str]] = None, limit_ms: float = 2000) -> dict:
    """Проверяет результаты модуля name на статьях (по умолчанию — на всех из base_dir)."""
    terms = ontology_terms(ontology_files)
    spec = registry.REGISTRY[name]
    errors, counts, ms, docs = [], Counter(), 0.0, 0
    for doc_id in doc_ids or _done_ids(base_dir):
        facts = load_facts(out_dir, name, doc_id)
        if facts is None:
            errors.append(f"{doc_id}: нет результата"); continue
        doc = read_doc(base_dir, doc_id)
        known = {"title", "abstract", "keywords", "bib"}
        known |= {paragraph_id(i) for i in range(len(doc.paragraphs))}
        known |= {formula_id(i) for i in range(len(doc.formulas))}
        for r in spec.requires:
            up = load_facts(out_dir, r, doc_id)
            if up:
                known |= {f.id for f in up.fragments}
        errors += [f"{doc_id}: {e}" for e in check_facts(facts, doc, terms, known)]
        counts.update(f.cls for f in facts.fragments)
        counts.update("→" + r.pred for r in facts.relations)
        ms += facts.sec * 1000; docs += 1
    ms_per_doc = ms / max(1, docs)
    if ms_per_doc > limit_ms:
        errors.append(f"среднее время {ms_per_doc:.0f} мс на статью больше {limit_ms:.0f} мс")
    return {"module": name, "docs": docs, "errors": errors, "counts": dict(counts),
            "ms_per_doc": round(ms_per_doc, 1), "passed": not errors}


def build_report(base_dir: str, out_dir: str, ontology_files: List[str],
                 doc_ids: Optional[List[str]] = None, limit_ms: float = 2000) -> List[dict]:
    """Сборка: проверка всех зарегистрированных модулей на общем тестовом наборе. Возвращает
    строки таблицы: модуль, автор, слой, прошёл ли, ошибки, число фрагментов и связей, время."""
    rows = []
    for n in registry.extraction_order():
        spec = registry.REGISTRY[n]
        r = check_module(n, base_dir, out_dir, ontology_files, doc_ids, limit_ms)
        rows.append({"module": spec.module, "tool": n, "author": spec.author or "базовая версия",
                     "layer": spec.layer, "passed": r["passed"], "errors": len(r["errors"]),
                     "fragments": sum(v for k, v in r["counts"].items() if not k.startswith("→")),
                     "relations": sum(v for k, v in r["counts"].items() if k.startswith("→")),
                     "ms_per_doc": r["ms_per_doc"], "first_errors": r["errors"][:3]})
    return rows
