"""Модуль 8. Ссылки внутри статьи и зависимости — базовая версия (правила).
Зависит от модулей 2, 3, 4, 6, 7.

Находит ссылки «по лемме 2.1», «теоремы 3», «Theorem 1.2», «в силу (2.3)», «разд. 4»,
«Section 4», «[12]», «[3, 5–7]» и разрешает их по номеру внутри статьи: в утверждение, определение,
формулу, раздел или запись списка литературы. Ссылка внутри доказательства даёт связь
доказательства (moc:usesLemma, moc:usesTheorem, moc:usesDefinition, moc:usesResult), вне
доказательства — связь абзаца (moc:refersToTheorem, moc:refersToStatement,
moc:refersToDefinition, moc:refersToFormula, moc:refersToSection, moc:citesBibliographicReference).
Если PDF содержит гиперссылку с именованной целью (cite.…, equation.…), связь подтверждается
ссылкой. Студент улучшает: формы слов и сокращения («Л. 2», «Thm. 3»), диапазоны «леммы 2–4»,
ссылки «выше», «предыдущая лемма», гиперссылки как основной источник.
"""
from __future__ import annotations

import re

from ..model import Context, Doc, Facts, formula_id
from ..registry import tool
from ._util import NUM, paragraph_at

WORD = {
    "теорем": "moc:Theorem", "theorem": "moc:Theorem", "thm": "moc:Theorem",
    "лемм": "moc:Lemma", "lemma": "moc:Lemma",
    "предложени": "moc:Proposition", "proposition": "moc:Proposition", "prop": "moc:Proposition",
    "следстви": "moc:Corollary", "corollary": "moc:Corollary", "cor": "moc:Corollary",
    "утверждени": "moc:Claim", "claim": "moc:Claim",
    "определени": "moc:Definition", "definition": "moc:Definition", "def": "moc:Definition",
    "раздел": "section", "разд": "section", "section": "section", "sect": "section", "§": "section",
}
REF = re.compile(rf"\b(теорем\w*|лемм\w*|предложени\w*|следстви\w*|утверждени\w*|определени\w*|раздел\w*|разд\.|"
                 rf"Theorems?|Thm\.|Lemmas?|Propositions?|Prop\.|Corollar(?:y|ies)|Cor\.|Claims?|Definitions?|Def\.|"
                 rf"Sections?|Sect\.|§)\s*{NUM}", re.I)
EQ = re.compile(rf"\(\s*{NUM}\s*\)")
CITE = re.compile(r"\[(\d{1,3}(?:\s*[,–—-]\s*\d{1,3})*)\]")
# «[3, Theorem 4.1]», «[5, § 2]», «[7, с. 15]»: ссылка на литературу с указанием места в работе
CITE_LOC = re.compile(r"\[(\d{1,3}),\s*(?:Theorem|Thm\.|Lemma|Prop\w*|Cor\w*|Section|Sect\.|Chapter|Ch\.|§|p\.|pp\.|"
                      r"Теорема|теорема|Лемма|лемма|гл\.|с\.|п\.)[^\]]{0,40}\]")
USES = {"moc:Lemma": "moc:usesLemma", "moc:Theorem": "moc:usesTheorem", "moc:Definition": "moc:usesDefinition"}
REFERS = {"moc:Theorem": "moc:refersToTheorem", "moc:Definition": "moc:refersToDefinition"}


def _kind(word: str) -> str:
    w = word.lower().rstrip(".s")
    for k, v in WORD.items():
        if w.startswith(k):
            return v
    return ""


def _expand(s: str):
    out = []
    for part in re.split(r"\s*,\s*", s):
        m = re.match(r"(\d+)\s*[–—-]\s*(\d+)", part)
        if m and int(m.group(2)) - int(m.group(1)) < 30:
            out += list(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.strip().isdigit():
            out.append(int(part))
    return out


@tool(name="extract_references", module="m08",
      requires=("extract_structure", "extract_statements", "extract_definitions", "extract_bibliography",
                "extract_proofs"),
      produces=("moc:refersToTheorem", "moc:refersToFormula", "moc:usesLemma", "moc:citesBibliographicReference"))
def extract_references(doc: Doc, ctx: Context) -> Facts:
    """Ссылки внутри статьи на утверждения, определения, формулы, разделы и литературу;
    зависимости доказательств от лемм, теорем и определений."""
    facts = Facts("extract_references", doc.doc_id)
    stm = ctx.facts("extract_statements").fragments + ctx.facts("extract_definitions").fragments
    secs = {f.number: f.id for f in ctx.facts("extract_structure").fragments if f.number}
    refs = {f.number: f.id for f in ctx.facts("extract_bibliography").fragments if f.cls == "moc:ReferenceEntry"}
    bib = next((f for f in ctx.facts("extract_bibliography").fragments if f.id == "bib"), None)
    proofs = ctx.facts("extract_proofs").fragments
    eqs = {f.number: formula_id(f.i) for f in doc.formulas if f.number}
    linked = [(l.start, l.end, l.target) for l in doc.links if l.start >= 0]

    def source(pos):
        """Доказательство, в котором стоит ссылка, иначе абзац."""
        for p in proofs:
            if p.start <= pos < p.end:
                return p.id, True
        return paragraph_at(doc, pos), False

    def confirmed(a, b, prefix):
        return any(s < b and a < e and t.startswith(prefix) for s, e, t in linked)

    seen = set()
    def add(subj, pred, obj, a, b, conf):
        if subj and (subj, pred, obj) not in seen and subj != obj:
            seen.add((subj, pred, obj))
            facts.add(subj, pred, obj, start=a, end=b, confidence=conf)

    limit = bib.start if bib else len(doc.text)
    for m in REF.finditer(doc.text, 0, limit):
        kind, num = _kind(m.group(1)), m.group(2)
        own = [s for s in stm if s.start <= m.start() < s.end]
        src, in_proof = source(m.start())
        if kind == "section":
            if num in secs:
                add(src, "moc:refersToSection", secs[num], m.start(), m.end(), 0.8)
            continue
        target = next((s for s in stm if s.number == num and (not kind or s.cls == kind) and s not in own), None)
        if target is None:
            continue
        conf = 1.0 if confirmed(m.start(), m.end(), "") else 0.8
        if in_proof:
            add(src, USES.get(target.cls, "moc:usesResult"), target.id, m.start(), m.end(), conf)
        else:
            add(src, REFERS.get(target.cls, "moc:refersToStatement"), target.id, m.start(), m.end(), conf)
    for m in EQ.finditer(doc.text, 0, limit):
        if m.group(1) in eqs and not any(f.start <= m.start() < f.end for f in doc.formulas):
            src, _ = source(m.start())
            conf = 1.0 if confirmed(m.start(), m.end(), "equation") else 0.7
            add(src, "moc:refersToFormula", eqs[m.group(1)], m.start(), m.end(), conf)
    for m in CITE_LOC.finditer(doc.text, 0, limit):
        src, _ = source(m.start())
        if m.group(1) in refs:
            add(src, "moc:citesBibliographicReference", refs[m.group(1)], m.start(), m.end(), 0.8)
    for m in CITE.finditer(doc.text, 0, limit):
        src, _ = source(m.start())
        for n in _expand(m.group(1)):
            if str(n) in refs:
                conf = 1.0 if confirmed(m.start(), m.end(), "cite") else 0.8
                add(src, "moc:citesBibliographicReference", refs[str(n)], m.start(), m.end(), conf)
    return facts
