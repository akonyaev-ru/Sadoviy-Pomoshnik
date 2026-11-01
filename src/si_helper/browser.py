"""Запуск Chrome с отладочным портом и собственным профилем.

Про профиль. Chrome не отдаёт отладочный порт профилю, который уже запущен:
если попросить порт для обычного профиля, Chrome молча передаст адрес
работающему окну и завершится, а порт так и не откроется. Поэтому программа
всегда поднимает браузер со своей папкой профиля. Обычный Chrome пользователя
при этом не трогается вовсе, а вход в игру запоминается внутри нашей папки.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import urllib.request
import time
from pathlib import Path

# Где искать Chrome. Порядок важен: сначала обычные места установки.
CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
)


# Приложение upjers Home — тоже Chromium, только внутри Electron. Проверено
# 2026-09-09 по его папке: `LICENSE.electron.txt`, `LICENSES.chromium.html`,
# `resources/app.asar`, `icudtl.dat`, `chrome_100_percent.pak`, `libEGL.dll`.
# Оно принимает `--remote-debugging-port`, отдаёт цели `page` (оболочка) и
# `webview` (в нём открывается сама игра) и принимает вставку кода.
UPJERS_CANDIDATES = (
    r"%LOCALAPPDATA%\Programs\upjers-playground2\upjers Home.exe",
    r"%LOCALAPPDATA%\Programs\upjers-home\upjers Home.exe",
    r"%PROGRAMFILES%\upjers Home\upjers Home.exe",
)

# Папки, внутри которых установщики Electron кладут приложение своей
# подпапкой. Имя подпапки у разных версий разное (`upjers-playground2`,
# `upjers-home`, …) — поэтому смотрим ВСЕ подпапки, а не угадываем имя.
UPJERS_FOLDERS = (
    r"%LOCALAPPDATA%\Programs",
    r"%PROGRAMFILES%",
    r"%PROGRAMFILES(X86)%",
    r"%LOCALAPPDATA%",
)

# Где лежат ярлыки, которыми игрок обычно и запускает приложение.
SHORTCUT_FOLDERS = (
    r"%USERPROFILE%\Desktop",
    r"%PUBLIC%\Desktop",
    r"%APPDATA%\Microsoft\Windows\Start Menu\Programs",
    r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs",
)

UPJERS_PROCESS = "upjers Home.exe"
UPJERS_EXE = "upjers Home.exe"

# Консольные утилиты Windows (`tasklist`, `netstat`, `taskkill`, PowerShell)
# запускаем БЕЗ ОКНА. У сборки нет консоли, и без этого флага каждый вызов
# на секунду вспыхивал пустым чёрным окном — `tasklist` идёт при каждом
# запуске. Первая живая проверка 2026-09-11: «сначала открылось окно, где
# ничего не было» — такое окно и есть.
BEZ_OKNA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Заметка «где лежит приложение». Пишется, как только оно найдено любым
# способом, и читается первой при следующем запуске: дорогие способы поиска
# (реестр, ярлыки) второй раз не нужны.
NOTE_NAME = "prilozhenie.txt"


def note_path() -> Path:
    return Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "SadovyPomoshnik" / NOTE_NAME


class BrowserNotFound(RuntimeError):
    """Chrome не нашёлся на этом компьютере."""


def find_chrome() -> Path:
    """Ищет chrome.exe по обычным местам установки, затем в PATH."""
    for raw in CHROME_CANDIDATES:
        p = Path(os.path.expandvars(raw))
        if p.is_file():
            return p
    which = shutil.which("chrome") or shutil.which("chrome.exe")
    if which:
        return Path(which)
    raise BrowserNotFound(
        "Не нашёл chrome.exe. Проверьте, что Google Chrome установлен, "
        "или укажите путь к нему параметром --chrome."
    )


def remove_profile(path, tries: int = 6) -> bool:
    """Убирает временную папку профиля браузера — по-настоящему.

    ЗАЧЕМ ОТДЕЛЬНАЯ ФУНКЦИЯ. Везде стояло
    `shutil.rmtree(profile, ignore_errors=True)`, и оно молча не срабатывало:
    Chrome отпускает файлы профиля не в тот же миг, что закрывается процесс.
    За два дня работы на машине владельца скопилась 61 брошенная папка на
    258 МБ — нашлось только когда он спросил, зачем на экране мелькают окна.
    `ignore_errors` тут и виноват: он превращает неудачу в тишину.

    Поэтому пробуем несколько раз с короткой паузой и ЛИШЬ в самом конце
    сдаёмся молча — к тому времени папка обычно уже удалена.
    """
    import shutil

    path = Path(path)
    for i in range(tries):
        if not path.exists():
            return True
        try:
            shutil.rmtree(path)
            return True
        except OSError:
            time.sleep(0.4 * (i + 1))
    return not path.exists()


def _is_upjers_exe(path) -> bool:
    try:
        p = Path(path)
        return p.name.lower() == UPJERS_EXE.lower() and p.is_file()
    except (OSError, ValueError):
        return False


def _upjers_from_note(report: list[str]) -> Path | None:
    """Путь, запомненный прошлым запуском."""
    np = note_path()
    try:
        raw = np.read_text(encoding="utf-8").strip()
    except OSError:
        report.append("заметка прошлого запуска: нет")
        return None
    if raw and _is_upjers_exe(raw):
        report.append(f"заметка прошлого запуска: {raw}")
        return Path(raw)
    report.append(f"заметка прошлого запуска: устарела ({raw or 'пусто'})")
    return None


def _upjers_from_candidates(report: list[str]) -> Path | None:
    for raw in UPJERS_CANDIDATES:
        p = Path(os.path.expandvars(raw))
        if p.is_file():
            report.append(f"обычное место: {p}")
            return p
    report.append("обычные места установки: нет")
    return None


def _upjers_from_folders(report: list[str]) -> Path | None:
    """Любая подпапка известных папок с программами."""
    for raw in UPJERS_FOLDERS:
        expanded = os.path.expandvars(raw)
        if "%" in expanded:          # переменной среды нет — папки тоже
            continue
        base = Path(expanded)
        if not base.is_dir():
            continue
        try:
            for sub in sorted(base.iterdir()):
                cand = sub / UPJERS_EXE
                if cand.is_file():
                    report.append(f"подпапка программ: {cand}")
                    return cand
        except OSError:
            continue
    report.append("подпапки программ: нет")
    return None


def _powershell(script: str, timeout: float = 25.0) -> str:
    """Короткий вызов PowerShell без профиля и без окна."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=BEZ_OKNA,
        )
    except Exception:
        return ""
    return r.stdout or ""


