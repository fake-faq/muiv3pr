"""Общий кэш моделей Hugging Face: каждая модель загружается один раз за сеанс, и все модули,
которым она нужна, получают одну и ту же копию. Модули не загружают модели сами.

    from mathkg.models import get_llm, get_pipeline, get_sentence_model
    tok, llm = get_llm()                                          # Qwen2.5-1.5B-Instruct, fp16
    zsc = get_pipeline("zero-shot-classification", "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli")
    enc = get_sentence_model("intfloat/multilingual-e5-base")
"""
from __future__ import annotations

from typing import Dict, Tuple

LLM_NAME = "Qwen/Qwen2.5-1.5B-Instruct"      # общая языковая модель модулей группы

_CACHE: Dict[Tuple, object] = {}


def _device() -> int:
    try:
        import torch
        return 0 if torch.cuda.is_available() else -1
    except ImportError:
        return -1


def get_llm(name: str = LLM_NAME):
    """(токенизатор, модель) языковой модели; fp16 на GPU. Токенизатор с padding_side='left'
    для пакетной генерации."""
    key = ("llm", name)
    if key not in _CACHE:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        tok = AutoTokenizer.from_pretrained(name, padding_side="left")
        model = AutoModelForCausalLM.from_pretrained(
            name, torch_dtype=torch.float16 if _device() == 0 else torch.float32,
            device_map="cuda" if _device() == 0 else None)
        model.eval()
        _CACHE[key] = (tok, model)
    return _CACHE[key]


def get_pipeline(task: str, model: str, **kwargs):
    """Конвейер transformers (zero-shot-classification, token-classification, text-classification)."""
    key = ("pipeline", task, model, tuple(sorted(kwargs.items())))
    if key not in _CACHE:
        from transformers import pipeline
        _CACHE[key] = pipeline(task, model=model, device=_device(), **kwargs)
    return _CACHE[key]


def get_sentence_model(name: str = "intfloat/multilingual-e5-base"):
    """Кодировщик sentence-transformers; на GPU — в fp16."""
    key = ("st", name)
    if key not in _CACHE:
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer(name, device="cuda" if _device() == 0 else "cpu")
        if _device() == 0:
            m.half()
        _CACHE[key] = m
    return _CACHE[key]


def generate(prompts, max_new_tokens: int = 256, batch_size: int = 8, name: str = LLM_NAME):
    """Детерминированная генерация (без сэмплирования) для списка сообщений чата: каждый элемент —
    список {'role', 'content'}. Возвращает тексты ответов."""
    import torch
    tok, model = get_llm(name)
    out = []
    for i in range(0, len(prompts), batch_size):
        batch = [tok.apply_chat_template(m, tokenize=False, add_generation_prompt=True)
                 for m in prompts[i:i + batch_size]]
        enc = tok(batch, return_tensors="pt", padding=True).to(model.device)
        with torch.no_grad():
            ids = model.generate(**enc, max_new_tokens=max_new_tokens, do_sample=False,
                                 pad_token_id=tok.eos_token_id)
        out += tok.batch_decode(ids[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)
    return out


def clear() -> None:
    """Освобождает все модели (перед загрузкой крупной модели агента)."""
    _CACHE.clear()
    try:
        import gc, torch
        gc.collect(); torch.cuda.empty_cache()
    except ImportError:
        pass
