"""Модуль NN. <Название> — версия студента <Фамилия И. О.>.

Файл кладётся в папку modules/ репозитория группы под именем mNN_<название>.py (номер модуля из
варианта) и попадает в main через запрос на слияние своей ветки student-NN. Декоратор @tool с тем же name, что у базовой версии, заменяет базовую версию в
конвейере и в агенте. Менять name, requires и формат результата нельзя: от них зависят другие
модули. Внутри функции можно всё: правила, модели Hugging Face, языковую модель.
"""
from __future__ import annotations

from mathkg.model import Context, Doc, Facts
from mathkg.registry import tool

# Модели берутся из общего кэша mathkg.models: модель загружается один раз за сеанс, и все модули
# получают одну её копию. Образец ступени модели — modules_template/m04_model_example.py.
# from mathkg.models import get_pipeline, get_sentence_model
# zs = get_pipeline("zero-shot-classification", "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli")


@tool(name="extract_statements",          # имя базовой версии модуля NN — не менять
      module="mNN",
      requires=(),                         # как у базовой версии — не менять
      produces=("moc:Theorem",),
      author="Фамилия И. О.")
def extract_statements(doc: Doc, ctx: Context) -> Facts:
    """Описание для агента: что находит инструмент и что возвращает (одно-два предложения).
    Агент выбирает инструмент по этому тексту, поэтому он пишется для читателя-модели."""
    facts = Facts("extract_statements", doc.doc_id)
    # 1. правила (можно начать с копии базовой версии из mathkg/modules/)
    # 2. модель Hugging Face для случаев, которые правила пропускают
    # 3. языковая модель — только там, где без неё не обойтись (медленно)
    # Каждый фрагмент: facts.add_fragment(id=..., cls=..., start=..., end=..., method=..., confidence=...)
    # Каждая связь:    facts.add(subj, pred, obj, method=..., confidence=...)
    return facts
