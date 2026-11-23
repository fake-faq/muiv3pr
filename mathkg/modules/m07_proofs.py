"""Модуль 7. Доказательства — базовая версия (правила). Зависит от модуля 3.

Находит доказательства: начало — абзац «Доказательство.», «Proof.», «Proof of Theorem 2.1.»,
конец — знак □ ∎ ■ или «Q.E.D.», либо начало следующего блока, раздела или утверждения
(не дальше 15 абзацев). Связь moc:proves: с утверждением, номер которого назван в заголовке
доказательства, иначе с ближайшим предшествующим утверждением. Студент улучшает: конец
доказательства без знака (классификатор абзацев), шаги, случаи, индукция, доказательство от
противного (moc:ProofStep, moc:Case, moc:ContradictionAssumption).
"""
from __future__ import annotations

import re

from ..model import Context, Doc, Facts
from ..registry import tool
from ._util import NUM, STATEMENT_WORDS, header_re

START = re.compile(rf"^\s*(Доказательство|Proof)(\s+(of|of the|теоремы|леммы|предложения|следствия|"
                   rf"утверждения)?\s*(Theorem|Lemma|Proposition|Corollary|Claim)?\s*{NUM})?\s*[.:]", re.I)
QED = re.compile(r"(□|∎|■|◻|⊓⊔|\bQ\.\s?E\.\s?D\.?|\bч\.\s?т\.\s?д\.?)\s*$")
NEXT_BLOCK = header_re(list(STATEMENT_WORDS))
KIND = {"theor": "moc:Theorem", "теоре": "moc:Theorem", "lemma": "moc:Lemma", "леммы": "moc:Lemma",
        "propo": "moc:Proposition", "предл": "moc:Proposition", "corol": "moc:Corollary",
        "следс": "moc:Corollary", "claim": "moc:Claim", "утвер": "moc:Claim"}
SECTION = re.compile(r"^\s*\d+(\.\d+)*\.?\s+[A-ZА-ЯЁ][^.]{1,80}$")


@tool(name="extract_proofs", module="m07", requires=("extract_statements",),
      produces=("moc:Proof", "moc:proves", "moc:provedBy"))
def extract_proofs(doc: Doc, ctx: Context) -> Facts:
    """Доказательства статьи и связь каждого с утверждением, которое оно доказывает
    (moc:proves и обратная moc:provedBy)."""
    facts = Facts("extract_proofs", doc.doc_id)
    statements = ctx.facts("extract_statements").fragments
    pars = doc.paragraphs
    i = 0
    while i < len(pars):
        p = pars[i]
        t = doc.text[p.start:p.end]
        m = START.match(t)
        if not m:
            i += 1
            continue
        end, j, closed = p.end, i, bool(QED.search(t))
        while not closed and j + 1 < len(pars) and j + 1 < i + 15:
            q = pars[j + 1]
            qt = doc.text[q.start:q.end]
            if NEXT_BLOCK.match(qt) or START.match(qt) or SECTION.match(qt):
                break
            j += 1
            end = q.end
            closed = bool(QED.search(qt))
        f = facts.add_fragment(id=f"prf-{len(facts.fragments) + 1:04d}", cls="moc:Proof", start=p.start, end=end,
                               page=p.page, confidence=0.9 if closed else 0.6,
                               label=" ".join(t[:m.end()].split()).rstrip(".:"))
        num = m.group(5)
        kind = KIND.get((m.group(4) or m.group(3) or "").lower()[:5])   # «of Lemma», «леммы»
        target = next((s for s in statements if s.number == num and (kind is None or s.cls == kind)),
                      None) if num else None
        if target is None:                       # ближайшее предшествующее утверждение
            prev = [s for s in statements if s.end <= p.start]
            target = prev[-1] if prev else None
        if target is not None:
            f.number = target.number
            facts.add(f.id, "moc:proves", target.id, confidence=f.confidence)
            facts.add(target.id, "moc:provedBy", f.id, confidence=f.confidence)
        i = j + 1
    return facts
