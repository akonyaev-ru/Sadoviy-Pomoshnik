"""Сборка .exe — мимо Google Диска.

Проект лежит на синхронизируемом Диске, и PyInstaller от этого страдает дважды:
один раз собранный файл оказался повреждён («Failed to extract struct»), другой
раз сборка вовсе упала на правке ресурсов («remove_all_resources failed»).
Причина одна: Диск влезает в файл, пока тот пишется.

Поэтому собираем во временную папку на локальном диске и только готовый файл
кладём в `dist/`.

Запуск:  python tools/build.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Готовый .exe кладём НЕ в проект: папка проекта на Google Диске, и его
# синхронизация уже дважды портила сборку, а потом и вовсе закрыла доступ
# к `dist/` («Отказано в доступе»). Собранное живёт на локальном диске.
OUT_DIR = Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "SadovyPomoshnik" / "build"
NAME = "Садовый помощник"
DATA = ["src/panel.js", "src/razvedka.user.js"]
ICON = "assets/znachok.ico"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    work = Path(tempfile.mkdtemp(prefix="si-build-"))
    print(f"собираю во временной папке: {work}")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        # Без чёрного окна: владелец попросил убрать консоль. Весь вывод уходит
        # в файл (`app.setup_output`), про ошибки сообщает окно.
        "--noconsole",
        "--icon", str(ROOT / ICON),
        "--name", NAME,
        "--paths", "src",
        "--hidden-import", "websocket",
        "--exclude-module", "tkinter",
        "--exclude-module", "numpy",
        "--exclude-module", "PIL",
        "--distpath", str(work / "dist"),
        "--workpath", str(work / "build"),
        "--specpath", str(work),
    ]
    for d in DATA:
        cmd += ["--add-data", f"{ROOT / d};."]
    cmd.append(str(ROOT / "run.py"))

    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print("СБОРКА УПАЛА:")
        print("\n".join(result.stdout.splitlines()[-12:]))
        print("\n".join(result.stderr.splitlines()[-12:]))
        shutil.rmtree(work, ignore_errors=True)
        return 1

    built = work / "dist" / f"{NAME}.exe"
    if not built.is_file():
        print(f"СБОРКА УПАЛА: файла нет — {built}")
        shutil.rmtree(work, ignore_errors=True)
        return 1

    """Рабочая копия — удобство, а не обязанность.

    Если помощник сейчас запущен, Windows держит его файл, и `unlink` падает
    с «Отказано в доступе». Раньше на этом валилась ВСЯ сборка, хотя копия в
    `dist/` — та, которую отдают, — легла бы прекрасно. За один день это
    остановило работу трижды, причём дважды я после этого прогонял проверки
    против СТАРОГО файла и видел зелёное. Теперь занятая рабочая копия — это
    предупреждение, а не отказ.
    """
    out_dir = OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / built.name
    rabochaya_legla = True
    try:
        if target.exists():
            target.unlink()
        shutil.copy2(built, target)
    except OSError as exc:
        rabochaya_legla = False
        print(f"  рабочую копию обновить не вышло: {exc.__class__.__name__} — "
              "похоже, помощник сейчас запущен.")
        print("  Это не беда: копия в dist/ ниже — свежая. Но ПОМНИТЕ:")
        print("  `check_exe.py` без аргумента проверяет именно рабочую копию,")
        print("  то есть СТАРУЮ. Проверять надо так:")
        print(f'      python tools/check_exe.py "dist/{built.name}"')

    print(f"готово: {built.name}, {built.stat().st_size / 1_048_576:.1f} МБ")
    if rabochaya_legla:
        print(f"  рабочая копия: {target}")

    placed = place_in_project(built)
    shutil.rmtree(work, ignore_errors=True)

    if placed:
        print(f"  копия в проекте: {placed}")
    else:
        print("  в dist/ проекта положить не удалось — Диск держит файл. "
              "Рабочая копия выше цела.")

    print("теперь обязательно: python tools/check_exe.py")
    return 0


def place_in_project(built: Path) -> Path | None:
    """Кладёт готовый файл в `dist/` проекта, переживая Google Диск.

    Приём взят из `0. Проекты/Hunter CLI/MEMORY.md`: Диск держит **имя только что
    удалённого файла**, а не папку. Поэтому нельзя сперва удалить, потом
    записать — надо положить под ДРУГИМ именем и перезаписать переносом
    (`os.replace`, то же самое, что `move /y`): удаления нет, а значит нет и
    имени, за которое Диск держится.
    """
    dist = ROOT / "dist"
    final = dist / built.name
    staging = dist / "_svezhaya_sborka.exe"

    for attempt in range(1, 6):
        try:
            dist.mkdir(exist_ok=True)
            shutil.copy2(built, staging)
            os.replace(staging, final)          # перезапись БЕЗ предварительного удаления
            if final.stat().st_size == built.stat().st_size:
                return final
            print(f"  попытка {attempt}: размер не совпал, повторяю")
        except OSError as exc:
            print(f"  попытка {attempt}: {exc.__class__.__name__} — {exc}")
            time.sleep(1.5)
        finally:
            if staging.exists():
                try:
                    staging.unlink()
                except OSError:
                    pass
    return None


if __name__ == "__main__":
    raise SystemExit(main())
