"""Модуль 1. Метаданные статьи — версия студента Ускова К. И. (вариант 1).

Три ступени (переключаются переменной окружения MATHKG_M01=rules|model|llm, по умолчанию model):
* rules — разбор зон первой страницы по кеглю, положению, верхним индексам и регулярным выражениям:
  заголовок (и англ. дубль в русских статьях), аннотация (в т.ч. без метки), ключевые слова,
  FrontMatter, УДК, MSC (включая AMS subject classification), DOI, ORCID, год публикации (без года
  поступления рукописи), авторы, организации, e-mail, корпоративный домен, журнал/архив, том, номер,
  страницы и ISSN;
* model — проверка и добор авторов/организаций готовым NER Babelscape/wikineural-multilingual-ner
  через общий кэш mathkg.models;
* llm — выборочный JSON-разбор Qwen/Qwen2.5-1.5B-Instruct с дисковым кэшем и обязательной
  проверкой каждого значения подстрокой в тексте (только если правила и NER не нашли журнал или
  организации/авторов).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

from mathkg.common import MP, normalize_name, org_iri, person_iri, platform_iri, slug
from mathkg.model import Context, Doc, Facts
from mathkg.registry import tool

NER_MODEL = "Babelscape/wikineural-multilingual-ner"

PUBLIC_EMAIL_DOMAINS = {
    "gmail.com", "mail.ru", "yandex.ru", "ya.ru", "bk.ru", "list.ru",
    "inbox.ru", "rambler.ru", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
}

SUP_TRANS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")

UDC_RX = re.compile(r"(?:УДК|UDC)\s*:?\s*([0-9][0-9.:()\-+/ ]{0,40}[0-9)])")
MSC_RX = re.compile(
    r"(?:(?:20\d{2}\s+)?(?:MSC|Mathematics\s+Subject\s+Classification|AMS\s+[Ss]ubject\s+[Cc]lassification)"
    r"(?:\s*\(?\d{4}\)?)?)\s*[:.]?\s*"
    r"((?:\d{2}[A-Z]\d{2}|\d{2}[A-Z]xx|\d{2}-XX)(?:\s*[,;]\s*(?:\d{2}[A-Z]\d{2}|\d{2}[A-Z]xx|\d{2}-XX))*)",
    re.I,
)
DOI_RX = re.compile(r"\b(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/[^\s\"<>]+[^\s\"<>.,;\]\)])", re.I)
ORCID_RX = re.compile(r"(?:https?://orcid\.org/|ORCID\s*:?\s*)?(\d{4}-\d{4}-\d{4}-\d{3}[\dX])\b", re.I)
ISSN_RX = re.compile(r"\bISSN\s*:?\s*(\d{4}[-–]\d{3}[\dX])\b", re.I)
ARXIV_RX = re.compile(r"\barXiv\s*:\s*(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.I)
EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
YEAR_RX = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")

ABSTRACT_RU_RX = re.compile(r"(?:^|\n)\s*(Аннотация|Резюме)\s*[.:—-]?\s*", re.I)
ABSTRACT_EN_RX = re.compile(r"(?:^|\n)\s*(Abstract|Summary)\s*[.:—-]?\s*", re.I)
KEYWORDS_RU_RX = re.compile(r"(?:^|\n)\s*(Ключевые слова)\s*[.:—-]?\s*", re.I)
KEYWORDS_EN_RX = re.compile(r"(?:^|\n)\s*(Keywords|Key words)\s*[.:—-]?\s*", re.I)
STOP_ABSTRACT_RX = re.compile(
    r"\n\n\s*(Ключевые слова|Keywords|Key words|1\.?\s+(?:Введение|Introduction)|Введение|Introduction|"
    r"MSC\b|AMS\s+[Ss]ubject|УДК\b|UDC\b|DOI\b)",
    re.I,
)
FIRST_SEC_RX = re.compile(
    r"(?:^|\n\n)\s*(?:1\.?\s+(?:Введение|Introduction|Preliminaries|Постановка)|Введение|Introduction)\b",
    re.I,
)
RECEIVED_CTX_RX = re.compile(
    r"(?:Received|Submitted|Revised|Поступила(?:\s+в\s+редакцию)?|Доработана|Принята\s+к\s+печати)[^\n]{0,60}$",
    re.I,
)
PUBLISHED_CTX_RX = re.compile(r"(?:Published|Опубликована|©|\bVol\.|\bТ\.\s*\d)", re.I)

# Строки цитирования: русская «// Математические заметки. 2019. Т. 105, № 1. С. 3–15» и англ. форматы
CITE_RU_RX = re.compile(
    r"(?:^|\n)\s*//\s*([А-ЯЁA-Z][^.\n]{2,80}?)\.\s*(?:[—–-]\s*)?(?:(19\d\d|20\d\d)\.\s*(?:[—–-]\s*)?)?"
    r"(?:Т\.|Том)\s*(\d+)\s*[,;.]\s*(?:№|No\.?)\s*(\d+)"
    r"(?:\s*[.,;]\s*(?:[—–-]\s*)?(?:С\.|с\.|pp\.?)\s*(\d+\s*[–—-]\s*\d+))?",
    re.M,
)
CITE_EN_VOL_RX = re.compile(
    r"(?:^|\n)\s*([A-Z][A-Za-z0-9 &:'’.-]{2,80}?)[,;.]\s*"
    r"(?:Vol\.?|Volume)\s*(\d+)\s*(?:\((19\d\d|20\d\d)\))?\s*[,;.]\s*"
    r"(?:No\.?|Issue)\s*(\d+)(?:\s*[,;.]\s*(?:pp?\.?|pages?)\s*(\d+\s*[–—-]\s*\d+))?",
    re.M,
)
CITE_EN_SHORT_RX = re.compile(
    r"(?:^|\n)\s*([A-Z][A-Za-z0-9 &:'’.-]{2,80}?)[,]\s*(19\d\d|20\d\d)\s*,\s*"
    r"(\d+)\s*\((\d+)\)\s*[,:]\s*(\d+\s*[–—-]\s*\d+)",
    re.M,
)

ORG_CUE_RX = re.compile(
    r"\b(университет\w*|институт\w*|академ\w*|факультет\w*|кафедр\w*|центр\w*|лаборатор\w*|"
    r"отделени\w*|филиал\w*|ран|мгу|спбгу|вшэ|мфти|нгу|фиц|ипм|россия|москва|санкт-петербург|"
    r"новосибирск|казань|екатеринбург|university|universidade|universitat|universität|université|"
    r"institute|institut|department|dept\.?|faculty|college|school|center|centre|academy|laboratory|"
    r"research|federal|national|state|polytechnic|corporation|street|str\.|ave\.?|avenue|blvd|"
    r"moscow|russia|brazil|rio\s+de\s+janeiro|prague|czech|germany|berlin|france|paris|china|"
    r"beijing|usa|italy|spain|poland|ukraine|belarus)\b",
    re.I,
)

NON_PERSON_WORDS = {
    "abstract", "summary", "introduction", "keywords", "key", "words", "journal", "volume",
    "issue", "pages", "received", "revised", "accepted", "published", "submitted", "theorem",
    "lemma", "proposition", "corollary", "definition", "remark", "example", "proof",
    "equation", "equations", "spaces", "space", "banach", "sobolev", "differential",
    "mathematics", "mathematical", "physics", "classification", "subject", "section",
    "preprint", "arxiv", "copyright", "corresponding", "author", "authors", "email", "mail",
    "phone", "fax", "doi", "issn", "udc", "msc", "ams", "federal", "state", "national",
    "university", "universidade", "institute", "academy", "department", "faculty", "center",
    "centre", "laboratory", "school", "college", "research", "moscow", "russia", "prague",
    "brazil", "rio", "janeiro", "republic", "аннотация", "резюме", "введение", "ключевые",
    "слова", "журнал", "том", "номер", "страницы", "поступила", "редакцию", "принята",
    "теорема", "лемма", "определение", "пример", "замечание", "доказательство", "университет",
    "институт", "академия", "факультет", "кафедра", "центр", "лаборатория", "москва", "россия",
}

AUTHOR_ITEM_RX = re.compile(
    r"(?:"
    r"(?P<init1>[A-ZА-ЯЁ]\.(?:\s*[A-ZА-ЯЁ]\.)?)\s*(?P<sur1>[A-ZА-ЯЁ][a-zа-яё'’-]{2,})|"
    r"(?P<first3>[A-ZА-ЯЁ][a-zа-яё]{1,})\s+(?:(?P<mid3>[A-ZА-ЯЁ]\.)\s+)?(?P<sur3>[A-ZА-ЯЁ][a-zа-яё'’-]{2,})|"
    r"(?P<sur2>[A-ZА-ЯЁ][a-zа-яё'’-]{2,})\s+(?P<init2>[A-ZА-ЯЁ]\.(?:\s*[A-ZА-ЯЁ]\.)?)(?!\s+[A-ZА-ЯЁ][a-zа-яё]{2,})"
    r")(?P<idx>[⁰¹²³⁴⁵⁶⁷⁸⁹*†‡]+(?:,[⁰¹²³⁴⁵⁶⁷⁸⁹*†‡]+)*|(?:\d+(?:,\d+)*))?",
)

ORG_PREFIX_RX = re.compile(
    r"^\s*(?:(?P<sup1>[⁰¹²³⁴⁵⁶⁷⁸⁹*†‡]+)|(?P<num1>\d{1,2})\s*[.)]?|(?:\((?P<num2>\d{1,2})\)))\s+"
)


def _get_level() -> str:
    return os.environ.get("MATHKG_M01", "model").strip().lower()


def _get_threshold() -> float:
    return float(os.environ.get("MATHKG_M01_THR", "0.7"))


def _get_cache_path() -> str:
    default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".m01_llm_cache.json")
    return os.environ.get("MATHKG_M01_CACHE", default_path)


def _norm_sup(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    s = raw.translate(SUP_TRANS)
    parts = [p.strip() for p in re.split(r"[,;\s]+", s) if p.strip()]
    out: List[str] = []
    for p in parts:
        if p.isdigit() and len(p) > 1 and all(ch != "0" for ch in p):
            out.extend(list(p))
        else:
            out.append(p)
    return out


def _clean_space(s: str) -> str:
    return " ".join(s.split())


def _is_valid_person_tokens(words: Sequence[str]) -> bool:
    for w in words:
        lw = w.lower().strip(".,:;()[]")
        if not lw or lw in NON_PERSON_WORDS or any(ch.isdigit() for ch in lw):
            return False
    return True


def _parse_person_name(raw_name: str) -> Optional[Tuple[str, str, str, str]]:
    """Возвращает (display_name, surname, initials, full_name_or_empty) или None."""
    name = _clean_space(raw_name.strip(" ,.;:*†‡⁰¹²³⁴⁵⁶⁷⁸⁹"))
    if not name or ORG_CUE_RX.search(name):
        return None
    tokens = name.split()
    if len(tokens) < 2 or len(tokens) > 4:
        return None
    if not _is_valid_person_tokens(tokens):
        return None

    # Формат «Иванов А. Б.»
    if len(tokens[0]) >= 3 and "." not in tokens[0] and all(re.fullmatch(r"[A-ZА-ЯЁ]\.", t) for t in tokens[1:]):
        surname = tokens[0]
        initials = " ".join(tokens[1:])
        return name, surname, initials, ""

    # Формат «А. Б. Иванов»
    if all(re.fullmatch(r"[A-ZА-ЯЁ]\.", t) for t in tokens[:-1]) and len(tokens[-1]) >= 3:
        surname = tokens[-1]
        initials = " ".join(tokens[:-1])
        return name, surname, initials, ""

    # Полное имя «John Smith» / «John A. Smith»
    if len(tokens[-1]) >= 3 and "." not in tokens[-1] and len(tokens[0]) >= 2:
        surname = tokens[-1]
        inits = [t if t.endswith(".") else (t[0].upper() + ".") for t in tokens[:-1]]
        return name, surname, " ".join(inits), name

    return None


def _extract_title(doc: Doc, head: str) -> Optional[Tuple[int, int, str]]:
    page_h = max(790.0, max((ln["bbox"][3] for ln in doc.first_page_lines), default=800.0))
    body = doc.meta.get("quality", {}).get("body_size", 10)
    lines = [
        ln for ln in doc.first_page_lines
        if len(ln["text"].strip()) > 3
        and ln["bbox"][1] < page_h * 0.55
        and re.search(r"[A-Za-zА-Яа-яЁё]{3}", ln["text"])
        and not ln["text"].lstrip().startswith(("arXiv:", "//", "ISSN", "DOI", "http"))
        and (ln["bbox"][2] - ln["bbox"][0]) > (ln["bbox"][3] - ln["bbox"][1])
        and not UDC_RX.search(ln["text"])
        and not MSC_RX.search(ln["text"])
        and not EMAIL_RX.search(ln["text"])
        and not CITE_RU_RX.search(ln["text"])
        and not CITE_EN_VOL_RX.search(ln["text"])
        and not CITE_EN_SHORT_RX.search(ln["text"])
    ]
    if not lines:
        return None

    big = max(ln["size"] for ln in lines)
    cand = [ln for ln in lines if abs(ln["size"] - big) < 0.6]
    if big <= body + 0.5:
        styled_cand = [ln for ln in lines if ln["bold"] or ln["text"].strip().isupper()]
        if styled_cand:
            cand = styled_cand[:4]

    pos, start, end = 0, -1, -1
    for ln in cand:
        chunk = ln["text"].strip()
        k = doc.text.find(chunk, pos)
        if k < 0 or (start >= 0 and k - end > 15):
            break
        start = k if start < 0 else start
        end = pos = k + len(chunk)

    if start >= 0 and end > start:
        return start, end, _clean_space(doc.text[start:end])
    return None


def _extract_abstract(doc: Doc, head: str, title_end: int, kw_start: int) -> Optional[Tuple[int, int, str]]:
    rxs = (ABSTRACT_RU_RX, ABSTRACT_EN_RX) if doc.lang == "ru" else (ABSTRACT_EN_RX, ABSTRACT_RU_RX)
    for rx in rxs:
        m = rx.search(doc.text[:max(len(head), 6000)])
        if m:
            stop = STOP_ABSTRACT_RX.search(doc.text, m.end())
            if stop and stop.start() - m.end() < 4000:
                end = stop.start()
            else:
                nn = doc.text.find("\n\n", m.end())
                end = nn if nn > m.end() else min(len(doc.text), m.end() + 2500)
            if end > m.end() + 10:
                return m.end(), end, _clean_space(doc.text[m.end():end])

    # Фоллбек для журналов без слова «Abstract» (например, Kybernetika)
    sec_m = FIRST_SEC_RX.search(doc.text)
    upper_bound = min(sec_m.start() if sec_m else len(head), kw_start or len(head))
    for p in doc.paragraphs:
        if p.page > 2 or p.end <= title_end:
            continue
        if upper_bound > title_end and p.start >= upper_bound:
            break
        txt = doc.text[p.start:p.end].strip()
        if not (120 <= len(txt) <= 3000):
            continue
        if EMAIL_RX.search(txt) or UDC_RX.search(txt) or MSC_RX.search(txt) or DOI_RX.search(txt):
            continue
        if ORG_CUE_RX.search(txt) and len(txt) < 300:
            continue
        if RECEIVED_CTX_RX.search(txt) or CITE_RU_RX.search(txt) or CITE_EN_VOL_RX.search(txt):
            continue
        if len(txt.split()) >= 15 and "." in txt:
            return p.start, p.end, _clean_space(txt)
    return None


def _extract_keywords(doc: Doc) -> Optional[Tuple[int, int, List[str]]]:
    limit = doc.pages[1].end if len(doc.pages) > 1 else len(doc.text)
    rxs = (KEYWORDS_RU_RX, KEYWORDS_EN_RX) if doc.lang == "ru" else (KEYWORDS_EN_RX, KEYWORDS_RU_RX)
    for rx in rxs:
        m = rx.search(doc.text[:limit])
        if m:
            nn = doc.text.find("\n\n", m.end())
            end = nn if 0 < nn - m.end() < 500 else min(len(doc.text), m.end() + 300)
            if end <= m.end():
                continue
            raw_list = doc.text[m.end():end].rstrip(". \n")
            kws = []
            for kw in re.split(r"\s*[;,]\s*", raw_list):
                kw = _clean_space(kw)
                if 2 < len(kw) < 80:
                    kws.append(kw)
            return m.end(), end, kws
    return None


def _extract_english_title_dub(doc: Doc, main_title: str) -> Optional[str]:
    if doc.lang != "ru":
        return None
    m = ABSTRACT_EN_RX.search(doc.text)
    if not m or m.start() < 200:
        return None
    window = doc.text[max(0, m.start() - 1200):m.start()]
    for line in reversed([ln.strip() for ln in window.splitlines() if ln.strip()]):
        if EMAIL_RX.search(line) or ORG_CUE_RX.search(line) or AUTHOR_ITEM_RX.fullmatch(line):
            continue
        if KEYWORDS_RU_RX.match(line) or UDC_RX.search(line) or MSC_RX.search(line):
            continue
        if len(line) >= 12 and re.search(r"[A-Za-z]{4}", line) and not re.search(r"[А-Яа-яЁё]", line):
            cand = _clean_space(line)
            if cand.lower() != main_title.lower():
                return cand
    return None


def _extract_citation(doc: Doc, head: str) -> Dict[str, Optional[str]]:
    res: Dict[str, Optional[str]] = {
        "journal": None, "year": None, "volume": None, "issue": None,
        "pages": None, "platform_type": None,
    }
    if ARXIV_RX.search(head):
        res["journal"] = "arXiv"
        res["platform_type"] = "preprint"

    for rx, kind in ((CITE_RU_RX, "ru"), (CITE_EN_VOL_RX, "en_vol"), (CITE_EN_SHORT_RX, "en_short")):
        m = rx.search(head)
        if not m:
            continue
        if kind == "ru":
            journal, year, vol, iss, pages = m.groups()
        elif kind == "en_vol":
            journal, vol, year, iss, pages = m.groups()
        else:
            journal, year, vol, iss, pages = m.groups()
        res["journal"] = _clean_space(journal.strip(" .,;/"))
        res["platform_type"] = "journal"
        res["year"] = year
        res["volume"] = vol
        res["issue"] = iss
        res["pages"] = _clean_space(pages) if pages else None
        break
    return res


def _extract_year(head: str, cite_year: Optional[str], abs_span: Optional[Tuple[int, int]]) -> Optional[int]:
    if cite_year and cite_year.isdigit():
        return int(cite_year)
    candidates: List[Tuple[int, int]] = []  # (priority, year)
    for m in YEAR_RX.finditer(head[:3500]):
        if abs_span and abs_span[0] <= m.start() < abs_span[1]:
            continue
        ctx_before = head[max(0, m.start() - 55):m.start()]
        if RECEIVED_CTX_RX.search(ctx_before):
            continue
        prio = 2 if PUBLISHED_CTX_RX.search(ctx_before) else 1
        candidates.append((prio, int(m.group(1))))
    if not candidates:
        return None
    best_prio = max(p for p, _ in candidates)
    return max(y for p, y in candidates if p == best_prio)


def _split_author_org_zone(zone: str, zone_offset: int) -> Tuple[List[dict], List[dict]]:
    authors: List[dict] = []
    orgs: List[dict] = []

    for m_line in re.finditer(r"[^\n]+", zone):
        raw_line = m_line.group(0).strip()
        if not raw_line:
            continue
        line_start = zone_offset + m_line.start()
        line_end = zone_offset + m_line.end()
        if UDC_RX.search(raw_line) or MSC_RX.search(raw_line) or DOI_RX.search(raw_line):
            continue
        if ARXIV_RX.search(raw_line) or RECEIVED_CTX_RX.search(raw_line):
            continue

        pref_m = ORG_PREFIX_RX.match(raw_line)
        has_org_cue = bool(ORG_CUE_RX.search(raw_line))
        has_email = bool(EMAIL_RX.search(raw_line))

        # Строка организации
        if has_org_cue or (pref_m and not AUTHOR_ITEM_RX.match(raw_line)):
            idx_list = _norm_sup(pref_m.group("sup1") or pref_m.group("num1") or pref_m.group("num2")) if pref_m else []
            body_line = raw_line[pref_m.end():] if pref_m else raw_line
            # Отрезаем хвост e-mail/ORCID, если они в той же строке
            body_line = EMAIL_RX.sub("", body_line)
            body_line = re.sub(r"\b(?:e-?mail|mail|ORCID)\s*:?\s*", "", body_line, flags=re.I)
            org_name = _clean_space(body_line.strip(" ,;:."))
            if len(org_name) >= 5 and (has_org_cue or idx_list):
                orgs.append({
                    "name": org_name,
                    "indices": idx_list,
                    "start": line_start,
                    "end": line_end,
                    "method": "rule",
                    "confidence": 0.85 if idx_list else 0.75,
                })
            continue

        if has_email and not AUTHOR_ITEM_RX.search(EMAIL_RX.sub("", raw_line)):
            continue

        # Строка авторов
        clean_line = EMAIL_RX.sub("", raw_line)
        for am in AUTHOR_ITEM_RX.finditer(clean_line):
            idx_raw = am.group("idx")
            raw_name = am.group(0)
            if idx_raw:
                raw_name = raw_name[:len(raw_name) - len(idx_raw)]
            parsed = _parse_person_name(raw_name)
            if not parsed:
                continue
            disp, sur, init, full = parsed
            authors.append({
                "name": disp,
                "surname": sur,
                "initials": init,
                "full_name": full,
                "indices": _norm_sup(idx_raw),
                "start": line_start + am.start(),
                "end": line_start + am.end(),
                "method": "rule",
                "confidence": 0.85,
            })
    return authors, orgs


def _run_ner_stage(zone: str, zone_offset: int, authors: List[dict], orgs: List[dict], threshold: float) -> None:
    if not zone.strip():
        return
    try:
        from mathkg.models import get_pipeline
        ner = get_pipeline("token-classification", NER_MODEL, aggregation_strategy="simple")
        preds = ner(zone[:1200])
    except Exception:
        return

    per_spans: List[Tuple[int, int, str, float]] = []
    org_spans: List[Tuple[int, int, str, float]] = []
    for ent in preds:
        grp = ent.get("entity_group") or ent.get("entity")
        score = float(ent.get("score", 0.0))
        if score < threshold:
            continue
        s0 = zone_offset + int(ent["start"])
        s1 = zone_offset + int(ent["end"])
        word = _clean_space(str(ent.get("word", "")))
        if grp == "PER":
            per_spans.append((s0, s1, word, score))
        elif grp in ("ORG", "LOC"):
            org_spans.append((s0, s1, word, score))

    # Отбрасываем кандидатов правил, которые NER уверенно пометил как ORG/LOC (без PER-перекрытия)
    kept_authors: List[dict] = []
    for a in authors:
        has_per = any(s0 < a["end"] and a["start"] < s1 for s0, s1, _, _ in per_spans)
        has_org = any(s0 < a["end"] and a["start"] < s1 for s0, s1, _, _ in org_spans)
        if has_org and not has_per:
            continue
        if has_per:
            best_sc = max(sc for s0, s1, _, sc in per_spans if s0 < a["end"] and a["start"] < s1)
            a["confidence"] = round(max(a["confidence"], best_sc), 3)
        kept_authors.append(a)
    authors[:] = kept_authors

    # Добавляем авторов, найденных NER и пропущенных правилами
    existing_iris = {person_iri(a["surname"], a["initials"]) for a in authors}
    for s0, s1, word, sc in per_spans:
        parsed = _parse_person_name(word)
        if not parsed:
            continue
        disp, sur, init, full = parsed
        pi = person_iri(sur, init)
        if pi in existing_iris:
            continue
        existing_iris.add(pi)
        authors.append({
            "name": disp,
            "surname": sur,
            "initials": init,
            "full_name": full,
            "indices": [],
            "start": s0,
            "end": s1,
            "method": "model",
            "confidence": round(sc, 3),
        })

    # Добавляем организации, найденные NER
    existing_orgs = {org_iri(o["name"]) for o in orgs}
    for s0, s1, word, sc in org_spans:
        if len(word) < 5 or not ORG_CUE_RX.search(word):
            continue
        oi = org_iri(word)
        if oi in existing_orgs:
            continue
        existing_orgs.add(oi)
        orgs.append({
            "name": word,
            "indices": [],
            "start": s0,
            "end": s1,
            "method": "model",
            "confidence": round(sc, 3),
        })


def _load_llm_cache(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}
    return {}


def _save_llm_cache(path: str, cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _verified_in_text(val: Optional[str], text_norm: str) -> bool:
    if not val:
        return False
    v = _clean_space(str(val)).lower()
    return len(v) >= 2 and v in text_norm


def _run_llm_stage(doc: Doc, cite: Dict[str, Optional[str]], authors: List[dict], orgs: List[dict]) -> None:
    need_llm = not cite["journal"] or not authors or (not orgs and bool(EMAIL_RX.search(doc.text[:2500])))
    if not need_llm:
        return

    snippet = doc.text[:1500]
    key = f"{doc.doc_id}:{hashlib.sha1(snippet.encode('utf-8')).hexdigest()[:12]}"
    cache_path = _get_cache_path()
    cache = _load_llm_cache(cache_path)
    parsed_json = cache.get(key)

    if parsed_json is None:
        try:
            from mathkg.models import generate
            prompt = (
                "Извлеки метаданные из начала научной статьи и верни ТОЛЬКО валидный JSON без пояснений по схеме: "
                '{"journal": str|null, "year": int|null, "volume": str|null, "issue": str|null, "pages": str|null, '
                '"authors": [{"name": str, "affiliation_index": str|null}], '
                '"affiliations": [{"index": str|null, "name": str}]}.\n\n'
                f"Текст:\n{snippet}"
            )
            raw = generate([[{"role": "user", "content": prompt}]], max_new_tokens=320)[0]
            m = re.search(r"\{.*\}", raw, re.S)
            parsed_json = json.loads(m.group(0)) if m else {}
            cache[key] = parsed_json
            _save_llm_cache(cache_path, cache)
        except Exception:
            return

    if not isinstance(parsed_json, dict):
        return
    check_text = _clean_space(doc.text[:2500]).lower()

    if not cite["journal"] and _verified_in_text(parsed_json.get("journal"), check_text):
        cite["journal"] = _clean_space(str(parsed_json["journal"]))
        cite["platform_type"] = cite["platform_type"] or "journal"
        for k in ("volume", "issue", "pages"):
            if not cite[k] and parsed_json.get(k) and _verified_in_text(str(parsed_json[k]), check_text):
                cite[k] = _clean_space(str(parsed_json[k]))
        if not cite["year"] and parsed_json.get("year") and str(parsed_json["year"]) in check_text:
            cite["year"] = str(parsed_json["year"])

    existing_orgs = {org_iri(o["name"]) for o in orgs}
    for aff in parsed_json.get("affiliations") or []:
        if not isinstance(aff, dict):
            continue
        aname = _clean_space(str(aff.get("name") or ""))
        if len(aname) >= 5 and _verified_in_text(aname, check_text):
            oi = org_iri(aname)
            if oi not in existing_orgs:
                existing_orgs.add(oi)
                idx = str(aff["index"]).strip() if aff.get("index") else ""
                orgs.append({
                    "name": aname,
                    "indices": [idx] if idx else [],
                    "start": -1,
                    "end": -1,
                    "method": "llm",
                    "confidence": 0.75,
                })

    existing_authors = {person_iri(a["surname"], a["initials"]) for a in authors}
    for au in parsed_json.get("authors") or []:
        if not isinstance(au, dict):
            continue
        pname = _clean_space(str(au.get("name") or ""))
        if not _verified_in_text(pname, check_text):
            continue
        parsed = _parse_person_name(pname)
        if not parsed:
            continue
        disp, sur, init, full = parsed
        pi = person_iri(sur, init)
        if pi in existing_authors:
            continue
        existing_authors.add(pi)
        idx = str(au["affiliation_index"]).strip() if au.get("affiliation_index") else ""
        authors.append({
            "name": disp,
            "surname": sur,
            "initials": init,
            "full_name": full,
            "indices": [idx] if idx else [],
            "start": -1,
            "end": -1,
            "method": "llm",
            "confidence": 0.75,
        })


@tool(
    name="extract_metadata",
    module="m01",
    requires=(),
    produces=(
        "moc:FrontMatter", "moc:Title", "moc:Abstract", "moc:KeywordList",
        "scilibreco:Author", "scilibreco:Keyword", "scilibreco:Organization",
        "scilibreco:PublishingPlatform",
    ),
    author="Усков К. И.",
)
def extract_metadata(doc: Doc, ctx: Context) -> Facts:
    """Метаданные статьи: заголовок (включая английский дубль), аннотация (в т.ч. без метки),
    ключевые слова, FrontMatter, авторы, организации и аффилиации, e-mail, ORCID, УДК, MSC, DOI,
    журнал или архив, том, номер, страницы и год публикации."""
    facts = Facts("extract_metadata", doc.doc_id)
    d = doc.iri
    head = doc.text[:doc.pages[0].end] if doc.pages else doc.text[:5000]

    # 1. Заголовок и ключевые слова
    title = _extract_title(doc, head)
    title_end = title[1] if title else 0
    if title:
        t0, t1, t_clean = title
        facts.add_fragment(id="title", cls="moc:Title", start=t0, end=t1, page=1, method="rule", confidence=0.9)
        facts.add(d, "scilibreco:hasTitle", t_clean, literal=True, start=t0, end=t1)
        facts.add(d, "rdfs:label", t_clean, literal=True, start=t0, end=t1)
        en_dub = _extract_english_title_dub(doc, t_clean)
        if en_dub:
            facts.add(d, "rdfs:label", en_dub, literal=True)

    kw_res = _extract_keywords(doc)
    kw_start = kw_res[0] if kw_res else len(head)

    # 2. Аннотация (с меткой на основном языке или позиционный фоллбек)
    abs_m_ru = ABSTRACT_RU_RX.search(head)
    abs_m_en = ABSTRACT_EN_RX.search(head)
    abs_first = abs_m_ru or abs_m_en
    zone_end = abs_first.start() if abs_first else min(kw_start, 2500)

    abs_res = _extract_abstract(doc, head, title_end, kw_start)
    if abs_res:
        a0, a1, a_clean = abs_res
        conf_abs = 0.85 if abs_first else 0.7
        facts.add_fragment(id="abstract", cls="moc:Abstract", start=a0, end=a1, page=doc.page_of(a0),
                           method="rule", confidence=conf_abs)
        facts.add(d, "scilibreco:hasAbstract", a_clean, literal=True, start=a0, end=a1, confidence=conf_abs)
        if not abs_first and a0 < zone_end:
            zone_end = a0

    if kw_res:
        k0, k1, kws = kw_res
        facts.add_fragment(id="keywords", cls="moc:KeywordList", start=k0, end=k1, page=doc.page_of(k0),
                           method="rule", confidence=0.85)
        for kw in kws:
            norm_kw = normalize_name(kw)
            ki = MP + "keyword/" + slug(norm_kw or kw)
            facts.add(ki, "rdf:type", "scilibreco:Keyword")
            facts.add(ki, "scilibreco:hasName", kw, literal=True)
            if norm_kw:
                facts.add(ki, "scilibreco:hasNormalizedName", norm_kw, literal=True)
            facts.add(d, "scilibreco:hasKeyword", ki)

    # 3. FrontMatter (начальная часть статьи целиком)
    fm_ends = [f.end for f in facts.fragments]
    fm_end = max(fm_ends) if fm_ends else min(len(head), len(doc.text))
    if 0 < fm_end <= len(doc.text):
        facts.add_fragment(id="front", cls="moc:FrontMatter", start=0, end=fm_end, page=1, method="rule", confidence=0.9)
        for f in list(facts.fragments):
            if f.id != "front" and f.end <= fm_end:
                facts.add(f.id, "moc:isPartOf", "front")

    # 4. Литералы идентификаторов: УДК, DOI, MSC, ISSN, строка цитирования и год
    udc_m = UDC_RX.search(head)
    if udc_m:
        facts.add(d, "scilibreco:hasUDC", _clean_space(udc_m.group(1)), literal=True,
                  start=udc_m.start(1), end=udc_m.end(1))

    doi_m = DOI_RX.search(head)
    if doi_m:
        facts.add(d, "moc:hasDOI", doi_m.group(1).strip().lower(), literal=True,
                  start=doi_m.start(1), end=doi_m.end(1))

    seen_msc = set()
    for msc_m in MSC_RX.finditer(doc.text[:20000]):
        for code in re.split(r"\s*[,;]\s*", msc_m.group(1)):
            c = code.strip()
            if c and c not in seen_msc:
                seen_msc.add(c)
                facts.add(d, "moc:hasMSCCode", c, literal=True, start=msc_m.start(1), end=msc_m.end(1))

    cite = _extract_citation(doc, head)

    # 5. Авторы и организации (ступени rules -> model -> llm)
    sec_m = FIRST_SEC_RX.search(head)
    if sec_m and sec_m.start() > title_end:
        zone_end = min(zone_end, sec_m.start())
    zone_end = max(title_end, min(zone_end, len(head), title_end + 2500))
    zone = doc.text[title_end:zone_end]

    authors, orgs = _split_author_org_zone(zone, title_end)
    level = _get_level()
    if level in ("model", "llm"):
        _run_ner_stage(zone, title_end, authors, orgs, _get_threshold())
    if level == "llm":
        _run_llm_stage(doc, cite, authors, orgs)

    # Год публикации после возможного уточнения строки цитирования
    pub_year = _extract_year(head, cite["year"], (abs_res[0], abs_res[1]) if abs_res else None)
    if pub_year is not None:
        facts.add(d, "scilibreco:hasPublicationYear", pub_year, literal=True, confidence=0.85 if cite["year"] else 0.7)

    # Площадка публикации (журнал / arXiv)
    if cite["journal"]:
        plat_iri = platform_iri(cite["journal"])
        facts.add(plat_iri, "rdf:type", "scilibreco:PublishingPlatform")
        facts.add(plat_iri, "scilibreco:hasName", cite["journal"], literal=True)
        if cite["platform_type"]:
            facts.add(plat_iri, "scilibreco:hasPlatformType", cite["platform_type"], literal=True)
        issn_m = ISSN_RX.search(head)
        if issn_m:
            facts.add(plat_iri, "scilibreco:hasISSN", issn_m.group(1).replace("–", "-").upper(), literal=True,
                      start=issn_m.start(1), end=issn_m.end(1))
        facts.add(d, "scilibreco:publishedIn", plat_iri, confidence=0.85)
        if cite["volume"]:
            facts.add(d, "scilibreco:hasVolume", cite["volume"], literal=True)
        if cite["issue"]:
            facts.add(d, "scilibreco:hasIssue", cite["issue"], literal=True)
        if cite["pages"]:
            facts.add(d, "moc:hasPageRange", cite["pages"], literal=True)

    # Организации
    org_by_idx: Dict[str, str] = {}
    all_org_iris: List[str] = []
    for o in orgs:
        oi = org_iri(o["name"])
        all_org_iris.append(oi)
        facts.add(oi, "rdf:type", "scilibreco:Organization", method=o["method"], confidence=o["confidence"])
        facts.add(oi, "scilibreco:hasName", o["name"], literal=True, method=o["method"], confidence=o["confidence"])
        for idx in o["indices"]:
            org_by_idx.setdefault(idx, oi)

    # Почты, ORCID и аффилиации авторов
    emails = [e.strip(".,;").lower() for e in EMAIL_RX.findall(head)]
    orcids = [(m.group(1).upper(), m.start(1)) for m in ORCID_RX.finditer(head)]

    seen_authors = set()
    author_iris_in_order: List[Tuple[str, dict]] = []
    for idx_a, a in enumerate(authors):
        pi = person_iri(a["surname"], a["initials"])
        if pi in seen_authors:
            continue
        seen_authors.add(pi)
        author_iris_in_order.append((pi, a))

        kw_pos = {"start": a["start"], "end": a["end"]} if a["start"] >= 0 else {}
        facts.add(pi, "rdf:type", "scilibreco:Author", method=a["method"], confidence=a["confidence"])
        facts.add(pi, "scilibreco:hasName", a["name"], literal=True, method=a["method"], confidence=a["confidence"])
        facts.add(pi, "scilibreco:hasNormalizedName", slug(f"{a['surname']} {a['initials']}").replace("-", " "),
                  literal=True)
        if a["full_name"]:
            facts.add(pi, "scilibreco:hasFullName", a["full_name"], literal=True)
        facts.add(d, "scilibreco:hasAuthor", pi, method=a["method"], confidence=a["confidence"], **kw_pos)

        # Привязка к организации по индексу (или к единственной организации статьи)
        linked_orgs: List[str] = []
        for idx in a["indices"]:
            if idx in org_by_idx and org_by_idx[idx] not in linked_orgs:
                linked_orgs.append(org_by_idx[idx])
        if not linked_orgs and len(all_org_iris) == 1:
            linked_orgs.append(all_org_iris[0])
        for oi in linked_orgs:
            facts.add(pi, "scilibreco:hasAffiliation", oi, method=a["method"], confidence=a["confidence"])

        # Привязка e-mail по транслиту фамилии (или позиционно, если число совпало)
        sur_slug = slug(a["surname"])
        matched_email = next((em for em in emails if sur_slug[:4] and sur_slug[:4] in em), None)
        if matched_email is None and len(emails) == len(authors) and idx_a < len(emails):
            matched_email = emails[idx_a]
        if matched_email:
            facts.add(pi, "scilibreco:hasEmail", matched_email, literal=True)
            domain = matched_email.split("@", 1)[1]
            if domain not in PUBLIC_EMAIL_DOMAINS:
                for oi in linked_orgs:
                    facts.add(oi, "scilibreco:hasEmailDomain", domain, literal=True)

    # Привязка ORCID по ближайшему автору (или к единственному автору)
    for orcid_val, opos in orcids:
        if len(author_iris_in_order) == 1:
            facts.add(author_iris_in_order[0][0], "scilibreco:hasORCID", orcid_val, literal=True)
        elif author_iris_in_order:
            with_pos = [(abs(a["end"] - opos), pi) for pi, a in author_iris_in_order if a["end"] >= 0]
            if with_pos:
                _, nearest_pi = min(with_pos)
                facts.add(nearest_pi, "scilibreco:hasORCID", orcid_val, literal=True)

    return facts
