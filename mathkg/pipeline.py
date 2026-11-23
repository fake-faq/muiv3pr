"""Прогон модулей по набору статей.

    from mathkg import pipeline
    pipeline.setup(student_modules=KG + "/modules")      # базовые + студенческие; KG — клон репозитория группы
    pipeline.run_corpus(base_dir=WORK + "/base", out_dir=WORK + "/facts")
    pipeline.export_jsonl(out_dir=WORK + "/facts")      # fragments.jsonl, relations.jsonl

Результат каждого модуля на каждой статье хранится в out_dir/{tool}/{doc_id}.json. Готовые файлы
повторно не считаются, поэтому прогон продолжается после обрыва сеанса. Если студент обновил свой
модуль, его папка удаляется (или вызывается run_corpus(..., redo=['имя'])), и пересчитываются
только этот модуль и зависящие от него.
"""
from __future__ import annotations

import glob
import importlib.util
import json
import os
import sys
import time
from collections import Counter
from typing import Dict, List, Optional

from . import registry
from .base import read_doc
from .model import Facts


def _load_files(folder: str, pattern: str, prefix: str) -> List[str]:
    loaded = []
    for path in sorted(glob.glob(os.path.join(folder, pattern))):
        name = prefix + os.path.basename(path)[:-3]
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        loaded.append(path)
    return loaded


def setup(student_modules: Optional[str] = None, query_tools: Optional[str] = None) -> List[str]:
    """Регистрирует базовые версии всех модулей, затем — студенческие файлы mNN_*.py из папки
    student_modules и инструменты запросов qNN_*.py из папки query_tools. Студенческий модуль с тем же name заменяет базовый. Вызов без
    student_modules возвращает в реестр только базовые версии (в том же сеансе)."""
    registry.reset("extract")
    loaded = registry.load_modules("mathkg.modules")
    if student_modules and os.path.isdir(student_modules):
        loaded += _load_files(student_modules, "m[0-9][0-9]_*.py", "student_")
    registry.reset("query")
    import importlib
    from . import tools_text                                 # встроенные: get_article_text, find_in_article
    importlib.reload(tools_text)
    if query_tools and os.path.isdir(query_tools):          # инструменты запросов группы qNN_*.py
        loaded += _load_files(query_tools, "q[0-9][0-9]_*.py", "qt_")
    return loaded


def _done_ids(base_dir: str) -> List[str]:
    with open(os.path.join(base_dir, "docs.jsonl"), encoding="utf-8") as f:
        return [r["doc_id"] for r in map(json.loads, f) if r.get("status") == "ok"]


def _path(out_dir: str, tool: str, doc_id: str) -> str:
    return os.path.join(out_dir, tool, f"{doc_id}.json")


def load_facts(out_dir: str, tool: str, doc_id: str) -> Optional[Facts]:
    p = _path(out_dir, tool, doc_id)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return Facts.from_json(f.read())


def run_corpus(base_dir: str, out_dir: str, names: Optional[List[str]] = None,
               doc_ids: Optional[List[str]] = None, redo: Optional[List[str]] = None,
               log_every: int = 50) -> Dict[str, dict]:
    """Запускает модули (по умолчанию все) на статьях base_dir. redo — модули, которые нужно
    пересчитать заново; зависящие от них пересчитываются тоже. Возвращает статистику по модулям:
    число статей, фрагментов, связей, ошибок и среднее время на статью."""
    order = registry.extraction_order(names)
    redo = set(redo or [])
    for n in order:                                   # зависящие от пересчитываемых — тоже
        if any(r in redo for r in registry.REGISTRY[n].requires):
            redo.add(n)
    ids = doc_ids or _done_ids(base_dir)
    stats = {n: Counter() for n in order}
    for k, doc_id in enumerate(ids, 1):
        doc = None
        cache: Dict[str, Facts] = {}
        for n in order:
            f = None if n in redo else load_facts(out_dir, n, doc_id)
            if f is not None:
                cache[n] = f
        missing = [n for n in order if n not in cache]
        if not missing:
            continue
        doc = read_doc(base_dir, doc_id)
        for n in missing:
            try:
                res = registry.run_on_doc(doc, [n], cache)
                cache[n] = res[n]
                os.makedirs(os.path.join(out_dir, n), exist_ok=True)
                with open(_path(out_dir, n, doc_id), "w", encoding="utf-8") as fh:
                    fh.write(res[n].to_json())
                s = stats[n]
                s["docs"] += 1; s["fragments"] += len(res[n].fragments)
                s["relations"] += len(res[n].relations); s["ms"] += int(res[n].sec * 1000)
            except Exception as e:
                stats[n]["errors"] += 1
                print(f"ошибка {n} на {doc_id}: {e!r}")
                break                                 # зависящие модули на этой статье не запускаются
        if k % log_every == 0:
            print(k, "статей")
    out = {}
    for n, s in stats.items():
        out[n] = dict(s)
        if s["docs"]:
            out[n]["ms_per_doc"] = round(s["ms"] / s["docs"], 1)
    return out


def export_jsonl(out_dir: str, names: Optional[List[str]] = None) -> Dict[str, int]:
    """Сводит результаты модулей в out_dir/fragments.jsonl и out_dir/relations.jsonl
    (поля fragment/relation + doc_id + module) — для анализа в pandas и для этапа 9."""
    names = names or registry.extraction_order()
    nf = nr = 0
    with open(os.path.join(out_dir, "fragments.jsonl"), "w", encoding="utf-8") as ff, \
         open(os.path.join(out_dir, "relations.jsonl"), "w", encoding="utf-8") as fr:
        for n in names:
            for p in sorted(glob.glob(os.path.join(out_dir, n, "*.json"))):
                with open(p, encoding="utf-8") as fh:
                    facts = Facts.from_json(fh.read())
                for x in facts.fragments:
                    ff.write(json.dumps(dict(vars(x), doc_id=facts.doc_id, module=n), ensure_ascii=False) + "\n"); nf += 1
                for x in facts.relations:
                    fr.write(json.dumps(dict(vars(x), doc_id=facts.doc_id, module=n), ensure_ascii=False) + "\n"); nr += 1
    return {"fragments": nf, "relations": nr}
