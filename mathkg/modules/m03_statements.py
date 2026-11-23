"""Модуль 3. Утверждения: теоремы, леммы, предложения, следствия — базовая версия (правила).

Находит блоки с заголовком «Теорема 2.1 (Банах).», «Lemma 3.», «Следствие.» в начале абзаца,
определяет класс по слову заголовка, номер и имя в скобках. Границы блока: абзац заголовка и
следующие курсивные абзацы (в статьях из LaTeX формулировка набрана курсивом) до начала
доказательства или нового блока. Студент улучшает: заголовок внутри абзаца, границы в статьях
без курсива, утверждения без заголовка (классификатор), гипотезы и открытые проблемы.
"""
from __future__ import annotations

from ..model import Context, Doc, Facts
from ..registry import tool
from ._util import STATEMENT_WORDS, header_re, is_bold, italic_share, numbered, paragraph_at

WORDS = {w: c for w, c in STATEMENT_WORDS.items()
         if c in ("moc:Theorem", "moc:Lemma", "moc:Proposition", "moc:Corollary", "moc:Claim",
                  "moc:Conjecture", "moc:Axiom")}
HEAD = header_re(WORDS)
STOP = header_re(list(STATEMENT_WORDS) + ["доказательство", "proof"])


@tool(name="extract_statements", module="m03",
      produces=("moc:Theorem", "moc:Lemma", "moc:Proposition", "moc:Corollary", "moc:Claim", "moc:Conjecture"))
def extract_statements(doc: Doc, ctx: Context) -> Facts:
    """Утверждения статьи с заголовком: теоремы, леммы, предложения, следствия, гипотезы.
    У каждого — класс, номер ('2.1'), метка ('Теорема 2.1 (Банах)') и границы формулировки."""
    facts = Facts("extract_statements", doc.doc_id)
    pars = doc.paragraphs
    for i, p in enumerate(pars):
        t = doc.text[p.start:p.end]
        m = HEAD.match(t)
        if not m or not (m.group("num") or m.group("name") or t[m.end() - 1:m.end()] in ".:"
                         or t[:m.end()].rstrip().endswith((".", ":"))):
            continue                                  # «Theorem formalization builds…» — не заголовок
        word = m.group("word").lower()
        end, j = p.end, i + 1
        while j < len(pars) and j < i + 4:            # продолжение формулировки курсивом
            q = pars[j]
            if STOP.match(doc.text[q.start:q.end]) or italic_share(doc, q.start, q.end) < 0.6:
                break
            end, j = q.end, j + 1
        bold = is_bold(doc, p.start, p.start + m.end())
        label = " ".join(x for x in (m.group("word"), m.group("num")) if x)
        if m.group("name"):
            label += f" ({m.group('name')})"
        facts.add_fragment(id="st", cls=WORDS[word], start=p.start, end=end, number=m.group("num"),
                           label=label, page=p.page, confidence=0.9 if bold else 0.7)
    numbered(facts.fragments, "st")
    for f in facts.fragments:
        facts.add(doc.iri, "scilibreco:containsStatement", f.id)
        par = paragraph_at(doc, f.start)
        if par:
            facts.add(f.id, "moc:isPartOf", par)
    return facts
