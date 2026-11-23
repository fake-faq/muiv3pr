"""Встроенные инструменты запросов к тексту статьи (base). Регистрируются pipeline.setup.

Папка с результатами base задаётся переменной окружения KG_BASE_DIR (в блокноте:
os.environ['KG_BASE_DIR'] = WORK + '/base'); так её видит и MCP-сервер в дочернем процессе.
"""
from __future__ import annotations

import os

from .registry import query_tool


def _doc(doc_id: str):
    from .base import read_doc
    base_dir = os.environ.get("KG_BASE_DIR")
    if not base_dir:
        raise RuntimeError("не задана переменная окружения KG_BASE_DIR (папка base набора)")
    return read_doc(base_dir, doc_id)


@query_tool(name="get_article_text")
def get_article_text(doc_id: str, start_paragraph: int = 0, n_paragraphs: int = 20) -> dict:
    """Текст статьи doc_id по абзацам: n_paragraphs абзацев начиная с номера start_paragraph
    (нумерация с 0). Возвращает язык, число страниц и абзацев и список абзацев с id (p-0001…),
    страницей и текстом. Для длинной статьи инструмент вызывают несколько раз, сдвигая
    start_paragraph."""
    doc = _doc(doc_id)
    n_paragraphs = max(1, min(int(n_paragraphs), 50))
    out = []
    for i in range(int(start_paragraph), min(len(doc.paragraphs), int(start_paragraph) + n_paragraphs)):
        p = doc.paragraphs[i]
        out.append({"id": f"p-{i + 1:04d}", "page": p.page, "text": doc.text[p.start:p.end][:1500]})
    return {"doc_id": doc_id, "lang": doc.lang, "n_pages": doc.meta.get("n_pages"),
            "n_paragraphs": len(doc.paragraphs), "paragraphs": out}


@query_tool(name="find_in_article")
def find_in_article(doc_id: str, phrase: str, limit: int = 10) -> list:
    """Абзацы статьи doc_id, в которых встречается phrase (без учёта регистра): id абзаца,
    страница и фрагмент текста вокруг совпадения."""
    doc = _doc(doc_id)
    q = phrase.lower()
    hits = []
    for i, p in enumerate(doc.paragraphs):
        t = doc.text[p.start:p.end]
        k = t.lower().find(q)
        if k >= 0:
            hits.append({"id": f"p-{i + 1:04d}", "page": p.page, "text": t[max(0, k - 150):k + 250]})
            if len(hits) >= limit:
                break
    return hits
