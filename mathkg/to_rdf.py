"""Перевод статьи и результатов модулей в RDF (N-Triples) по модели данных практики.

    from mathkg.to_rdf import corpus_to_ntriples
    corpus_to_ntriples(base_dir, facts_dir, out_path=WORK + "/graph.nt", student="student-07")

N-Triples пишется построчно по статьям, поэтому граф в миллион троек не держится в памяти.
Для проверок и запросов файл загружается в pyoxigraph (быстро) или в rdflib (медленнее).
"""
from __future__ import annotations

import datetime
import json
from typing import Dict, Iterable, List, Optional

from . import registry
from .base import read_doc
from .common import MP, slug
from .model import Doc, Facts, formula_id, paragraph_id, resolve
from .pipeline import _done_ids, load_facts

NS = {"moc": "https://w3id.org/mocassinplus/ontology#",
      "scilibreco": "https://scilib.ai/ontology/scilibreco#",
      "mp": MP,
      "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
      "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
      "xsd": "http://www.w3.org/2001/XMLSchema#"}


def iri(name: str) -> str:
    """'moc:Theorem' -> полный IRI; полный IRI — без изменений."""
    if name.startswith("http"):
        return name
    p, local = name.split(":", 1)
    return NS[p] + local


def _esc(s: str) -> str:
    return (str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            .replace("\r", "\\r"))


def _lit(value, datatype: Optional[str] = None, lang: Optional[str] = None) -> str:
    if isinstance(value, bool):
        return f'"{str(value).lower()}"^^<{NS["xsd"]}boolean>'
    if isinstance(value, int) and datatype is None:
        return f'"{value}"^^<{NS["xsd"]}integer>'
    if isinstance(value, float) and datatype is None:
        return f'"{value}"^^<{NS["xsd"]}decimal>'
    if lang:
        return f'"{_esc(value)}"@{lang}'
    if datatype:
        return f'"{_esc(value)}"^^<{iri(datatype)}>'
    return f'"{_esc(value)}"'


def _t(s: str, p: str, o: str) -> str:
    return f"<{s}> <{iri(p)}> {o} .\n"


class Runs:
    """Запуски извлечения moc:ExtractionRun: по одному на модуль и метод."""
    def __init__(self, student: str):
        self.student, self.now = student, datetime.datetime.now()
        self.seen: Dict[str, str] = {}
        self.lines: List[str] = []

    def get(self, module: str, method: str) -> str:
        key = f"{module}-{method}"
        if key not in self.seen:
            run = MP + f"run/{self.student}/{slug(key)}-{self.now:%Y%m%d%H%M}"
            tool = MP + "tool/" + slug(module)
            self.seen[key] = run
            self.lines += [_t(run, "rdf:type", f"<{iri('moc:ExtractionRun')}>"),
                           _t(run, "moc:hasExtractionMethod", _lit(method)),
                           _t(run, "moc:wasExtractedBy", f"<{tool}>"),
                           _t(run, "moc:wasExtractedAt", _lit(self.now.isoformat(timespec="seconds"), "xsd:dateTime")),
                           _t(tool, "rdf:type", f"<{iri('moc:ExtractionTool')}>"),
                           _t(tool, "rdfs:label", _lit(module))]
        return self.seen[key]


def referenced_paragraphs(doc: Doc, facts_list: List[Facts]) -> set:
    """Абзацы, на которые ссылается хоть один фрагмент или связь модулей. Только они попадают в
    граф: текст остальных агент берёт из base (get_article_text), а граф набора остаётся в
    пределах 1–2 млн троек."""
    used = set()
    for facts in facts_list:
        for r in facts.relations:
            for v in (r.subj, r.obj):
                if isinstance(v, str) and v.startswith("p-"):
                    used.add(v)
    for f in doc.formulas:                     # абзац, в котором стоит выключная формула
        k = doc.paragraph_of(max(0, f.start - 3))
        if k is not None:
            used.add(paragraph_id(k))
    return used


