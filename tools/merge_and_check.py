#!/usr/bin/env python3
"""Межмодульный валидатор идентификаторов и сквозной склейщик RDF-графов mathkg.

Использует только стандартную библиотеку Python. Три режима работы:
  check <facts_dir>                        — проверка фактов модулей перед сборкой
  merge <out_nt> <in_nt...> [--mode ...]   — склейка N-Triples графов по якорям
  test                                     — встроенный самотест
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

MP = "https://w3id.org/mocassinplus/resource/"
MOC = "https://w3id.org/mocassinplus/ontology#"
SLR = "https://scilib.ai/ontology/scilibreco#"
OWL_SAME_AS = "http://www.w3.org/2002/07/owl#sameAs"

ALLOWED_MP_PREFIXES = {
    "doc", "person", "org", "platform", "concept",
    "statement", "keyword", "work", "source", "run", "tool",
}
ALLOWED_VOCAB_NS = (
    MP,
    MOC,
    SLR,
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "http://www.w3.org/2000/01/rdf-schema#",
    "http://www.w3.org/2001/XMLSchema#",
    "http://www.w3.org/2002/07/owl#",
)
ALLOWED_SHORT_PREFIXES = ("moc:", "scilibreco:", "rdf:", "rdfs:", "xsd:", "owl:")

PUBLIC_EMAIL_DOMAINS = {
    "gmail.com", "mail.ru", "yandex.ru", "ya.ru", "bk.ru", "list.ru",
    "inbox.ru", "rambler.ru", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
}

RE_NUMBER = re.compile(r"^[A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)*$")
RE_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
RE_MSC = re.compile(r"^(?:\d{2}[A-Z]\d{2}|\d{2}[A-Z]xx|\d{2}-XX)$")
RE_ORCID = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
RE_DOC_SEGMENT = re.compile(r"/doc/[^/]+/.+")

RE_NT_LINE = re.compile(
    r'^\s*<([^>]+)>\s+<([^>]+)>\s+(?:<([^>]+)>|"((?:[^"\\]|\\.)*)"(?:@([A-Za-z0-9-]+)|\^\^<([^>]+)>)?)?\s*\.\s*$'
)


# ---------------------------------------------------------------------------
# 1. Валидация фактов (check)
# ---------------------------------------------------------------------------

def _expand_pred(pred: str) -> str:
    if pred.startswith("moc:"):
        return MOC + pred[4:]
    if pred.startswith("scilibreco:"):
        return SLR + pred[11:]
    return pred


def _check_global_iri(ref: str, loc: str, errors: List[str]) -> None:
    if not isinstance(ref, str):
        return
    if ref.startswith(MP) or ref.startswith("mp:"):
        rest = ref[len(MP):] if ref.startswith(MP) else ref[3:]
        head = rest.split("/", 1)[0]
        if head not in ALLOWED_MP_PREFIXES:
            errors.append(f"{loc}: недопустимый префикс mp-ресурса '{head}' в '{ref}'")
        return
    if ref.startswith(("http://", "https://")):
        if not ref.startswith(ALLOWED_VOCAB_NS):
            errors.append(f"{loc}: посторонний внешний IRI '{ref}'")
    elif ":" in ref and not ref.startswith(ALLOWED_SHORT_PREFIXES):
        errors.append(f"{loc}: неизвестный префикс в '{ref}'")


def _check_literal_value(pred: str, val: object, loc: str, errors: List[str]) -> None:
    full = _expand_pred(pred)
    s = str(val)
    if full == MOC + "hasDOI":
        if s != s.lower() or s.startswith(("http://", "https://", "doi:")) or not RE_DOI.match(s):
            errors.append(f"{loc}: неверный формат DOI '{s}' (ожидается нижний регистр '10.xxxx/...')")
    elif full == MOC + "hasMSCCode":
        if not RE_MSC.match(s):
            errors.append(f"{loc}: неверный формат MSC '{s}' (ожидается код вида '46B20' или '46Bxx')")
    elif full == SLR + "hasORCID":
        if not RE_ORCID.match(s):
            errors.append(f"{loc}: неверный формат ORCID '{s}' (ожидается '0000-0000-0000-0000')")


def check_facts_dir(facts_dir: str) -> Tuple[int, List[str]]:
    """Проверяет JSON-файлы Facts на межмодульные коллизии ID, форматы полей и IRI."""
    root = Path(facts_dir)
    if not root.exists():
        return 0, [f"каталог не найден: {facts_dir}"]

    errors: List[str] = []
    seen_ids: Dict[Tuple[str, str], str] = {}
    checked_files = 0

    for path in sorted(root.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path}: ошибка чтения JSON ({exc})")
            continue
        if not isinstance(data, dict) or "fragments" not in data or "relations" not in data:
            continue

        checked_files += 1
        module = str(data.get("module") or path.parent.name)
        doc_id = str(data.get("doc_id") or path.stem)

        for frag in data.get("fragments", []):
            fid = frag.get("id", "")
            loc = f"{doc_id}/{module}/{fid}"
            key = (doc_id, fid)
            if key in seen_ids and seen_ids[key] != module:
                errors.append(f"{loc}: коллизия локального id '{fid}' с модулем '{seen_ids[key]}'")
            else:
                seen_ids.setdefault(key, module)

            num = frag.get("number")
            if num is not None and str(num) != "":
                if not RE_NUMBER.match(str(num)):
                    errors.append(f"{loc}: некорректный number '{num}' (уберите слова, скобки и точку в конце)")

            for attr_pred, attr_val in (frag.get("attrs") or {}).items():
                _check_literal_value(attr_pred, attr_val, loc, errors)

        for rel in data.get("relations", []):
            pred = str(rel.get("pred", ""))
            loc = f"{doc_id}/{module} ({pred})"
            _check_global_iri(rel.get("subj", ""), loc, errors)
            if rel.get("literal"):
                _check_literal_value(pred, rel.get("obj", ""), loc, errors)
            else:
                _check_global_iri(rel.get("obj", ""), loc, errors)

    return checked_files, errors


# ---------------------------------------------------------------------------
# 2. Сквозная склейка N-Triples (merge)
# ---------------------------------------------------------------------------

class UnionFind:
    """Система непересекающихся множеств с детерминированным выбором канона."""

    def __init__(self) -> None:
        self.parent: Dict[str, str] = {}

    def find(self, x: str) -> str:
        while self.parent.get(x, x) != x:
            self.parent[x] = self.parent.get(self.parent[x], self.parent[x])
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.parent.setdefault(ra, ra)
        self.parent[rb] = ra
        return True


def _unescape_nt(s: str) -> str:
    return (
        s.replace("\\\\", "\x00")
        .replace('\\"', '"')
        .replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
        .replace("\x00", "\\")
    )


def _norm_doi(raw: str) -> Optional[str]:
    v = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", raw.strip(), flags=re.I).lower()
    return v if RE_DOI.match(v) else None


def _norm_orcid(raw: str) -> Optional[str]:
    v = re.sub(r"^https?://orcid\.org/", "", raw.strip(), flags=re.I).upper()
    return v if RE_ORCID.match(v) else None


def _norm_email(raw: str) -> Optional[str]:
    v = raw.strip().lower()
    return v if "@" in v and "." in v.split("@", 1)[1] else None


def _norm_domain(raw: str) -> Optional[str]:
    v = raw.strip().lower().lstrip("@")
    if not v or "." not in v or v in PUBLIC_EMAIL_DOMAINS:
        return None
    return v


def _norm_issn(raw: str) -> Optional[str]:
    digits = re.sub(r"[^0-9X]", "", raw.upper())
    return f"{digits[:4]}-{digits[4:]}" if len(digits) == 8 else None


def _extract_anchor(subj: str, pred: str, lit_val: str) -> Optional[Tuple[str, str]]:
    if pred == MOC + "hasDOI":
        # Исключаем библиографические строки /doc/{id}/ref-NN: они упоминают работу, но не являются ею.
        if RE_DOC_SEGMENT.search(subj):
            return None
        val = _norm_doi(lit_val)
        return ("DOI", val) if val else None
    if pred == SLR + "hasORCID":
        val = _norm_orcid(lit_val)
        return ("ORCID", val) if val else None
    if pred == SLR + "hasEmail":
        val = _norm_email(lit_val)
        return ("email", val) if val else None
    if pred == SLR + "hasEmailDomain":
        val = _norm_domain(lit_val)
        return ("email_domain", val) if val else None
    if pred == SLR + "hasISSN":
        val = _norm_issn(lit_val)
        return ("ISSN", val) if val else None
    return None


def _canonical_choice(members: Iterable[str]) -> str:
    """Приоритет у вершины корпуса mp:doc/{id}, иначе лексикографически минимальный IRI."""
    doc_prefix = MP + "doc/"
    return min(
        members,
        key=lambda iri: (0 if iri.startswith(doc_prefix) and "/" not in iri[len(doc_prefix):] else 1, iri),
    )


def _rewrite_nt_line(line: str, alias_map: Dict[str, str]) -> str:
    m = RE_NT_LINE.match(line)
    if not m:
        return line.strip()
    subj, pred, obj_iri, lit_val, lang, dtype = m.groups()
    subj = alias_map.get(subj, subj)
    if obj_iri is not None:
        obj_iri = alias_map.get(obj_iri, obj_iri)
        return f"<{subj}> <{pred}> <{obj_iri}> ."
    if lang is not None:
        return f'<{subj}> <{pred}> "{lit_val}"@{lang} .'
    if dtype is not None:
        return f'<{subj}> <{pred}> "{lit_val}"^^<{dtype}> .'
    return f'<{subj}> <{pred}> "{lit_val}" .'


def merge_ntriples(out_path: str, in_paths: Sequence[str], mode: str = "sameas") -> Dict[str, object]:
    """Объединяет N-Triples графы с дедупликацией и склейкой сущностей по якорям."""
    anchors: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    in_triples = 0

    for p in in_paths:
        with open(p, encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                in_triples += 1
                m = RE_NT_LINE.match(line)
                if not m:
                    continue
                subj, pred, _, lit_val, _, _ = m.groups()
                if lit_val is not None:
                    anc = _extract_anchor(subj, pred, _unescape_nt(lit_val))
                    if anc:
                        anchors[anc].add(subj)

    uf = UnionFind()
    anchor_clusters: Dict[str, int] = defaultdict(int)
    for (kind, _), subs in anchors.items():
        if len(subs) > 1:
            ordered = sorted(subs)
            first = ordered[0]
            merged_any = False
            for other in ordered[1:]:
                if uf.union(first, other):
                    merged_any = True
            if merged_any:
                anchor_clusters[kind] += 1

    groups: Dict[str, Set[str]] = defaultdict(set)
    for subs in anchors.values():
        if len(subs) > 1:
            for s in subs:
                groups[uf.find(s)].add(s)

    alias_map: Dict[str, str] = {}
    for members in groups.values():
        if len(members) > 1:
            canon = _canonical_choice(members)
            for m_iri in members:
                if m_iri != canon:
                    alias_map[m_iri] = canon

    seen_lines: Set[str] = set()
    out_lines: List[str] = []
    for p in in_paths:
        with open(p, encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                final_line = _rewrite_nt_line(line, alias_map) if mode == "rewrite" else line
                if final_line not in seen_lines:
                    seen_lines.add(final_line)
                    out_lines.append(final_line)

    sameas_added = 0
    if mode == "sameas":
        for alias, canon in sorted(alias_map.items()):
            stmt = f"<{alias}> <{OWL_SAME_AS}> <{canon}> ."
            if stmt not in seen_lines:
                seen_lines.add(stmt)
                out_lines.append(stmt)
                sameas_added += 1

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as out:
        for line in out_lines:
            out.write(line + "\n")

    return {
        "mode": mode,
        "in_triples": in_triples,
        "out_triples": len(out_lines),
        "merged_aliases": len(alias_map),
        "sameas_added": sameas_added,
        "clusters_by_anchor": dict(sorted(anchor_clusters.items())),
    }


# ---------------------------------------------------------------------------
# 3. Встроенный самотест (test)
# ---------------------------------------------------------------------------

def run_self_test() -> None:
    """Проверяет check_facts_dir и merge_ntriples на синтетических примерах."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # 3.1 Валидный набор Facts
        ok_dir = root / "facts_ok"
        (ok_dir / "extract_metadata").mkdir(parents=True)
        (ok_dir / "extract_statements").mkdir(parents=True)
        (ok_dir / "extract_metadata" / "A001.json").write_text(json.dumps({
            "module": "m01", "doc_id": "A001",
            "fragments": [{"id": "title", "cls": "moc:Title", "start": 0, "end": 10}],
            "relations": [
                {"subj": f"{MP}doc/A001", "pred": "moc:hasDOI", "obj": "10.1134/s0001434619010012", "literal": True},
                {"subj": f"{MP}doc/A001", "pred": "moc:hasMSCCode", "obj": "46B20", "literal": True},
                {"subj": f"{MP}person/ivanov-a-b", "pred": "scilibreco:hasORCID", "obj": "0000-0002-1825-0097", "literal": True},
            ],
        }), encoding="utf-8")
        (ok_dir / "extract_statements" / "A001.json").write_text(json.dumps({
            "module": "m03", "doc_id": "A001",
            "fragments": [{"id": "st-0001", "cls": "moc:Theorem", "start": 20, "end": 50, "number": "2.1"}],
            "relations": [],
        }), encoding="utf-8")
        n_files, errs = check_facts_dir(str(ok_dir))
        assert n_files == 2 and not errs, f"Ожидалось 0 ошибок, получено: {errs}"

        # 3.2 Набор Facts с 5 намеренными ошибками
        bad_dir = root / "facts_bad"
        (bad_dir / "m01").mkdir(parents=True)
        (bad_dir / "m03").mkdir(parents=True)
        (bad_dir / "m01" / "A001.json").write_text(json.dumps({
            "module": "m01", "doc_id": "A001",
            "fragments": [{"id": "st-0001", "cls": "moc:Title", "start": 0, "end": 10}],
            "relations": [
                {"subj": f"{MP}doc/A001", "pred": "moc:hasDOI", "obj": "https://doi.org/10.1134/ABC", "literal": True},
                {"subj": f"{MP}doc/A001", "pred": "moc:hasMSCCode", "obj": "MSC 46B20", "literal": True},
                {"subj": "mp:author/ivanov", "pred": "rdf:type", "obj": "scilibreco:Author", "literal": False},
            ],
        }), encoding="utf-8")
        (bad_dir / "m03" / "A001.json").write_text(json.dumps({
            "module": "m03", "doc_id": "A001",
            "fragments": [{"id": "st-0001", "cls": "moc:Theorem", "start": 20, "end": 50, "number": "2.1."}],
            "relations": [],
        }), encoding="utf-8")
        _, errs = check_facts_dir(str(bad_dir))
        assert len(errs) == 5, f"Ожидалось 5 ошибок, получено {len(errs)}: {errs}"

        # 3.3 Склейка N-Triples двух студентов (sameas и rewrite)
        nt1 = root / "student1.nt"
        nt2 = root / "student2.nt"
        nt1.write_text(
            "\n".join([
                f'<{MP}doc/A001> <{MOC}hasDOI> "10.1134/s0001" .',
                f'<{MP}person/ivanov-a-b> <{SLR}hasEmail> "Ivanov@msu.ru" .',
                f'<{MP}person/ivanov-a-b> <{SLR}hasORCID> "0000-0002-1825-0097" .',
                f'<{MP}org/mgu> <{SLR}hasEmailDomain> "msu.ru" .',
                f'<{MP}org/author1> <{SLR}hasEmailDomain> "gmail.com" .',
                f'<{MP}platform/mat-zametki> <{SLR}hasISSN> "0025-567X" .',
            ]) + "\n",
            encoding="utf-8",
        )
        nt2.write_text(
            "\n".join([
                f'<{MP}doc/B002/ref-07> <{MOC}hasDOI> "10.1134/s0001" .',
                f'<{MP}doc/B002/ref-07> <{MOC}mentions> <{MP}work/doi-10-1134-s0001> .',
                f'<{MP}work/doi-10-1134-s0001> <{MOC}hasDOI> "10.1134/S0001" .',
                f'<{MP}person/ivanov-a> <{SLR}hasEmail> "ivanov@msu.ru" .',
                f'<{MP}org/lomonosov-msu> <{SLR}hasEmailDomain> "msu.ru" .',
                f'<{MP}org/author2> <{SLR}hasEmailDomain> "gmail.com" .',
                f'<{MP}platform/math-notes> <{SLR}hasISSN> "0025567x" .',
            ]) + "\n",
            encoding="utf-8",
        )

        out_sameas = root / "merged_sameas.nt"
        st_sameas = merge_ntriples(str(out_sameas), [str(nt1), str(nt2)], mode="sameas")
        txt_sameas = out_sameas.read_text(encoding="utf-8")
        assert f"<{MP}work/doi-10-1134-s0001> <{OWL_SAME_AS}> <{MP}doc/A001> ." in txt_sameas
        assert f"<{MP}doc/B002/ref-07> <{OWL_SAME_AS}>" not in txt_sameas
        assert f"<{MP}person/ivanov-a-b> <{OWL_SAME_AS}> <{MP}person/ivanov-a> ." in txt_sameas
        assert f"<{MP}org/mgu> <{OWL_SAME_AS}> <{MP}org/lomonosov-msu> ." in txt_sameas
        assert f"<{MP}org/author1> <{OWL_SAME_AS}>" not in txt_sameas
        assert f"<{MP}platform/math-notes> <{OWL_SAME_AS}> <{MP}platform/mat-zametki> ." in txt_sameas
        assert st_sameas["sameas_added"] == 4

        out_rw = root / "merged_rewrite.nt"
        merge_ntriples(str(out_rw), [str(nt1), str(nt2)], mode="rewrite")
        txt_rw = out_rw.read_text(encoding="utf-8")
        assert f"<{MP}doc/B002/ref-07> <{MOC}mentions> <{MP}doc/A001> ." in txt_rw
        assert f"<{MP}work/doi-10-1134-s0001>" not in txt_rw
        assert OWL_SAME_AS not in txt_rw


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка межмодульных ID и склейка графов mathkg")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="Проверить JSON-факты модулей в папке facts/")
    p_check.add_argument("facts_dir", help="Путь к каталогу facts/")

    p_merge = sub.add_parser("merge", help="Склеить N-Triples файлы студентов в общий граф")
    p_merge.add_argument("out_nt", help="Выходной .nt файл")
    p_merge.add_argument("in_nt", nargs="+", help="Входные .nt файлы")
    p_merge.add_argument("--mode", choices=("sameas", "rewrite"), default="sameas",
                         help="sameas — добавить owl:sameAs (по умолчанию); rewrite — заменить алиасы")

    sub.add_parser("test", help="Запустить встроенный самотест")

    args = parser.parse_args(argv)
    if args.cmd == "check":
        n_files, errors = check_facts_dir(args.facts_dir)
        print(f"Проверено файлов Facts: {n_files}, ошибок: {len(errors)}")
        for e in errors[:50]:
            print(" -", e)
        if len(errors) > 50:
            print(f" ... и ещё {len(errors) - 50}")
        return 1 if errors else 0
    if args.cmd == "merge":
        stats = merge_ntriples(args.out_nt, args.in_nt, mode=args.mode)
        print(json.dumps(stats, ensure_ascii=False))
        return 0
    if args.cmd == "test":
        run_self_test()
        print("OK")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
