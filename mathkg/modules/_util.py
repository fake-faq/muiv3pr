"""Общие помощники базовых модулей."""
from __future__ import annotations

import re
from typing import List, Optional

from ..model import Doc, Fragment, paragraph_id

NUM = r"((?:[A-Z]\.)?\d+(?:\.\d+)*[a-z]?)"   # 2.1, 3, 4a, A.2.1 (приложение)

STATEMENT_WORDS = {
    # слово заголовка блока -> класс MocassinPlus (синонимы ролей онтологии + английские формы)
    "теорема": "moc:Theorem", "theorem": "moc:Theorem",
    "лемма": "moc:Lemma", "lemma": "moc:Lemma",
    "предложение": "moc:Proposition", "proposition": "moc:Proposition",
    "следствие": "moc:Corollary", "corollary": "moc:Corollary",
    "утверждение": "moc:Claim", "claim": "moc:Claim",
    "гипотеза": "moc:Conjecture", "conjecture": "moc:Conjecture",
    "аксиома": "moc:Axiom", "axiom": "moc:Axiom",
    "определение": "moc:Definition", "definition": "moc:Definition",
    "замечание": "moc:Remark", "remark": "moc:Remark",
    "пример": "moc:Example", "example": "moc:Example",
}


def header_re(words) -> re.Pattern:
    """Заголовок блока в начале абзаца: 'Теорема 2.1 (Банах).', 'Lemma 3.', 'Следствие.'"""
    alt = "|".join(sorted(words, key=len, reverse=True))
    return re.compile(rf"^\s*(?P<word>{alt})\b\s*(?P<num>{NUM})?\s*\.?\s*(\((?P<name>[^)]{{1,80}})\))?\s*[.:]?\s*",
                      re.I)


def paragraph_at(doc: Doc, pos: int) -> Optional[str]:
    k = doc.paragraph_of(pos)
    return paragraph_id(k) if k is not None else None


def italic_share(doc: Doc, start: int, end: int) -> float:
    """Доля символов отрезка, набранных курсивом."""
    if end <= start:
        return 0.0
    cov = 0
    for s in doc.styled:
        if s.kind == "italic" and s.start < end and start < s.end:
            cov += min(end, s.end) - max(start, s.start)
    return cov / (end - start)


def is_bold(doc: Doc, start: int, end: int) -> bool:
    return any(s.kind == "bold" and s.start < end and start < s.end for s in doc.styled)


def numbered(frags: List[Fragment], prefix: str) -> None:
    """Присваивает фрагментам id вида prefix-0001 в порядке позиции в тексте."""
    frags.sort(key=lambda f: f.start)
    for k, f in enumerate(frags, 1):
        f.id = f"{prefix}-{k:04d}"