def doc_triples(doc: Doc, runs: Runs, keep: Optional[set] = None) -> Iterable[str]:
    """Тройки, которые даёт base: статья, исходный PDF, абзацы (из keep или все), выключные формулы."""
    d = doc.iri
    yield _t(d, "rdf:type", f"<{iri('moc:Document')}>")
    yield _t(d, "scilibreco:hasLanguage", _lit(doc.lang))
    src = MP + f"source/{doc.doc_id}"
    yield _t(src, "rdf:type", f"<{iri('moc:SourceDocument')}>")
    run = runs.get("base", "rule")
    # происхождение абзацев и формул base записано один раз на статью; обратные свойства
    # (moc:hasSegment к moc:segmentOf, moc:hasPart к moc:isPartOf) не пишутся — их выводит GraphDB
    yield _t(d, "moc:generatedBy", f"<{run}>")
    yield _t(d, "moc:extractedFrom", f"<{src}>")
    for i, p in enumerate(doc.paragraphs):
        if keep is not None and paragraph_id(i) not in keep:
            continue
        s = f"{d}/{paragraph_id(i)}"
        yield _t(s, "rdf:type", f"<{iri('moc:Paragraph')}>")
        yield _t(s, "moc:hasText", _lit(doc.text[p.start:p.end]))
        yield _t(s, "moc:hasOrderIndex", _lit(i + 1))
        yield _t(s, "moc:hasStartOffset", _lit(p.start, "xsd:nonNegativeInteger"))
        yield _t(s, "moc:hasEndOffset", _lit(p.end, "xsd:nonNegativeInteger"))
        yield _t(s, "moc:hasPageNumber", _lit(max(1, p.page), "xsd:positiveInteger"))
        yield _t(s, "moc:segmentOf", f"<{d}>")
    for f in doc.formulas:
        s = f"{d}/{formula_id(f.i)}"
        yield _t(s, "rdf:type", f"<{iri('moc:Formula')}>")
        yield _t(s, "moc:hasPlainText", _lit(f.text))
        yield _t(s, "scilibreco:isDisplayFormula", _lit(True))
        yield _t(s, "moc:hasPageNumber", _lit(max(1, f.page), "xsd:positiveInteger"))
        if f.number:
            yield _t(s, "moc:hasNumber", _lit(f.number))
        if f.latex:
            yield _t(s, "moc:hasLatexSource", _lit(f.latex))
        yield _t(d, "scilibreco:containsFormula", f"<{s}>")
        k = doc.paragraph_of(max(0, f.start - 3))
        if k is not None:
            yield _t(s, "moc:occursIn", f"<{d}/{paragraph_id(k)}>")


def facts_triples(doc: Doc, facts: Facts, runs: Runs) -> Iterable[str]:
    """Тройки результата одного модуля: фрагменты с происхождением и связи."""
    d = doc.iri
    for f in facts.fragments:
        s = resolve(doc.doc_id, f.id)
        yield _t(s, "rdf:type", f"<{iri(f.cls)}>")
        yield _t(s, "moc:hasText", _lit(doc.text[f.start:f.end]))
        yield _t(s, "moc:hasStartOffset", _lit(f.start, "xsd:nonNegativeInteger"))
        yield _t(s, "moc:hasEndOffset", _lit(f.end, "xsd:nonNegativeInteger"))
        yield _t(s, "moc:hasPageNumber", _lit(max(1, f.page or doc.page_of(f.start)), "xsd:positiveInteger"))
        if f.number:
            yield _t(s, "moc:hasNumber", _lit(f.number))
        if f.label:
            yield _t(s, "moc:hasLabel", _lit(f.label))
        yield _t(s, "moc:hasExtractionMethod", _lit(f.method))
        yield _t(s, "moc:hasConfidence", _lit(round(float(f.confidence), 3)))
        yield _t(s, "moc:generatedBy", f"<{runs.get(facts.module, f.method)}>")
        yield _t(s, "moc:segmentOf", f"<{d}>")
        for p, v in f.attrs.items():
            yield _t(s, p, _lit(v))
    for r in facts.relations:
        s = resolve(doc.doc_id, r.subj)
        o = _lit(r.obj, r.datatype) if r.literal else f"<{iri(resolve(doc.doc_id, r.obj))}>"
        yield _t(iri(s), r.pred, o)


def corpus_to_ntriples(base_dir: str, facts_dir: str, out_path: str, student: str,
                       names: Optional[List[str]] = None, doc_ids: Optional[List[str]] = None,
                       all_paragraphs: bool = False) -> int:
    """Пишет граф набора в N-Triples. Возвращает число троек. По умолчанию в граф попадают только
    абзацы, на которые ссылаются модули; all_paragraphs=True записывает все."""
    names = names or registry.extraction_order()
    runs = Runs(student)
    n = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for doc_id in doc_ids or _done_ids(base_dir):
            doc = read_doc(base_dir, doc_id)
            facts_list = [f for f in (load_facts(facts_dir, name, doc_id) for name in names) if f]
            keep = None if all_paragraphs else referenced_paragraphs(doc, facts_list)
            for line in doc_triples(doc, runs, keep):
                out.write(line); n += 1
            for facts in facts_list:
                for line in facts_triples(doc, facts, runs):
                    out.write(line); n += 1
        for line in runs.lines:
            out.write(line); n += 1
    return n
