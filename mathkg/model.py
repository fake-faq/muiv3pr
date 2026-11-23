"""Типы данных практики: статья после base (Doc), результат модуля (Facts), контекст запуска.

Контракт общий для всех модулей. Модуль получает Doc и Context и возвращает Facts:
фрагменты (отрезки текста статьи с классом онтологии) и связи (тройки). Позиции —
в символах Doc.text, конец не включается.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .common import doc_iri, MP

METHODS = ("rule", "model", "llm", "manual")


@dataclass
class Span:
    """Отрезок текста статьи: абзац, предложение, страница, отметка шрифта."""
    start: int
    end: int
    page: int = 0                 # номер страницы PDF, с 1
    kind: str = ""                # 'bold' | 'italic' | 'math' для отметок шрифта


@dataclass
class Formula:
    """Выключная формула, найденная base: текст из PDF, номер, координаты, LaTeX (если распознан)."""
    i: int
    start: int
    end: int
    page: int
    bbox: List[float]             # [x0, y0, x1, y1] в координатах страницы PDF
    text: str
    number: Optional[str] = None  # '2.3' для формулы с номером (2.3)
    latex: Optional[str] = None   # заполняет base.recognize_formulas


@dataclass
class Link:
    """Внутренняя гиперссылка PDF (статьи из LaTeX с hyperref): откуда и куда указывает."""
    start: int                    # позиция текста ссылки в Doc.text (-1, если не сопоставлена)
    end: int
    page: int
    target: str                   # именованная цель: 'cite.Diestel75', 'equation.2.3', 'theorem.2.1'
    target_page: int = 0
    target_y: float = 0.0


@dataclass
class Doc:
    """Статья после общей основы base. Все модули работают только с этим представлением."""
    doc_id: str
    lang: str
    text: str
    pages: List[Span] = field(default_factory=list)
    paragraphs: List[Span] = field(default_factory=list)
    sentences: List[Span] = field(default_factory=list)
    styled: List[Span] = field(default_factory=list)
    formulas: List[Formula] = field(default_factory=list)
    links: List[Link] = field(default_factory=list)
    footnotes: List[str] = field(default_factory=list)
    first_page_lines: List[Dict[str, Any]] = field(default_factory=list)  # строки 1-й страницы со шрифтом
    meta: Dict[str, Any] = field(default_factory=dict)  # file, n_pages, quality, pdf_metadata

    @property
    def iri(self) -> str:
        return doc_iri(self.doc_id)

    def page_of(self, pos: int) -> int:
        for p in self.pages:
            if p.start <= pos < p.end:
                return p.page
        return self.pages[-1].page if self.pages else 0

    def paragraph_of(self, pos: int) -> Optional[int]:
        """Номер абзаца (с 0), в который попадает позиция."""
        for i, p in enumerate(self.paragraphs):
            if p.start <= pos < p.end:
                return i
        return None

    def style_at(self, start: int, end: int) -> set:
        """Отметки шрифта, которые пересекаются с отрезком [start, end)."""
        return {s.kind for s in self.styled if s.start < end and start < s.end}

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @staticmethod
    def from_json(s: str) -> "Doc":
        d = json.loads(s)
        d["pages"] = [Span(**x) for x in d["pages"]]
        d["paragraphs"] = [Span(**x) for x in d["paragraphs"]]
        d["sentences"] = [Span(**x) for x in d["sentences"]]
        d["styled"] = [Span(**x) for x in d["styled"]]
        d["formulas"] = [Formula(**x) for x in d["formulas"]]
        d["links"] = [Link(**x) for x in d["links"]]
        return Doc(**d)


def paragraph_id(i: int) -> str:
    """Локальный идентификатор абзаца base: 'p-0007'. Абзацы нумерует base, модули их не создают."""
    return f"p-{i + 1:04d}"


def formula_id(i: int) -> str:
    """Локальный идентификатор выключной формулы base: 'f-0003'."""
    return f"f-{i + 1:04d}"


@dataclass
class Fragment:
    """Отрезок текста статьи с классом онтологии (moc:Theorem, moc:Proof, moc:Title, ...).

    id — локальный идентификатор внутри статьи; префикс задаёт модуль (st-, def-, prf-, sec-,
    ref-, ...), номер — порядок в статье. Полный IRI: doc_iri(doc_id) + '/' + id.
    """
    id: str
    cls: str
    start: int
    end: int
    method: str = "rule"
    confidence: float = 1.0
    number: Optional[str] = None      # '2.1'
    label: Optional[str] = None       # 'Теорема 2.1 (Банах)'
    page: int = 0
    attrs: Dict[str, Any] = field(default_factory=dict)  # литералы: {'moc:hasLatexSource': '...'}


@dataclass
class Relation:
    """Тройка. subj и obj — локальный id фрагмента статьи, полный IRI или имя с префиксом (mp:, moc:).
    Для литерала obj — значение, literal=True. start/end — где в тексте найдено основание связи."""
    subj: str
    pred: str
    obj: Any
    literal: bool = False
    datatype: Optional[str] = None    # 'xsd:integer', 'xsd:boolean', ...; None — строка
    method: str = "rule"
    confidence: float = 1.0
    start: int = -1
    end: int = -1


@dataclass
class Facts:
    """Результат одного модуля на одной статье."""
    module: str
    doc_id: str
    fragments: List[Fragment] = field(default_factory=list)
    relations: List[Relation] = field(default_factory=list)
    sec: float = 0.0

    def add_fragment(self, **kw) -> Fragment:
        f = Fragment(**kw)
        self.fragments.append(f)
        return f

    def add(self, subj: str, pred: str, obj: Any, **kw) -> Relation:
        r = Relation(subj=subj, pred=pred, obj=obj, **kw)
        self.relations.append(r)
        return r

    def find(self, cls: Optional[str] = None) -> List[Fragment]:
        return [f for f in self.fragments if cls is None or f.cls == cls]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @staticmethod
    def from_json(s: str) -> "Facts":
        d = json.loads(s)
        d["fragments"] = [Fragment(**x) for x in d["fragments"]]
        d["relations"] = [Relation(**x) for x in d["relations"]]
        return Facts(**d)


def resolve(doc_id: str, ref: str) -> str:
    """Локальный id -> полный IRI; полные IRI и имена с префиксом остаются как есть."""
    if ref.startswith("http") or ":" in ref:
        return ref
    return doc_iri(doc_id) + "/" + ref


@dataclass
class Context:
    """Доступ модуля к результатам модулей, от которых он зависит (на той же статье)."""
    doc: Doc
    upstream: Dict[str, Facts] = field(default_factory=dict)

    def facts(self, tool_name: str) -> Facts:
        if tool_name not in self.upstream:
            raise KeyError(f"модуль {tool_name} не объявлен в requires или не запускался")
        return self.upstream[tool_name]
