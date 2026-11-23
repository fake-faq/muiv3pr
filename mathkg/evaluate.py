"""Оценка модуля по ручному эталону на общем тестовом наборе.

Эталон студент записывает в gold/mNN.jsonl, по строке на объект. Позиции вручную не считаются:
достаточно дословной цитаты начала фрагмента (30–80 символов), программа находит её в тексте.

    {"doc_id": "A04571", "cls": "moc:Theorem", "number": "2.1", "quote": "Теорема 2.1. Пусть X — рефлексивное"}
    {"doc_id": "A04571", "pred": "moc:proves", "subj_quote": "Доказательство. По лемме 1.3",
     "obj_quote": "Теорема 2.1. Пусть X"}

Связь-литерал (DOI, год, УДК, имя формулы) записывается значением; subj — "doc" (сама статья),
локальный id или цитата subj_quote:

    {"doc_id": "A04571", "pred": "moc:hasDOI", "subj": "doc", "value": "10.1134/S0001434619010012"}

Фрагмент считается найденным, если модуль выдал фрагмент того же класса, начало которого лежит
не дальше tol символов от начала цитаты. Связь считается найденной, если модуль выдал связь с тем
же свойством между фрагментами, которые содержат начала цитат субъекта и объекта.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Dict, List, Optional

from . import registry
from .base import read_doc
from .model import Doc, Fragment, formula_id, paragraph_id
from .pipeline import load_facts


def read_gold(path: str) -> List[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def locate(doc: Doc, quote: str) -> int:
    """Позиция цитаты в тексте статьи; пробелы и переносы строк не учитываются."""
    k = doc.text.find(quote)
    if k >= 0:
        return k
    flat, idx = [], []
    for i, c in enumerate(doc.text):
        if not c.isspace():
            flat.append(c); idx.append(i)
    q = "".join(quote.split())
    k = "".join(flat).find(q)
    return idx[k] if k >= 0 else -1


def _spans(doc: Doc, frags: List[Fragment]) -> Dict[str, tuple]:
    out = {f.id: (f.start, f.end) for f in frags}
    out.update({paragraph_id(i): (p.start, p.end) for i, p in enumerate(doc.paragraphs)})
    out.update({formula_id(f.i): (f.start, f.end) for f in doc.formulas})
    return out


def _prf(tp: int, n_pred: int, n_gold: int) -> dict:
    p = tp / n_pred if n_pred else 0.0
    r = tp / n_gold if n_gold else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3),
            "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0, "tp": tp, "pred": n_pred, "gold": n_gold}


def evaluate(tool_name: str, gold_path: str, base_dir: str, out_dir: str, tol: int = 15) -> dict:
    """Точность, полнота и F1 модуля по классам фрагментов и по свойствам связей на статьях
    эталона. Возвращает также списки пропусков и ложных находок для разбора ошибок."""
    gold = read_gold(gold_path)
    spec = registry.REGISTRY[tool_name]
    by_doc = defaultdict(list)
    for g in gold:
        by_doc[g["doc_id"]].append(g)
    stats = defaultdict(lambda: [0, 0, 0])          # ключ -> [tp, pred, gold]
    missed, extra, unresolved = [], [], []
    for doc_id, items in by_doc.items():
        doc = read_doc(base_dir, doc_id)
        facts = load_facts(out_dir, tool_name, doc_id)
        frags = facts.fragments if facts else []
        rels = facts.relations if facts else []
        upstream = [f for r in spec.requires for f in (load_facts(out_dir, r, doc_id) or type("E", (), {"fragments": []})).fragments]
        groups = [{f.id: (f.start, f.end) for f in frags},          # сначала фрагменты модуля,
                  {f.id: (f.start, f.end) for f in upstream},       # затем модулей-зависимостей,
                  _spans(doc, [])]                                  # затем абзацы и формулы base

        def holder(pos: int) -> Optional[str]:
            if pos < 0:
                return None
            for spans in groups:
                best = [k for k, (a, b) in spans.items() if a - tol <= pos < b]
                if best:
                    return min(best, key=lambda k: spans[k][1] - spans[k][0])
            return None

        # фрагменты
        gold_f = [g for g in items if "cls" in g]
        matched = set()
        for g in gold_f:
            pos = locate(doc, g["quote"])
            if pos < 0:
                unresolved.append(g); continue
            stats[g["cls"]][2] += 1
            hit = next((f for f in frags if f.cls == g["cls"] and abs(f.start - pos) <= tol
                        and f.id not in matched), None)
            if hit:
                matched.add(hit.id); stats[g["cls"]][0] += 1
            else:
                missed.append(g)
        gold_classes = {g["cls"] for g in gold_f}
        for f in frags:
            if f.cls in gold_classes:
                stats[f.cls][1] += 1
                if f.id not in matched:
                    extra.append({"doc_id": doc_id, "cls": f.cls, "text": doc.text[f.start:f.start + 80]})
        # связи
        # связи-литералы: совпадение свойства, субъекта и значения (без учёта регистра и пробелов)
        def norm(v):
            return " ".join(str(v).lower().split())
        gold_l = [g for g in items if "pred" in g and "value" in g]
        lit_preds = {g["pred"] for g in gold_l}
        def subj_of(g):
            if g.get("subj") == "doc":
                return doc.iri
            return holder(locate(doc, g["subj_quote"])) if "subj_quote" in g else g.get("subj")
        gold_lk = {(subj_of(g), g["pred"], norm(g["value"])) for g in gold_l}
        pred_lk = {(doc.iri if r.subj == doc.iri else r.subj, r.pred, norm(r.obj))
                   for r in rels if r.literal and r.pred in lit_preds}
        for p in lit_preds:
            st = stats["→" + p]
            st[0] += len({k for k in gold_lk & pred_lk if k[1] == p})
            st[1] += len({k for k in pred_lk if k[1] == p}); st[2] += len({k for k in gold_lk if k[1] == p})
        missed += [{"doc_id": doc_id, "literal": k} for k in gold_lk - pred_lk]
        gold_r = [g for g in items if "pred" in g and "value" not in g]
        gold_keys = set()
        for g in gold_r:
            s = holder(locate(doc, g["subj_quote"])) if "subj_quote" in g else g.get("subj")
            o = holder(locate(doc, g["obj_quote"])) if "obj_quote" in g else g.get("obj")
            if not s or not o:
                unresolved.append(g); continue
            gold_keys.add((s, g["pred"], o))
        preds = {g["pred"] for g in gold_r}
        pred_keys = {(r.subj, r.pred, r.obj) for r in rels if r.pred in preds and not r.literal}
        for p in preds:
            tp = len({k for k in gold_keys & pred_keys if k[1] == p})
            st = stats["→" + p]
            st[0] += tp; st[1] += len({k for k in pred_keys if k[1] == p}); st[2] += len({k for k in gold_keys if k[1] == p})
        missed += [{"doc_id": doc_id, "relation": k} for k in gold_keys - pred_keys]
    return {"tool": tool_name, "by_key": {k: _prf(*v) for k, v in sorted(stats.items())},
            "missed": missed, "extra": extra, "unresolved_quotes": unresolved}
