# common.py — общие функции практики. Запуск "python common.py" печатает самопроверку.
import re, unicodedata
import pymorphy3                      # pip install pymorphy3 pymorphy3-dicts-ru

MP = "https://w3id.org/mocassinplus/resource/"
_morph = pymorphy3.MorphAnalyzer()

# латинские буквы, похожие на кириллические (ошибки распознавания в заглавиях)
_HOMO = str.maketrans("ABCEHKMOPTXaceopxy", "АВСЕНКМОРТХасеорху")

_TR = {'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ж':'zh','з':'z','и':'i','й':'y',
       'к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u',
       'ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh','щ':'shch','ъ':'','ы':'y','ь':'',
       'э':'e','ю':'yu','я':'ya'}

def fix_homoglyphs(word: str) -> str:
    """В слове, где есть кириллица, заменяет похожие латинские буквы кириллическими."""
    return word.translate(_HOMO) if re.search('[а-яёА-ЯЁ]', word) else word

_SKIP = {'PREP', 'CONJ', 'PRCL', 'INTJ'}

# леммы, которые правило ниже выбирает неверно (проверено на словаре из 6,8 тыс. словоформ математических текстов)
_LEMMA_FIX = {'пол': 'поле', 'тел': 'тело', 'пар': 'пара', 'баз': 'база', 'дол': 'доля',
              'машин': 'машина', 'грамматик': 'грамматика', 'сам': 'самый', 'отрезка': 'отрезок',
              'кош': 'коши'}

def lemma(word: str) -> str:
    """Каноническая лемма слова, одна для всех его форм.
    Незнакомые словарю слова остаются как есть. Если среди разборов есть фамилия
    (Маркова, Лиувилля), берётся лемма фамилии. Иначе из разборов с той же частью
    речи, что у самого вероятного, берётся наименьшая лемма ('основная' и
    'основного' -> 'основной'), затем применяются исправления _LEMMA_FIX."""
    if not _morph.word_is_known(word):
        return word
    parses = _morph.parse(word)
    surn = [p for p in parses if 'Surn' in p.tag]
    if surn:
        lem = min(p.normal_form for p in surn)
    else:
        pos = parses[0].tag.POS
        lem = min(p.normal_form for p in parses if p.tag.POS == pos)
    lem = lem.replace('ё', 'е')
    return _LEMMA_FIX.get(lem, lem)

_EN_STOP = {'of', 'the', 'a', 'an', 'in', 'on', 'for', 'and', 'with', 'to', 'by'}

def en_lemma(word: str) -> str:
    """Грубое приведение английского слова к единственному числу, одинаковое для всех форм:
    'spaces' -> 'space', 'classes' -> 'class', 'series' -> 'sery' (и для 'series' в ед. ч. тоже)."""
    if len(word) <= 3 or word.endswith(('ss', 'us', 'is')):
        return word
    if word.endswith('ies'):
        return word[:-3] + 'y'
    if word.endswith(('sses', 'shes', 'ches', 'xes')):
        return word[:-2]
    if word.endswith('s'):
        return word[:-1]
    return word

def normalize_name(name: str) -> str:
    """Ключ понятия: леммы значимых слов в алфавитном порядке.
    'абелевой группы', 'Абелева группа' -> 'абелев группа';
    'Banach spaces', 'space, Banach' -> 'banach space'.
    Русское и английское название одного понятия дают разные ключи; их связывает вариант 10."""
    name = unicodedata.normalize('NFC', name).replace('ё', 'е').replace('Ё', 'Е')
    # латиница с диакритикой: Hölder -> Holder, Poincaré -> Poincare (кириллица не трогается)
    name = ''.join(unicodedata.normalize('NFKD', c)[0] if 0xC0 <= ord(c) <= 0x24F else c for c in name)
    lemmas = []
    for w in re.findall(r"[A-Za-zА-Яа-я0-9]+", name):
        abbrev = w.isupper() and len(w) <= 5          # МГУ, РАН, ИПМ: аббревиатуры не лемматизируются
        w = fix_homoglyphs(w).lower()
        if abbrev:
            pass
        elif re.fullmatch('[а-я]+', w):
            if _morph.parse(w)[0].tag.POS in _SKIP:
                continue
            w = lemma(w)
        elif re.fullmatch('[a-z]+', w):
            if w in _EN_STOP:
                continue
            w = en_lemma(w)
        lemmas.append(w)
    return ' '.join(sorted(lemmas))

def slug(key: str) -> str:
    s = ''.join(_TR.get(c, c) for c in key.lower())
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s[:80]

def concept_iri(name: str) -> str:   return MP + "concept/"   + slug(normalize_name(name))
def statement_iri(name: str) -> str: return MP + "statement/" + slug(normalize_name(name))
def person_iri(surname: str, initials: str = "") -> str:
    return MP + "person/" + slug(f"{surname} {initials}")
def work_iri(first_author_surname: str, year, title: str) -> str:
    """Цитируемая работа без DOI: фамилия первого автора, год и три первые по алфавиту леммы названия."""
    words = [w for w in normalize_name(title).split()][:3]
    return MP + "work/" + slug(f"{first_author_surname} {year} {' '.join(words)}")
def doi_work_iri(doi: str) -> str:
    """Цитируемая работа с DOI: IRI строится из DOI, поэтому совпадает у всех студентов."""
    return MP + "work/doi-" + slug(doi.lower().replace('/', '-'))
def org_iri(name: str) -> str:
    """Организация: slug названия без кавычек и служебных слов; разные записи одной организации
    сводит вариант 6 (по домену почты)."""
    return MP + "org/" + slug(normalize_name(name))
def platform_iri(journal: str) -> str:
    """Журнал или архив препринтов: 'Математические заметки' -> mp:platform/matematicheskie-zametki."""
    return MP + "platform/" + slug(journal)
def doc_iri(doc_id: str) -> str:     return MP + f"doc/{doc_id}"
def seg_iri(doc_id: str, n: int, kind: str = "seg") -> str:
    return MP + f"doc/{doc_id}/{kind}-{n:04d}"

if __name__ == "__main__":
    tests = ["основная теорема алгебры", "основной теоремы алгебры", "Абелевой группы", "абелева группа",
             "банахово пространство", "банаховых пространств", "Banach spaces", "Banach space",
             "теорема Лиувилля", "теоремы Лиувилля", "неравенство Коши — Буняковского",
             "задача Дирихле", "задачи Дирихле", "Dirichlet problem", "Dirichlet problems",
             "уравнение Шрёдингера", "уравнения Шрёдингера", "векторное поле", "векторного поля",
             "classes of functions", "class of function", "Sobolev spaces", "пространства Соболева",
             "нормированное пространство", "нормированного пространства"]
    for n in tests:
        k = normalize_name(n); print(f"{n!r:42} -> {k!r:36} {slug(k)}")
    print(person_iri("Иванов", "А. Б."), doi_work_iri("10.1134/S0001434619010012"))
