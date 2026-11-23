"""Работа студента с репозиторием группы из Colab: своя ветка, свежая работа группы, сдача.

Репозиторий клонируется ячейкой подключения (см. ИНСТРУКЦИЯ_git.md), после чего:

    from mathkg.gitwork import Repo
    repo = Repo(KG, student='student-07', module='m07', surname='ivanov', token=userdata.get('GITHUB_TOKEN'))
    repo.start()                          # своя ветка student-07 и всё, что уже принято в main
    ...                                   # работа: свой модуль, эталон, блокнот, отчёт
    repo.submit('Этап 3: модуль на правилах и эталон')

submit() добавляет в коммит только файлы студента (свой модуль, свой эталон, свой инструмент
запросов, свои контрольные запросы и словарь, своя папка сдачи), отправляет ветку на GitHub и
печатает ссылку, по которой открывается запрос на слияние (pull request) в main.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
from typing import List, Optional


class Repo:
    COLAB_DIR = "/content/drive/MyDrive/Colab Notebooks"      # где Colab хранит блокноты студента

    def __init__(self, path: str, student: str, module: str, surname: str, token: str = ""):
        if not re.fullmatch(r"student-\d\d", student):
            raise ValueError("student — номер вида 'student-07'")
        if not re.fullmatch(r"m\d\d", module):
            raise ValueError("module — номер вида 'm07'")
        if not re.fullmatch(r"[a-z][a-z-]*", surname):
            raise ValueError("surname — фамилия латиницей строчными буквами, например 'ivanov'")
        self.path, self.student, self.module, self.surname = path, student, module, surname
        self.nn = module[1:]
        self.token = token or ""

    # ------------------------------------------------------------------ служебное
    def git(self, *args: str, quiet: bool = False) -> int:
        """Команда git в репозитории; вывод печатается без токена. Возвращает код завершения."""
        r = subprocess.run(["git", "-C", self.path, *args], capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        if self.token:
            out = out.replace(self.token, "***")
        if out and not quiet:
            print(out)
        return r.returncode

    @property
    def submit_dir(self) -> str:
        """Папка сдачи студента в репозитории: students/07_ivanov."""
        return os.path.join(self.path, "students", f"{self.nn}_{self.surname}")

    def own_patterns(self) -> List[str]:
        """Файлы, которые студент меняет и сдаёт; остальное в репозитории он не трогает."""
        nn, m = self.nn, self.module
        return [f"modules/{m}_*", f"gold/{m}.jsonl", f"query_tools/q{nn}_*", f"eval/queries_{nn}.jsonl",
                f"vocab/concepts_{nn}.tsv", f"students/{nn}_{self.surname}"]

    def own_files(self) -> List[str]:
        files = []
        for pat in self.own_patterns():
            files += [os.path.relpath(p, self.path) for p in glob.glob(os.path.join(self.path, pat))]
        return sorted(files)

    def foreign_changes(self) -> List[str]:
        """Изменённые файлы вне своих: их submit() не сдаёт (чужой модуль, общий код)."""
        r = subprocess.run(["git", "-C", self.path, "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True)
        own = tuple(p.rstrip("*") for p in self.own_patterns())
        changed = [line[3:].strip().strip('"') for line in r.stdout.splitlines() if line.strip()]
        return [p for p in changed if not p.startswith(own)]

    # ------------------------------------------------------------------ работа
    def start(self) -> None:
        """Начало сеанса: настройка автора коммитов, своя ветка, слияние свежего main группы."""
        self.git("config", "user.name", self.surname)
        self.git("config", "user.email", f"{self.student}@users.noreply.github.com")
        self.git("fetch", "-q", "origin")
        exists_remote = self.git("rev-parse", "--verify", "-q", f"origin/{self.student}", quiet=True) == 0
        exists_local = self.git("rev-parse", "--verify", "-q", self.student, quiet=True) == 0
        if exists_local:
            self.git("checkout", "-q", self.student)
        elif exists_remote:
            self.git("checkout", "-q", "-b", self.student, "--track", f"origin/{self.student}")
        else:
            self.git("checkout", "-q", "-b", self.student, "origin/main")
        if exists_remote:
            self.git("pull", "-q", "--no-rebase", "origin", self.student)
        if self.git("merge", "-q", "--no-edit", "origin/main") != 0:
            self.git("merge", "--abort", quiet=True)
            print("Слияние с main не удалось: в своей ветке изменены общие файлы. Их возвращают к версии "
                  "группы (repo.git('checkout', 'origin/main', '--', 'путь')), сдают и повторяют start().")
        os.makedirs(self.submit_dir, exist_ok=True)
        print(f"ветка {self.student}; папка сдачи {os.path.relpath(self.submit_dir, self.path)}")

    def save_notebook(self, colab_name: str, stage: int) -> Optional[str]:
        """Копия своего блокнота из Google Drive в папку сдачи: students/07_ivanov/stage03.ipynb.
        Перед вызовом блокнот сохраняют (Ctrl+S): копируется последняя сохранённая версия."""
        import shutil
        src = os.path.join(self.COLAB_DIR, colab_name)
        if not os.path.exists(src):
            print("нет файла", src)
            return None
        dst = os.path.join(self.submit_dir, f"stage{stage:02d}.ipynb")
        os.makedirs(self.submit_dir, exist_ok=True)
        shutil.copy2(src, dst)
        return dst

    SMALL = (".md", ".csv", ".tsv", ".rq", ".json", ".jsonl", ".txt", ".log", ".png", ".svg")

    def collect(self, src_dir: str, stage: int, max_kb: int = 1024) -> List[str]:
        """Копия результатов этапа из папки на Drive (practice2026/stageNN) в папку сдачи
        students/NN_фамилия/stageNN: отчёт, таблицы, запросы, журналы — текстовые файлы и картинки
        не больше max_kb. Крупные файлы (векторы, индексы, графы .nt) остаются на Drive."""
        import shutil
        dst_root = os.path.join(self.submit_dir, f"stage{stage:02d}")
        copied, skipped = [], []
        for root, _, names in os.walk(src_dir):
            for n in names:
                src = os.path.join(root, n)
                rel = os.path.relpath(src, src_dir)
                if n.lower().endswith(self.SMALL) and os.path.getsize(src) <= max_kb * 1024:
                    os.makedirs(os.path.dirname(os.path.join(dst_root, rel)), exist_ok=True)
                    shutil.copy2(src, os.path.join(dst_root, rel))
                    copied.append(rel)
                else:
                    skipped.append(rel)
        if not os.path.exists(os.path.join(src_dir, "REPORT.md")):
            print("в", src_dir, "нет REPORT.md")
        print(f"скопировано в {os.path.relpath(dst_root, self.path)}: {len(copied)} файлов"
              + (f"; остались на Drive: {', '.join(skipped[:8])}" if skipped else ""))
        return copied

    def submit(self, message: str) -> None:
        """Сдача: свои файлы — в коммит, ветка — на GitHub; печатает ссылку на запрос на слияние."""
        foreign = self.foreign_changes()
        if foreign:
            print("Эти изменения не сдаются (файлы вне своих):", ", ".join(foreign))
        files = self.own_files()
        if not files:
            print("Своих файлов для сдачи нет.")
            return
        self.git("add", "--", *files)
        if self.git("diff", "--cached", "--quiet", quiet=True) == 0:
            print("Изменений с прошлой сдачи нет.")
        else:
            self.git("commit", "-q", "-m", message)
        if self.git("push", "-q", "-u", "origin", self.student) == 0:
            url = subprocess.run(["git", "-C", self.path, "remote", "get-url", "origin"],
                                 capture_output=True, text=True).stdout.strip()
            url = re.sub(r"https://[^@/]+@", "https://", url)
            url = url[:-4] if url.endswith(".git") else url
            print(f"Ветка отправлена. Запрос на слияние: {url}/compare/main...{self.student}")
