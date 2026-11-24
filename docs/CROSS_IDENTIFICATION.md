# Как устроена сквозная идентификация (и что с этим делать)

Вся схема держится на трёх вещах:

1. Готовые функции IRI из `mathkg/common.py` (не собираем ссылки строками руками).
2. Быстрая автопроверка `tools/merge_and_check.py check` перед сдачей — если что-то сломалось, она сразу ткнёт в строку.
3. Финальный склейщик `tools/merge_and_check.py merge` — сам свяжет дубли между студентами по `DOI`, `ORCID`, `email`, домену почты и `ISSN`.

---

## Шаг 1. Как писать ID в своём модуле

**Глобальные IRI — только через `mathkg.common`:**

- статья: `doc_iri(doc_id)` → `mp:doc/A04571`
- автор: `person_iri(surname, initials)` → `mp:person/ivanov-a-b`
- организация: `org_iri(name)` → `mp:org/...`
- журнал/архив: `platform_iri(journal)` → `mp:platform/...`
- понятие: `concept_iri(name)` → `mp:concept/...`
- именованная теорема: `statement_iri(name)` → `mp:statement/...`
- работа в списке литературы: `doi_work_iri(doi)` или `work_iri(surname, year, title)` → `mp:work/...`
- ключевое слово (`m01`): `MP + "keyword/" + slug(normalize_name(kw))`

---

## Шаг 2. Шпаргалка по модулям: кто какие ID создаёт

| Модуль | Свои локальные `id` | Что отдаёт соседям и в граф |
| ------------ | ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `base` | `p-NNNN`, `f-NNNN` | общий `Doc.text` с координатами `[start, end)` и номера формул |
| `m01` | `title`, `abstract`, `keywords`, `front` | авторы/организации/журналы + якоря `DOI`, `ORCID`, `email`, `hasEmailDomain`, `ISSN`; ключевые слова → `m09`; `MSC` и `УДК` → `m10` |
| `m02` | `sec-NNNN`, `cap-NNNN`, `fig-NNNN`, `tbl-NNNN` | номера разделов, рисунков, таблиц → `m08` |
| `m03` | `st-NNNN`, `asm-NNNN`, `cnd-NNNN`, `ccl-NNNN` | утверждения и их `number` → `m07`, `m08` |
| `m04` | `def-NNNN`, `not-NNNN`, `ex-NNNN`, `rem-NNNN` | `concept_iri` и `hasNormalizedName` → `m09`; номера определений → `m08` |
| `m05` | `if-NNNN`, `var-NNNN`, `sym-NNNN` | дописывает `moc:Equation` и `hasNormalizedText` к `f-NNNN` |
| `m06` | `bib`, `ref-NN` | `doi_work_iri` / `work_iri`; записи `ref-NN` → `m08` |
| `m07` | `prf-NNNN`, `step-NNNN`, `case-NNNN` | `moc:proves` → `st-NNNN`; границы доказательств → `m08` |
| `m08` | — (только связи) | связывает ссылки и доказательства с объектами `m02`, `m03`, `m04`, `m06`, `m07` и `f-NNNN` |
| `m09` | — (только связи) | `statement_iri` и `moc:mentionsConcept` → `mp:concept/...` |
| `m10` | — (только связи) | готовые рубрики `scilibreco:subject_*` по `MSC` / `УДК` |

---

## Шаг 3. Проверить свой прогон перед сдачей

После того как прогнал модуль на тестовом наборе и сложил JSON в `facts/`, запусти:

```bash
python tools/merge_and_check.py check work/facts
```

Скрипт вернёт код `1` и покажет точный файл/статью, если:

- два разных модуля создали одинаковый локальный `id` в одной статье;
- в `number` попали слова, скобки или точка в конце;
- кривой `DOI`, `MSC` или `ORCID`;
- кто-то выдумал свой префикс внутри `mp:` (разрешены только `doc`, `person`, `org`, `platform`, `concept`, `statement`, `keyword`, `work`, `source`, `run`, `tool`) или вставил левый URI.

---

## Шаг 4. Склеить графы всех студентов в один

Разные студенты и разные языки всё равно дадут расхождения («А. Б. Иванов» vs «A. B. Ivanov», «МГУ» vs «Lomonosov MSU», `mp:doc/A001` vs `mp:work/doi-...`). Чтобы ничего не сводить руками, в конце прогоняем общий склейщик:

```bash
# Вариант А (для GraphDB): не ломает старые IRI, просто дописывает owl:sameAs и убирает дубли
python tools/merge_and_check.py merge merged.nt student-01.nt student-02.nt --mode sameas

# Вариант Б (для pyoxigraph / rdflib): сразу переписывает дубли на один канонический IRI
python tools/merge_and_check.py merge merged.nt student-01.nt student-02.nt --mode rewrite
```

Скрипт сам:

- склеит статью `mp:doc/...` и её цитирование `mp:work/doi-...` по `DOI` (строки литературы `/ref-NN` не трогает);
- склеит дубли одного автора по `ORCID` или `email`;
- склеит организации по корпоративному `hasEmailDomain` (публичные домены вроде `gmail.com`, `mail.ru`, `yandex.ru` игнорируются);
- склеит названия одного журнала по `ISSN`.

Самотест скрипта (проверяет и `check`, и `merge`):

```bash
python tools/merge_and_check.py test
```

---

## Шпаргалка для промпта твоей нейронки

Если просишь ИИ написать или доработать модуль, просто закинь ей эти 5 пунктов:

```text
1. Все глобальные IRI бери только функциями из mathkg/common.py (doc_iri, person_iri, org_iri, platform_iri, concept_iri, statement_iri, doi_work_iri, work_iri).
2. Не меняй формат id фрагментов своего модуля, чтобы не было коллизий с другими модулями.
3. Поле number пиши без слов, скобок и точки в конце ("2.1", а не "Теорема 2.1.").
4. Форматы: DOI в нижнем регистре без URL, MSC без слова "MSC" ("46B20"), ORCID без URL ("0000-0002-1825-0097").
5. После изменений прогони python tools/merge_and_check.py check <папка_facts> и исправь все ошибки.
```
