# Пакет mathkg

Общий код практики: основа `base`, десять модулей извлечения (базовые версии), реестр
инструментов, прогон по набору, проверка контракта, перевод в RDF. Студенты используют пакет
и заменяют базовую версию своего модуля своей.

## Подключение в Colab

Пакет лежит в репозитории группы `kg-practice-2026`. В Colab репозиторий клонируется ячейкой
подключения (`ИНСТРУКЦИЯ_git.md` в корне репозитория), после чего:

```python
import sys
sys.path.insert(0, '/content/kg-practice-2026')                  # клон репозитория группы
!pip install -q -r /content/kg-practice-2026/requirements.txt
```

Распознаванию формул нужен ещё `pip install pix2tex` и GPU.

## Состав

| Файл | Назначение |
|---|---|
| `common.py` | нормализация имён, IRI понятий, утверждений, персон, организаций, журналов, работ |
| `model.py` | типы данных: `Doc` (статья после base), `Fragment`, `Relation`, `Facts`, `Context` |
| `base/` | PDF → `Doc`: строки со шрифтами, порядок колонок, колонтитулы, сноски, переносы, абзацы, предложения, выключные формулы с номерами, гиперссылки PDF, проверка текстового слоя, распознавание LaTeX формул |
| `registry.py` | декораторы `@tool` и `@query_tool`, порядок запуска по зависимостям, `reset`, инструменты для агента (`agent_tools`) |
| `pipeline.py` | прогон модулей по набору с продолжением после обрыва, сводные `fragments.jsonl` и `relations.jsonl` |
| `check.py` | проверка контракта модуля и отчёт сборки |
| `evaluate.py` | точность, полнота и F1 модуля по ручному эталону (цитаты фрагментов в `gold/mNN.jsonl`) |
| `to_rdf.py` | статья и результаты модулей → N-Triples по модели данных |
| `models.py` | общий кэш моделей: `get_llm()` (Qwen2.5-1.5B-Instruct), `get_pipeline(task, model)`, `get_sentence_model(name)`, `generate(prompts)`; одна копия модели на сеанс для всех модулей |
| `gitwork.py` | работа студента с репозиторием группы из Colab: `Repo.start()` (своя ветка и свежий `main`), `save_notebook`, `collect` (результаты этапа в папку сдачи), `submit` (только свои файлы, ветка на GitHub, ссылка на запрос на слияние) |
| `tools_text.py` | встроенные инструменты запросов к тексту статьи: `get_article_text(doc_id, start_paragraph, n_paragraphs)`, `find_in_article(doc_id, phrase)`; папка base — переменная окружения `KG_BASE_DIR` |
| `modules/m01…m10` | базовые версии модулей |
| `modules/_util.py` | помощники модулей (часть интерфейса, студенческие модули ими пользуются): `header_re`, `italic_share`, `is_bold`, `paragraph_at`, `numbered`, `STATEMENT_WORDS`, `NUM` |
| `modules_template/mNN_template.py` | шаблон студенческого модуля |
| `modules_template/m04_model_example.py` | образец модуля с двумя ступенями (правила и классификатор) для этапа 4: модуль 4 (m04), ступень и порог задаются переменными `MATHKG_M04`, `MATHKG_M04_THR` |

## Модули и зависимости

| Модуль | Инструмент | Зависит от | Слой |
|---|---|---|---|
| m01 Метаданные | `extract_metadata` | — | 1 |
| m02 Структура | `extract_structure` | — | 1 |
| m03 Утверждения | `extract_statements` | — | 1 |
| m04 Определения | `extract_definitions` | — | 1 |
| m05 Формулы | `extract_formulas` | — | 1 |
| m06 Литература | `extract_bibliography` | — | 1 |
| m07 Доказательства | `extract_proofs` | m03 | 2 |
| m09 Понятия | `extract_concepts` | m01, m04 | 2 |
| m10 Предметные области | `classify_topics` | m01 | 2 |
| m08 Ссылки и зависимости | `extract_references` | m02, m03, m04, m06, m07 | 3 |

Слой — только порядок запуска. Все модули разрабатываются одновременно: модуль второго и
третьего слоя с первого дня работает на результатах базовых версий.

## Основные вызовы

```python
from mathkg.base import build_corpus, read_doc, recognize_formulas
from mathkg import pipeline, registry
from mathkg.check import check_module, build_report
from mathkg.evaluate import evaluate
from mathkg.to_rdf import corpus_to_ntriples

build_corpus(WORK + '/index.csv', WORK + '/pdf', WORK + '/base')        # PDF -> Doc, с продолжением
pipeline.setup(student_modules=KG + '/modules')                          # KG — клон репозитория группы
pipeline.run_corpus(WORK + '/base', WORK + '/facts')                     # все модули
pipeline.run_corpus(WORK + '/base', WORK + '/facts', redo=['extract_proofs'])  # пересчёт своего
build_report(WORK + '/base', WORK + '/facts', ONTOLOGY)                  # таблица сборки
evaluate('extract_proofs', GOLD + '/m07.jsonl', TEST + '/base', TEST + '/facts')  # качество модуля
corpus_to_ntriples(WORK + '/base', WORK + '/facts', WORK + '/graph.nt', 'student-07')
tools = registry.agent_tools(lambda doc_id: read_doc(WORK + '/base', doc_id))
```

## Проверено

Прогон на семи PDF (статьи из arXiv и журналов Kybernetika, Int. J. Appl. Math. Comput. Sci.,
русский журнал, свёрстанный в Word): `base` — 0,1–2 с на статью (66 страниц — 2 с, со ссылками —
до 10 с), все десять модулей проходят проверку контракта, граф — 95 тыс. троек, читается `rdflib`.
Распознавание формул (`recognize_formulas`, pix2tex) в этой проверке не запускалось.

## Известные ограничения base

* Пределы сумм и интегралов («i=1» под знаком суммы) иногда отделяются от своей формулы.
* Числа в таблицах и строки кода иногда принимаются за выключные формулы.
* Скан с распознанным текстовым слоем проверку текстового слоя проходит; шум в нём
  («[0, l]2 —•») остаётся и виден в результатах модулей.
* Положение ссылки PDF в тексте определяется по строке шрифта; если найти текст ссылки внутри
  неё не удалось, ссылка покрывает весь фрагмент строки.
* Сноски вынимаются из текста и хранятся в `Doc.footnotes` без позиций, поэтому фрагменты
  `moc:Footnote` модуль 2 создать не может.
* Модуль 10 и словарь понятий модуля 9 строят связи по всему набору; они не помещаются в результат
  одной статьи и пишутся отдельным файлом N-Triples (этап 7).