def _upjers_from_process(report: list[str]) -> Path | None:
    """Путь уже работающего приложения — у Windows он есть всегда."""
    out = _powershell(
        "Get-Process -Name 'upjers Home' -ErrorAction SilentlyContinue "
        "| Select-Object -First 1 -ExpandProperty Path"
    )
    for line in out.splitlines():
        line = line.strip()
        if line and _is_upjers_exe(line):
            report.append(f"работающее приложение: {line}")
            return Path(line)
    report.append("работающее приложение: нет")
    return None


def _upjers_from_registry(report: list[str]) -> Path | None:
    """Список установленных программ: DisplayIcon или InstallLocation."""
    try:
        import winreg
    except ImportError:
        report.append("реестр: недоступен")
        return None
    roots = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for root, path in roots:
        try:
            key = winreg.OpenKey(root, path)
        except OSError:
            continue
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(key, i)
                i += 1
            except OSError:
                break
            try:
                sk = winreg.OpenKey(key, sub)
            except OSError:
                continue
            vals = {}
            for name in ("DisplayName", "DisplayIcon", "InstallLocation"):
                try:
                    vals[name] = str(winreg.QueryValueEx(sk, name)[0])
                except OSError:
                    pass
            haystack = (sub + " " + " ".join(vals.values())).lower()
            if "upjers" not in haystack:
                continue
            icon = vals.get("DisplayIcon", "").split(",")[0].strip().strip('"')
            if icon and _is_upjers_exe(icon):
                report.append(f"реестр: {icon}")
                return Path(icon)
            loc = vals.get("InstallLocation", "").strip().strip('"')
            if loc:
                cand = Path(loc) / UPJERS_EXE
                if cand.is_file():
                    report.append(f"реестр: {cand}")
                    return cand
    report.append("реестр (установленные программы): нет")
    return None


