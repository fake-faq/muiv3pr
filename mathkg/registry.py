"""Реестр инструментов практики.

Каждый модуль извлечения объявляется декоратором @tool. Реестр знает зависимости модулей,
вычисляет порядок запуска и превращает каждый модуль в инструмент агента: функцию с одним
параметром doc_id, описанием (docstring) и JSON-схемой параметров.

    @tool(name="extract_proofs", module="m07", requires=("extract_statements",),
          produces=("moc:Proof", "moc:proves"))
    def extract_proofs(doc: Doc, ctx: Context) -> Facts:
        '''Находит доказательства и связывает каждое с утверждением, которое оно доказывает.'''

Инструменты запросов к графу и индексу (этапы 8–11) объявляются декоратором @query_tool:
у них произвольные параметры, схема строится по аннотациям типов.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .model import Context, Doc, Facts


@dataclass
class ToolSpec:
    name: str
    module: str
    func: Callable
    requires: Tuple[str, ...] = ()
    produces: Tuple[str, ...] = ()
    layer: int = 1
    kind: str = "extract"            # 'extract' | 'query'
    author: str = ""
    params: Dict[str, dict] = field(default_factory=dict)

    @property
    def description(self) -> str:
        return inspect.getdoc(self.func) or ""


REGISTRY: Dict[str, ToolSpec] = {}


def tool(name: str, module: str, requires: Tuple[str, ...] = (), produces: Tuple[str, ...] = (),
         author: str = ""):
    """Объявляет модуль извлечения. Повторное объявление того же name заменяет прежнее:
    так студенческая версия модуля заменяет базовую."""
    def deco(func: Callable[[Doc, Context], Facts]):
        REGISTRY[name] = ToolSpec(name=name, module=module, func=func, requires=tuple(requires),
                                  produces=tuple(produces), author=author,
                                  params={"doc_id": {"type": "string",
                                                     "description": "идентификатор статьи набора, например A04571"}})
        return func
    return deco


_PY2JSON = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


def query_tool(name: str, author: str = ""):
    """Объявляет инструмент запросов (поиск, SPARQL, соседи в графе). Параметры и их типы
    берутся из сигнатуры функции, описание — из docstring."""
    def deco(func: Callable):
        params = {}
        hints = getattr(func, "__annotations__", {})
        for p in inspect.signature(func).parameters.values():
            t = hints.get(p.name, str)
            t = {"str": str, "int": int, "float": float, "bool": bool}.get(t, t) if isinstance(t, str) else t
            params[p.name] = {"type": _PY2JSON.get(t, "string")}
            if p.default is not inspect.Parameter.empty:
                params[p.name]["default"] = p.default
        REGISTRY[name] = ToolSpec(name=name, module="query", func=func, kind="query", author=author,
                                  params=params)
        return func
    return deco


def reset(kind: str = "extract") -> None:
    """Удаляет из реестра инструменты указанного вида (по умолчанию — модули извлечения)."""
    for name in [n for n, s in REGISTRY.items() if s.kind == kind]:
        del REGISTRY[name]


def load_modules(package: str = "mathkg.modules") -> List[str]:
    """Импортирует (повторно — перезагружает) все модули пакета m01_….py … m10_….py, чтобы
    они зарегистрировались."""
    import sys
    pkg = importlib.import_module(package)
    names = []
    for m in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        full = f"{package}.{m.name}"
        if full in sys.modules:
            importlib.reload(sys.modules[full])
        else:
            importlib.import_module(full)
        names.append(m.name)
    return names


def extraction_order(names: Optional[List[str]] = None) -> List[str]:
    """Порядок запуска модулей извлечения: каждый после тех, от которых зависит.
    Заодно вычисляет слой модуля (1 — зависит только от base)."""
    specs = {n: s for n, s in REGISTRY.items() if s.kind == "extract"}
    wanted = set(names or specs)
    for n in list(wanted):                     # зависимости добавляются автоматически
        stack = [n]
        while stack:
            for r in specs[stack.pop()].requires:
                if r not in specs:
                    raise KeyError(f"{n} зависит от незарегистрированного модуля {r}")
                if r not in wanted:
                    wanted.add(r); stack.append(r)
    order, done = [], set()

    def visit(n, path=()):
        if n in done:
            return
        if n in path:
            raise ValueError("циклическая зависимость: " + " -> ".join(path + (n,)))
        for r in specs[n].requires:
            visit(r, path + (n,))
        specs[n].layer = 1 + max((specs[r].layer for r in specs[n].requires), default=0)
        done.add(n); order.append(n)

    for n in sorted(wanted, key=lambda x: specs[x].module):
        visit(n)
    return order


def run_on_doc(doc: Doc, names: Optional[List[str]] = None,
               cache: Optional[Dict[str, Facts]] = None) -> Dict[str, Facts]:
    """Запускает модули на одной статье в порядке зависимостей. cache — уже готовые результаты
    (например, прочитанные с диска): такие модули повторно не запускаются."""
    results: Dict[str, Facts] = dict(cache or {})
    for n in extraction_order(names):
        if n in results:
            continue
        spec = REGISTRY[n]
        ctx = Context(doc=doc, upstream={r: results[r] for r in spec.requires})
        t0 = time.time()
        facts = spec.func(doc, ctx)
        facts.module, facts.doc_id, facts.sec = n, doc.doc_id, round(time.time() - t0, 3)
        results[n] = facts
    return results


def compact(doc: Doc, facts: Facts, max_fragments: int = 25, max_relations: int = 40,
            text_chars: int = 250) -> dict:
    """Результат модуля в виде, удобном агенту: полные IRI, начало текста фрагмента, ограниченное
    число фрагментов и связей (их общее число — в полях n_fragments, n_relations)."""
    from .model import resolve
    return {"doc_id": doc.doc_id,
            "n_fragments": len(facts.fragments), "n_relations": len(facts.relations),
            "fragments": [{"iri": resolve(doc.doc_id, f.id), "cls": f.cls, "number": f.number,
                           "label": f.label, "method": f.method, "confidence": f.confidence,
                           "text": doc.text[f.start:f.end][:text_chars]}
                          for f in facts.fragments[:max_fragments]],
            "relations": [{"subj": resolve(doc.doc_id, r.subj), "pred": r.pred,
                           "obj": r.obj if r.literal else resolve(doc.doc_id, r.obj)}
                          for r in facts.relations[:max_relations]]}


def _extraction_callable(name: str, doc_loader: Callable[[str], Doc]) -> Callable:
    """Инструмент извлечения с единственным параметром doc_id (сигнатура чистая: её читают
    chat template и MCP)."""
    def call(doc_id: str) -> dict:
        doc = doc_loader(doc_id)
        return compact(doc, run_on_doc(doc, [name])[name])
    call.__name__ = name
    call.__doc__ = REGISTRY[name].description
    return call


def agent_tools(doc_loader: Callable[[str], Doc], kinds=("extract", "query")) -> List[dict]:
    """Инструменты для агента в формате, который принимает chat template transformers и MCP:
    [{'type': 'function', 'function': {'name', 'description', 'parameters'}, 'callable': f}].
    Инструмент извлечения вызывается с doc_id, сам загружает статью через doc_loader и
    возвращает сжатый результат (compact). Инструмент запросов вызывается с параметрами из
    своей сигнатуры."""
    out = []
    for name, spec in REGISTRY.items():
        if spec.kind not in kinds:
            continue
        call = _extraction_callable(name, doc_loader) if spec.kind == "extract" else spec.func
        out.append({"type": "function",
                    "function": {"name": name, "description": spec.description,
                                 "parameters": {"type": "object", "properties": spec.params,
                                                "required": [k for k, v in spec.params.items()
                                                             if "default" not in v]}},
                    "callable": call})
    return out