def _upjers_from_shortcuts(report: list[str]) -> Path | None:
    """Ярлыки на рабочем столе и в меню «Пуск» — куда они ведут."""
    dirs = [os.path.expandvars(d) for d in SHORTCUT_FOLDERS]
    dirs = [d for d in dirs if "%" not in d and Path(d).is_dir()]
    if not dirs:
        report.append("ярлыки: папок нет")
        return None
    quoted = ",".join("'" + d.replace("'", "''") + "'" for d in dirs)
    out = _powershell(
        "$s = New-Object -ComObject WScript.Shell; "
        "Get-ChildItem -Path " + quoted + " -Recurse -Filter '*.lnk' -ErrorAction SilentlyContinue "
        "| ForEach-Object { try { $s.CreateShortcut($_.FullName).TargetPath } catch { } } "
        "| Where-Object { $_ -like '*upjers*' }"
    )
    for line in out.splitlines():
        line = line.strip()
        if line and _is_upjers_exe(line):
            report.append(f"ярлык: {line}")
            return Path(line)
    report.append("ярлыки (рабочий стол, меню «Пуск»): нет")
    return None


def remember_upjers(path: Path) -> None:
    """Записывает найденный путь, чтобы в следующий раз не искать."""
    try:
        np = note_path()
        np.parent.mkdir(parents=True, exist_ok=True)
        np.write_text(str(path), encoding="utf-8")
    except OSError:
        pass


def find_upjers(report: list[str] | None = None) -> Path | None:
    """Ищет приложение upjers Home. Возвращает None, если его нет.

    ПОЧЕМУ ИЩЕМ ШИРОКО. У игрока приложение может стоять не там, где у
    владельца: у установщиков Electron имя подпапки меняется от версии к
    версии, бывает установка «для всех» в Program Files. Три угаданных пути
    2026-09-11 не нашли приложение у игрока — и помощник молча открыл
    браузер, в котором игрок войти не умеет. Теперь порядок такой:
    заметка прошлого запуска → обычные места → любая подпапка папок с
    программами → работающий процесс → реестр → ярлыки. В `report`
    складывается, где искали: это уходит в журнал и в окно ошибки.
    """
    report = report if report is not None else []
    for finder in (
        _upjers_from_note,
        _upjers_from_candidates,
        _upjers_from_folders,
        _upjers_from_process,
        _upjers_from_registry,
        _upjers_from_shortcuts,
    ):
        found = finder(report)
        if found:
            remember_upjers(found)
            return found
    return None


def upjers_running() -> list[int]:
    """Номера уже запущенных процессов приложения.

    У приложения замок на один экземпляр: если оно уже открыто, новый запуск
    просто передаёт команду старому и завершается, НЕ открыв отладочный порт.
    Ровно на это я напоролся при первой проверке — порт молчал 45 секунд, а
    причина была в пяти висящих процессах.
    """
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {UPJERS_PROCESS}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="cp866", errors="replace", timeout=15,
            creationflags=BEZ_OKNA,
        ).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) > 1 and parts[1].strip().isdigit():
            pids.append(int(parts[1].strip()))
    return pids


def _port_answers(port: int, timeout: float = 1.5) -> bool:
    """Отвечает ли порт как отладочный порт браузера."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=timeout
        ) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return False
    return isinstance(data, dict) and (
        "webSocketDebuggerUrl" in data or "Browser" in data
    )


def debug_port_of(pids: list[int]) -> int | None:
    """Отладочный порт уже работающего приложения, если он открыт.

    Зачем это нужно. У приложения замок на один экземпляр: пока живёт хоть
    один его процесс, новый запуск не откроет свой порт. Раньше мы в этом
    случае просто просили закрыть приложение руками — а закрывать его
    принудительно нельзя, оно теряет вход в игру.

    Но если приложение запускали МЫ, порт у него уже открыт, и подключиться
    к нему можно без всякого перезапуска. Ровно так помощник и работает
    дальше — ему нужен порт, а не свой собственный процесс.

    Порт ищем по списку слушающих сокетов этих процессов и проверяем
    запросом: мало ли что ещё слушает приложение.
    """
    if not pids:
        return None
    nashi = set(pids)
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, encoding="cp866",
            errors="replace", timeout=20, creationflags=BEZ_OKNA,
        ).stdout
    except Exception:
        return None

    kandidaty: list[int] = []
    for line in out.splitlines():
        parts = line.split()
        # Строка вида: TCP  127.0.0.1:63951  0.0.0.0:0  LISTENING  7080
        # Слово состояния переведено на язык системы — на него не смотрим.
        if len(parts) < 4 or parts[0].upper() != "TCP":
            continue
        if not parts[-1].isdigit() or int(parts[-1]) not in nashi:
            continue
        adres = parts[1]
        if not adres.startswith("127.0.0.1:"):
            continue
        try:
            kandidaty.append(int(adres.rsplit(":", 1)[1]))
        except ValueError:
            continue

    for port in kandidaty:
        if _port_answers(port):
            return port
    return None


def show_app_window(pids: list[int], title_part: str = "upjers") -> str:
    """Выводит окно приложения вперёд — но НЕ достаёт его из трея.

    Возвращает:
      * `"vpered"`  — окно было на экране или свёрнуто, мы его показали;
      * `"v-tree"`  — окно спрятано в трей: НЕ ТРОГАЛИ, надо сказать человеку;
      * `""`        — окна не нашли.

    ⚠️ ПОЧЕМУ СПРЯТАННОЕ В ТРЕЙ ОКНО НЕ ТРОГАЕМ.
    Проверено на живой системе 2026-09-10 и стоило владельцу кривого экрана.
    Приложение прячется в трей своей собственной логикой, и окно при этом
    не свёрнуто, а СКРЫТО (`IsWindowVisible = False`). Если вытащить его
    снаружи через `SW_SHOW`, окно появляется, а отрисовка остаётся в старом
    размере: рамка 1920 px, а страница считает себя 892 px — справа чёрное
    поле. Причём чинится это НИКАК: ни разворот, ни явный `SetWindowPos`
    отрисовку не сдвигают, страница так и стоит на 892×610. Мы обходим
    приложение с чёрного хода, и оно остаётся разобранным.
    Правильный путь для человека — значок upjers у часов; наше дело сказать
    об этом словами, а не лезть в чужое окно.

    `title_part` — по какой подписи узнаём нужное окно. Вынесено в параметр
    не для гибкости, а чтобы это можно было ПРОВЕРИТЬ на постороннем окне
    с известной подписью.
    """
    if not pids or os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return ""

    nashi = set(pids)
    found: list[int] = []
    try:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _each(hwnd, _lparam):
            pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in nashi:
                return True
            n = u32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            # Окон у Electron много и почти все служебные; нужное — с
            # подписью приложения.
            if buf.value and title_part.lower() in buf.value.lower():
                found.append(hwnd)
            return True

        u32.EnumWindows(callback(_each), 0)
        if not found:
            return ""

        SW_RESTORE = 9
        pokazali = False
        for hwnd in found:
            if not u32.IsWindowVisible(hwnd):
                continue                      # в трее — не наше дело
            if u32.IsIconic(hwnd):
                u32.ShowWindow(hwnd, SW_RESTORE)   # свёрнутое развернуть можно
            u32.SetForegroundWindow(hwnd)
            pokazali = True
    except Exception:
        return ""
    return "vpered" if pokazali else "v-tree"


class AttachedApp:
    """Уже работающее приложение, которое мы НЕ запускали.

    Главному циклу от процесса нужно ровно две вещи: «ещё живо?» и
    «останови». Здесь `poll()` смотрит, отвечает ли отладочный порт, а
    `terminate()` не делает НИЧЕГО: чужой запуск не наш, чтобы его снимать,
    и вместе с ним ушёл бы вход в игру.
    """

    def __init__(self, port: int) -> None:
        self.port = port
        self._ushlo: int | None = None

    def poll(self) -> int | None:
        if self._ushlo is not None:
            return self._ushlo
        if _port_answers(self.port, timeout=2.0):
            return None
        self._ushlo = 0
        return 0

    def terminate(self) -> None:
        return None


def quit_upjers(app: Path, timeout: float = 15.0) -> bool:
    """Штатно закрывает работающее приложение — его же командой `upjers://quit`.

    ОТКУДА ЭТО. Приложение регистрирует протокол `upjers://` и слушает
    такие ссылки вторым экземпляром: `second-instance` → `DeepLinks` →
    команды `start`, `show`, `quit` и другие (разобрано по `app.asar`
    2026-09-11). `quit` делает ровно то, что «Выход» в меню трея:
    `requestAppQuit` с настоящим `app.quit()`. Это не чёрный ход, а
    штатный вход приложения.

    ЗАЧЕМ. Приложение прописывает себя в автозапуск с ключом `--hidden` и
    у игрока с загрузки Windows висит в трее — без отладочного порта, а
    порт нужен помощнику. `taskkill /F` закрывал его, но терял вход в игру
    (Electron не успевал сохранить сессию). Штатный выход вход сохраняет:
    проверено на живом приложении 2026-09-11 — после `upjers://quit` и
    запуска с портом портал открылся на «Моих играх» без окна пароля.
    Закрылось за 1,2 с.

    С ОБНОВЛЕНИЕМ ПРИЛОЖЕНИЯ 2026-09-18 ПОЯВИЛСЯ ВОПРОС. На `quit` оно
    теперь показывает своё окно «Действительно закрыть» с кнопками
    «Отменить» и «Закрыть» и ждёт ответа; порт при этом молчит, а помощник
    прежде честно ждал 15 с и сдавался. Отвечаем за человека — нажимаем
    «Закрыть» в его же окне (`BM_CLICK` кнопке диалога, без фокуса и
    мыши). Это не чёрный ход, а ровно то нажатие, которое сделал бы игрок.
    Подпись кнопки зависит от языка приложения — держим список; не нашли
    подписи — не нажимаем ничего и сообщаем, как раньше.
    """
    try:
        subprocess.Popen(
            [str(app), "upjers://quit"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=BEZ_OKNA,
        )
    except Exception:
        return False
    deadline = time.monotonic() + timeout
    otvetili = False
    while time.monotonic() < deadline:
        pids = upjers_running()
        if not pids:
            return True
        if not otvetili:
            otvetili = confirm_quit_dialog(pids)
        time.sleep(0.5)
    return not upjers_running()


# Подписи кнопки «закрыть» в окне подтверждения — по языкам игры.
ZAKRYT_SLOVA = ("закрыть", "выйти", "выход", "close", "quit", "exit",
                "schließen", "beenden", "zamknij", "bezárás", "zavřít",
                "ukončit", "kapat", "çık", "cerrar", "salir", "sluiten",
                "afsluiten", "închide", "ieșire")


def confirm_quit_dialog(pids: list[int]) -> bool:
    """Нажимает «Закрыть» в окне подтверждения выхода приложения, если оно есть.

    Окно — обычный диалог Windows (класс `#32770`) процесса приложения; у
    его кнопок нет своих номеров (`GetDlgCtrlID` = 0 у обеих, замерено
    2026-09-18), поэтому узнаём нужную по подписи. Вернёт True, если нажал.
    """
    if not pids or os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return False
    nashi = set(pids)
    try:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _class(hwnd) -> str:
            buf = ctypes.create_unicode_buffer(64)
            u32.GetClassNameW(hwnd, buf, 64)
            return buf.value

        def _text(hwnd) -> str:
            n = u32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            return buf.value

        dialogs: list[int] = []

        def _top(hwnd, _lparam):
            pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in nashi and _class(hwnd) == "#32770":
                dialogs.append(hwnd)
            return True

        u32.EnumWindows(callback(_top), 0)
        for dlg in dialogs:
            knopki: list[tuple[int, str]] = []

            def _child(hwnd, _lparam):
                if _class(hwnd) == "Button":
                    knopki.append((hwnd, _text(hwnd)))
                return True

            u32.EnumChildWindows(dlg, callback(_child), 0)
            for hwnd, podpis in knopki:
                slovo = podpis.replace("&", "").strip().lower()
                if slovo in ZAKRYT_SLOVA:
                    u32.SendMessageW(hwnd, 0x00F5, 0, 0)   # BM_CLICK
                    return True
    except Exception:
        return False
    return False


def app_dialogs(pids: list[int]) -> list[int]:
    """Видимые окна-вопросы приложения — диалоги Windows (класс `#32770`).

    Приложение спрашивает игрока своими окнами: «Действительно закрыть»,
    «всё равно стартовать игру?» при нехватке памяти (2026-09-19). Пока такое
    окно открыто, игра не грузится, и сказать об этом игроку можем только мы.
    """
    if not pids or os.name != "nt":
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return []
    nashi = set(pids)
    found: list[int] = []
    try:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _top(hwnd, _lparam):
            pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in nashi and u32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(64)
                u32.GetClassNameW(hwnd, buf, 64)
                if buf.value == "#32770":
                    found.append(hwnd)
            return True

        u32.EnumWindows(callback(_top), 0)
    except Exception:
        return []
    return found


def _stroki(out: str) -> list[str]:
    """Непустые строки вывода PowerShell, без повторов и без метки BOM."""
    vse: list[str] = []
    for line in out.replace("﻿", "").splitlines():
        line = line.strip()
        if line and line not in vse:
            vse.append(line)
    return vse


def dialog_text(hwnd: int) -> str:
    """Текст окна-вопроса строками: заголовок, сам вопрос, подписи кнопок.

    Вопрос в таких окнах рисует DirectUI, а не обычный элемент Windows, и
    `GetWindowText` его не видит (проверено на окне нехватки памяти
    2026-09-19). Читаем через UI Automation — так же, как экранный диктор.
    Не прочиталось — пустая строка: окно-вопрос от этого вопросом быть не
    перестаёт.
    """
    out = _powershell(
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        "Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes; "
        f"$e = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]{int(hwnd)}); "
        "$e.FindAll([Windows.Automation.TreeScope]::Subtree, "
        "[Windows.Automation.Condition]::TrueCondition) "
        "| ForEach-Object { $_.Current.Name }"
    )
    return "\n".join(_stroki(out))


def upjers_command_lines() -> list[str]:
    """Командные строки запущенных процессов приложения.

    Нужны ради одного признака — `--updated`: с ним приложение поднимает
    себя само после обновления (замечено 2026-09-18; так запускают
    приложение установщики electron-builder). `tasklist` командной строки
    не показывает, а по номерам процессов новый запуск от остатков старого
    не отличить.
    """
    out = _powershell(
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        "Get-CimInstance Win32_Process "
        f"| Where-Object {{ $_.Name -eq '{UPJERS_PROCESS}' }} "
        "| ForEach-Object { $_.CommandLine }"
    )
    return [line.strip() for line in out.replace("﻿", "").splitlines() if line.strip()]


def close_upjers(timeout: float = 12.0) -> int:
    """Закрывает приложение upjers Home и ждёт, пока оно действительно уйдёт.

    Зачем: у приложения замок на один экземпляр, и пока живёт хоть один его
    процесс, новый запуск не откроет отладочный порт — помощник не появится.
    А процессы остаются висеть и после закрытия окна: Electron держит главный,
    отрисовку, графику и служебные. Владелец видел «6 процессов» при закрытых
    окнах — это ровно тот случай.

    ⚠️ ВЫЗЫВАТЬ ТОЛЬКО ПО ЯВНОМУ УКАЗАНИЮ (`--force-close`).
    Проверено 2026-09-09: приложение прячется в трей и штатно не выходит —
    ни `taskkill` без `/F`, ни `WM_CLOSE` его не берут. Остаётся только убить,
    а убитый Electron НЕ успевает сохранить вход: после этого игра встречает
    окном пароля. Мягкая попытка ниже оставлена на случай, если приложение
    когда-нибудь научится выходить само, но полагаться на неё нельзя.
    """
    pids = upjers_running()
    if not pids:
        return 0
    closed = len(pids)

    for force in (False, True):
        pids = upjers_running()
        if not pids:
            break
        cmd = ["taskkill"] + (["/F"] if force else []) + ["/T"]
        for pid in pids:
            cmd += ["/PID", str(pid)]
        try:
            subprocess.run(cmd, capture_output=True, timeout=20, creationflags=BEZ_OKNA)
        except Exception:
            pass

        deadline = time.monotonic() + (timeout / 2)
        while time.monotonic() < deadline:
            if not upjers_running():
                return closed
            time.sleep(0.4)

    return closed


def launch_app(app: Path, port: int | None = None) -> tuple[subprocess.Popen, int]:
    """Запускает приложение upjers Home с отладочным портом.

    Ни адреса, ни своей папки профиля не передаём: у приложения свой вход и
    свои сохранённые данные. Игрок заходит в игру там же, где привык, —
    ничего заново вводить не надо. Этим приложение выгодно отличается от
    отдельного Chrome, из-за которого терялся вход.
    """
    port = port or free_port()
    proc = subprocess.Popen(
        [str(app), f"--remote-debugging-port={port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc, port


def free_port() -> int:
    """Свободный порт от системы: фиксированный номер рано или поздно занят."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def profile_dir() -> Path:
    """Своя папка профиля рядом с данными пользователя, а не в Program Files."""
    base = Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "SadovyPomoshnik"
    base.mkdir(parents=True, exist_ok=True)
    return base / "chrome-profile"


def launch(
    url: str,
    port: int | None = None,
    chrome: Path | None = None,
    profile: Path | None = None,
    headless: bool = False,
    host_rules: str | None = None,
) -> tuple[subprocess.Popen, int]:
    """Запускает Chrome и возвращает процесс и номер отладочного порта."""
    exe = chrome or find_chrome()
    port = port or free_port()
    prof = profile or profile_dir()
    prof.mkdir(parents=True, exist_ok=True)

    args = [
        str(exe),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={prof}",
        # Без этого Chrome при первом запуске показывает мастер настройки и
        # предложение войти в аккаунт — игроку это ни к чему.
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
    ]
    if host_rules:
        # Только для проверок: заставляет браузер считать, что локальный стенд
        # находится на узле игры. Так граница «работаем только на страницах
        # игры» проверяется по-настоящему, а не через лазейку в самом коде.
        args.append(f"--host-resolver-rules={host_rules}")
    if headless:
        # Режим для проверок: окно не открывается, всё остальное как обычно.
        args += ["--headless=new", "--disable-gpu"]
    args.append(url)

    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc, port
