"""Красный цикл для оболочки программы — без доступа к живой игре.

Проверяется цепочка целиком: запуск Chrome → отладочный порт → подключение →
вставка кода → выполнение на странице-подделке. Всё это агент гоняет у себя,
не отнимая заходы у игрока.

Главная проверка — `test_injection_survives_reload`: код должен вернуться сам
после перезагрузки страницы. Именно этого не умеет закладка, и именно ради
этого делается программа. Сломается — тест покраснеет.
"""

from __future__ import annotations

import functools
import os
import http.server
import shutil
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from si_helper import app, browser, cdp, payload  # noqa: E402

STAND = "tools/stand.html"
MARKER = "window.__proverkaVstavki = (window.__proverkaVstavki || 0) + 1;"


@pytest.fixture(scope="session")
def stand_server():
    """Отдаёт папку проекта по HTTP на свободном порту."""
    handler = functools.partial(QuietHandler, directory=str(ROOT))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{port}"
        httpd.shutdown()


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Тот же файловый сервер, но без записей в вывод теста."""

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def chrome(stand_server):
    """Запущенный Chrome без окна плюс подключение к его вкладке.

    Профиль — свой на каждый запуск. Общий не годится: пока предыдущий Chrome
    не закрылся, новый запуск с тем же профилем передаёт ему адрес и выходит,
    не открыв отладочный порт. Из-за этого 2026-09-09 набор рассыпался восемью
    ошибками, а в системе осталось восемь висящих окон.
    """
    profile = Path(tempfile.mkdtemp(prefix="si-shell-"))
    proc, port = browser.launch(f"{stand_server}/{STAND}", headless=True, profile=profile)
    try:
        target = cdp.find_page(port)
        with cdp.Cdp(target["webSocketDebuggerUrl"]) as conn:
            yield conn
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        browser.remove_profile(profile)


# ── сами проверки ────────────────────────────────────────────────────


def test_chrome_found():
    """Chrome находится там, где мы его ищем."""
    assert browser.find_chrome().is_file()


def test_debug_port_opens(chrome):
    """Отладочный порт живой: вкладка отвечает на выполнение выражения."""
    assert chrome.evaluate("1 + 1") == 2


def test_stand_loaded(chrome, stand_server):
    """Открылась именно страница-подделка, а не пустая вкладка."""
    chrome.navigate(f"{stand_server}/{STAND}")
    assert chrome.evaluate("document.title").startswith("Стенд")


def test_stand_has_204_cells(chrome, stand_server):
    """Подделка честно рисует сад: 204 клетки, как в настоящей игре."""
    chrome.navigate(f"{stand_server}/{STAND}")
    assert chrome.evaluate("document.querySelectorAll('#garden img').length") == 204


def test_injection_runs(chrome, stand_server):
    """Вставленный код выполняется на странице."""
    chrome.inject_on_every_load(MARKER)
    chrome.navigate(f"{stand_server}/{STAND}?x=1")
    assert chrome.evaluate("window.__proverkaVstavki") == 1


def test_injection_survives_reload(chrome, stand_server):
    """ГЛАВНОЕ. Код возвращается сам при каждой загрузке страницы.

    Закладка так не умеет: её надо нажимать заново. Если эта проверка
    покраснеет — вся причина делать программу, а не закладку, отпала.
    """
    chrome.inject_on_every_load(MARKER)

    for number in (1, 2, 3):
        chrome.navigate(f"{stand_server}/{STAND}?x={number}")
        assert chrome.evaluate("window.__proverkaVstavki") == 1, (
            f"загрузка №{number}: код не выполнился сам"
        )


def test_injection_can_be_removed(chrome, stand_server):
    """Вставку можно снять — иначе её не выключить без перезапуска браузера."""
    ident = chrome.inject_on_every_load(MARKER)
    chrome.navigate(f"{stand_server}/{STAND}?x=a")
    assert chrome.evaluate("window.__proverkaVstavki") == 1

    chrome.remove_injection(ident)
    chrome.navigate(f"{stand_server}/{STAND}?x=b")
    assert chrome.evaluate("typeof window.__proverkaVstavki") == "undefined"


def test_error_from_page_is_raised(chrome):
    """Ошибку со страницы не проглатываем: молчаливый сбой хуже громкого."""
    with pytest.raises(cdp.CdpError):
        chrome.evaluate("throw new Error('нарочно')")


# ── доставка настоящего разведчика программой ────────────────────────


def test_payload_strips_userscript_header():
    """Шапка ==UserScript== при вставке через CDP не нужна и убирается."""
    code = payload.load("razvedka.user.js")
    assert "==UserScript==" not in code
    assert "РАЗВЕДКА" in code


def test_program_delivers_working_recon(chrome, stand_server):
    """Сквозная проверка: программа доставляет разведчика, он работает.

    Это то, что уйдёт игроку первым: он запускает файл, заходит в игру и
    видит панель с отчётом. Если проверка красная — отправлять нечего.
    """
    chrome.inject_on_every_load(payload.load("razvedka.user.js"))
    chrome.navigate(f"{stand_server}/{STAND}?x=recon")

    report = _wait_for_report(chrome)

    assert "РАЗВЕДКА" in report
    assert "клеток #b1..#b204: 204 из 204" in report, "разведчик не увидел сад"
    assert "КОНТЕКСТ: фрейм garten" in report, "разведчик не дошёл до фрейма"
    assert chrome.evaluate("document.querySelectorAll('pre').length") == 1, (
        "панелей должно быть ровно одна"
    )


def _wait_for_report(conn, timeout: float = 10.0) -> str:
    """Ждёт появления панели: разведчик сначала дожидается фреймов."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if conn.evaluate("document.querySelectorAll('pre').length") >= 1:
            return conn.evaluate("document.querySelector('pre').textContent")
        time.sleep(0.25)
    raise AssertionError(f"панель разведчика не появилась за {timeout:.0f} с")


# ── вставка во ВСЕ вкладки, а не в одну ──────────────────────────────


def test_injection_reaches_a_newly_opened_tab(stand_server):
    """Помощник попадает и в НОВУЮ вкладку, открытую после запуска.

    Вход через портал upjers уходит на другой адрес и нередко открывает новую
    вкладку. Вставка живёт только в той вкладке, к которой подключились, —
    без обхода вкладок помощника там просто нет. Поймано 2026-09-09.
    """
    marker = "window.__vstavka = (window.__vstavka || 0) + 1;"
    profile = Path(tempfile.mkdtemp(prefix="si-shell-"))
    proc, port = browser.launch("about:blank", headless=True, profile=profile)
    tabs: dict = {}
    try:
        cdp.find_page(port)
        app._sync_tabs(port, tabs, marker)
        assert len(tabs) >= 1, "не подключились к первой вкладке"

        # Открываем вторую вкладку — как это сделал бы портал.
        first = cdp.find_page(port)
        with cdp.Cdp(first["webSocketDebuggerUrl"]) as conn:
            conn.call("Target.createTarget", {"url": f"{stand_server}/{STAND}?vtoraya=1"})

        deadline = time.monotonic() + 15
        second = None
        while time.monotonic() < deadline:
            app._sync_tabs(port, tabs, marker)
            for t in cdp.list_targets(port):
                if t.get("type") == "page" and "vtoraya=1" in (t.get("url") or ""):
                    second = t
                    break
            if second and len(tabs) >= 2:
                break
            time.sleep(0.4)

        assert second, "вторая вкладка не открылась"
        assert len(tabs) >= 2, f"обход вкладок её не подхватил: {len(tabs)}"

        with cdp.Cdp(second["webSocketDebuggerUrl"]) as conn:
            conn.navigate(f"{stand_server}/{STAND}?vtoraya=2")
            assert conn.evaluate("window.__vstavka") == 1,                 "во второй вкладке код не выполнился"
    finally:
        for c in tabs.values():
            c.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        browser.remove_profile(profile)


# ── подключение к уже работающему приложению ─────────────────────────
#
# Владелец запустил помощника, когда приложение upjers Home уже было открыто,
# и получил отказ: закройте, мол, сами через трей. Закрывать его
# принудительно нельзя — теряется вход в игру (проверено 2026-09-09).
# Но если приложение открывали МЫ, его отладочный порт уже работает, и
# помощнику нужен именно порт, а не собственный процесс: можно просто
# подключиться. Эти проверки закрывают тот путь.


def test_debug_port_of_finds_the_port_of_a_running_process(stand_server):
    """Порт работающего процесса находится по его номеру.

    Берём настоящий процесс с настоящим отладочным портом, а не подделку:
    ровно так выглядит уже открытое приложение upjers Home.
    """
    profile = Path(tempfile.mkdtemp(prefix="si-attach-"))
    proc, port = browser.launch(f"{stand_server}/{STAND}", headless=True, profile=profile)
    try:
        cdp.wait_for_port(port, timeout=60)
        found = browser.debug_port_of([proc.pid])
        assert found == port, f"нашли не тот порт: {found} вместо {port}"
        assert browser.debug_port_of([]) is None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        browser.remove_profile(profile)


def test_debug_port_of_ignores_a_process_without_a_port():
    """У чужого процесса без отладочного порта искать нечего.

    Номер берём заведомо не наш — собственный процесс проверок. Он живой,
    но браузерного порта у него нет.
    """
    import os
    assert browser.debug_port_of([os.getpid()]) is None


def test_attached_app_says_alive_until_the_port_dies(stand_server):
    """`poll()` молчит, пока приложение живо, и отвечает, когда оно ушло.

    Главному циклу от процесса нужно ровно это. Плюс `terminate()` не должен
    менять ответ `poll()`: у подключённого приложения он обязан быть пустым
    действием — приложение не наше, и остановить его мы не вправе.

    Чего эта проверка НЕ доказывает: что `terminate()` не убьёт приложение
    каким-нибудь посторонним способом. Убивать ему нечем — у `AttachedApp`
    нет ни номера процесса, ни его ручки, только номер порта.
    """
    profile = Path(tempfile.mkdtemp(prefix="si-attach2-"))
    proc, port = browser.launch(f"{stand_server}/{STAND}", headless=True, profile=profile)
    try:
        cdp.wait_for_port(port, timeout=60)
        attached = browser.AttachedApp(port)
        assert attached.poll() is None, "решил, что приложение уже ушло"

        attached.terminate()          # не должен трогать чужой запуск
        time.sleep(1.0)
        assert proc.poll() is None, "terminate() убил чужое приложение"
        assert attached.poll() is None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        browser.remove_profile(profile)

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if attached.poll() is not None:
            break
        time.sleep(0.5)
    assert attached.poll() is not None, "не заметил, что приложение закрылось"


def test_show_app_window_finds_nothing_when_there_is_nothing():
    """Пустой список и чужой процесс — ничего не показываем и не падаем."""
    import os
    assert browser.show_app_window([]) == ""
    assert browser.show_app_window([os.getpid()]) == ""
    # Процесс с окнами есть, а подписи такой нет — тоже пусто.
    assert browser.show_app_window([os.getpid()], title_part="такого-окна-нет") == ""


# Эти две проверки открывают НАСТОЯЩЕЕ окно браузера и выводят его вперёд:
# иначе не докажешь, что помощник окно из трея не достаёт. На рабочем столе
# владельца такое всплывать не должно — он это и заметил. Поэтому по
# умолчанию они пропускаются, а на машине сборки (где `CI=true`) идут всегда.
# Запустить у себя намеренно: `SI_OKNA=1 python -m pytest -k show_app_window`.
s_oknami = pytest.mark.skipif(
    not (os.environ.get("CI") or os.environ.get("SI_OKNA")),
    reason="открывает видимое окно; на машине сборки идёт всегда, "
           "у себя — с SI_OKNA=1",
)


@s_oknami
def test_show_app_window_raises_a_visible_window(stand_server):
    """Обычное окно выводится вперёд."""
    profile = Path(tempfile.mkdtemp(prefix="si-window-"))
    proc, port = browser.launch(f"{stand_server}/{STAND}", headless=False, profile=profile)
    try:
        cdp.wait_for_port(port, timeout=60)
        time.sleep(3.0)   # окну нужно время появиться и получить подпись
        if not _windows_of(proc.pid, "Стенд"):
            pytest.skip("окно браузера не нашлось по подписи — проверять нечего")
        assert browser.show_app_window([proc.pid], title_part="Стенд") == "vpered"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        browser.remove_profile(profile)


@s_oknami
def test_show_app_window_leaves_a_tray_window_alone(stand_server):
    """ГЛАВНОЕ: спрятанное окно НЕ достаём — только сообщаем.

    Приложение прячется в трей своей логикой, и окно при этом не свёрнуто,
    а скрыто. Вытащенное снаружи, оно появляется РАЗОБРАННЫМ: рамка во весь
    экран, отрисовка в старом размере, справа чёрное поле — и не чинится
    ничем, ни разворотом, ни явным размером. Владелец это увидел
    2026-09-10, и лечение оказалось одно: не лезть.
    """
    import ctypes

    profile = Path(tempfile.mkdtemp(prefix="si-tray-"))
    proc, port = browser.launch(f"{stand_server}/{STAND}", headless=False, profile=profile)
    try:
        cdp.wait_for_port(port, timeout=60)
        time.sleep(3.0)
        hwnds = _windows_of(proc.pid, "Стенд")
        if not hwnds:
            pytest.skip("окно браузера не нашлось по подписи — проверять нечего")

        u32 = ctypes.WinDLL("user32", use_last_error=True)
        SW_HIDE = 0
        for h in hwnds:
            u32.ShowWindow(h, SW_HIDE)     # как приложение прячется в трей
        time.sleep(0.5)
        assert not any(u32.IsWindowVisible(h) for h in hwnds), "окно не спряталось"

        assert browser.show_app_window([proc.pid], title_part="Стенд") == "v-tree"
        time.sleep(0.5)
        assert not any(u32.IsWindowVisible(h) for h in hwnds),             "окно вытащили из трея — именно этого делать нельзя"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        browser.remove_profile(profile)


def _windows_of(pid: int, title_part: str) -> list[int]:
    """Окна процесса, в подписи которых есть кусок текста."""
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.WinDLL("user32", use_last_error=True)
    out: list[int] = []
    cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _l):
        owner = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            n = u32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value and title_part.lower() in buf.value.lower():
                out.append(hwnd)
        return True

    u32.EnumWindows(cb(each), 0)
    return out


def test_game_host_is_recognised():
    """Узел игры отличаем от портала и от пустого адреса.

    От этого зависит распознавание «страница есть, а игры на ней нет»:
    после потери сессии сервер отдаёт по адресу игры ПУСТУЮ страницу, и
    принять её за игру легко — 2026-09-10 я и принял.
    """
    assert app._na_uzle_igry("https://s5.ru.molehillempire.com/main.php")
    assert app._na_uzle_igry("https://www.sadowajaimperija.ru/main.php")
    assert app._na_uzle_igry("https://s1.wurzelimperium.de/main.php")
    assert not app._na_uzle_igry("https://ru.upjers.com/my-games")
    assert not app._na_uzle_igry("about:blank")
    assert not app._na_uzle_igry("")


def test_only_one_helper_takes_the_lock():
    """Второй запуск помощника права не получает.

    Два помощника разом уводят приложение в игру каждый по-своему, и
    получаются два игровых окна — владелец это и увидел 2026-09-10.
    Имя замка здесь своё, чтобы не мешать настоящему запуску.
    """
    import uuid

    imya = "SadovyPomoshnik-proverka-" + uuid.uuid4().hex[:8]
    assert app.take_single_run(imya) is True, "первый запуск не смог занять замок"
    assert app.take_single_run(imya) is False, "второй запуск занял замок повторно"
    # Другое имя — другой замок, мешать не должен.
    assert app.take_single_run(imya + "-drugoe") is True


def test_version_flag_answers_and_does_not_touch_the_log(tmp_path, monkeypatch, capsys):
    """`--version` отвечает в поток вывода и НЕ трогает журнал.

    Файл раздаётся страницей релиза, и «та ли версия скачалась» надо уметь
    спросить у самого файла. Ответ обязан идти в командную строку: у
    программы нет консоли, и `setup_output()`, не найдя её, уводит весь
    вывод в журнал — ответ уходил бы туда же, затирая журнал работающего
    помощника. Поэтому проверка следит и за порядком.
    """
    from si_helper import __version__

    zhurnal = tmp_path / "помощник.log"
    monkeypatch.setattr(app, "log_path", lambda: zhurnal)

    code = app.main(["--version"])
    assert code == 0
    vyvod = capsys.readouterr().out
    assert __version__ in vyvod, f"версии нет в ответе: {vyvod!r}"
    assert "Садовый помощник" in vyvod
    assert not zhurnal.exists(), "журнал тронут, хотя просили только версию"


# ── аудит 2026-09-11: запуск без тихого Chrome ───────────────────────
#
# Первая живая проверка игроком: «сначала открылось одно окно, где ничего не
# было, а потом другое». Программа не нашла приложение по трём угаданным
# путям и МОЛЧА открыла отдельный Chrome — окно, в котором игрок войти в
# игру не умеет. Ниже — контракт нового поведения, без Chrome и без игры.


class _Stop(Exception):
    """Останавливает run() там, где дальше пошёл бы настоящий запуск."""


def _run_auto(monkeypatch, **kw):
    """run() в режиме auto с подменёнными краями: ни Chrome, ни окон."""
    okna = []
    monkeypatch.setattr(app, "show_error", lambda text: okna.append(("error", text)))
    monkeypatch.setattr(app, "show_wait", lambda text: okna.append(("wait", text)))
    monkeypatch.setattr(app, "show_note", lambda text: okna.append(("note", text)))
    monkeypatch.setattr(app, "_say", lambda text="": None)

    def no_browser(*a, **k):
        raise AssertionError("отдельный Chrome запускаться не должен")

    monkeypatch.setattr(app.browser, "launch", no_browser)
    return okna


def test_auto_without_the_app_stops_loudly(monkeypatch):
    """Приложения нет — окно с объяснением и выход, а не тихий Chrome."""
    okna = _run_auto(monkeypatch)
    monkeypatch.setattr(app.browser, "find_upjers",
                        lambda report=None: (report.append("обычные места установки: нет")
                                             if report is not None else None) or None)
    code = app.run("https://ru.upjers.com/my-games", "panel", target="auto")
    assert code == 3
    assert okna and okna[0][0] == "error", f"игроку ничего не сказали: {okna}"
    text = okna[0][1]
    assert "upjers Home" in text and "Где искал" in text, text
    assert "обычные места установки" in text, "в окне нет отчёта, где искали"


def test_explicit_app_path_wins_over_the_search(monkeypatch, tmp_path):
    """`--app путь` сильнее любого поиска — и запускается именно он."""
    okna = _run_auto(monkeypatch)
    exe = tmp_path / "upjers Home.exe"
    exe.write_bytes(b"x")
    zapusk = []

    def stop_here(path, port=None):
        zapusk.append(path)
        raise _Stop()

    monkeypatch.setattr(app.browser, "find_upjers",
                        lambda report=None: (_ for _ in ()).throw(AssertionError("поиск не нужен")))
    monkeypatch.setattr(app.browser, "upjers_running", lambda: [])
    monkeypatch.setattr(app.browser, "launch_app", stop_here)
    with pytest.raises(_Stop):
        app.run("https://ru.upjers.com/my-games", "panel", target="auto", app_override=exe)
    assert zapusk == [exe]
    assert not [o for o in okna if o[0] == "error"], okna


def test_running_app_without_a_port_is_quit_gracefully_and_relaunched(monkeypatch, tmp_path):
    """Игра открыта без порта: закрываем её штатно, её же командой, и открываем сами.

    Приложение прописывает себя в автозапуск с `--hidden` и у игрока висит в
    трее с загрузки — это не редкость, а каждый день. Никаких окон: команда
    `upjers://quit` делает то же, что «Выход» в трее, и вход сохраняется.
    """
    okna = _run_auto(monkeypatch)
    exe = tmp_path / "upjers Home.exe"
    exe.write_bytes(b"x")
    zakryto = []

    def stop_here(path, port=None):
        raise _Stop()

    monkeypatch.setattr(app.browser, "find_upjers", lambda report=None: exe)
    monkeypatch.setattr(app.browser, "upjers_running", lambda: [4242])
    monkeypatch.setattr(app.browser, "debug_port_of", lambda pids: None)
    monkeypatch.setattr(app.browser, "quit_upjers", lambda path, timeout=15.0: zakryto.append(path) or True)
    monkeypatch.setattr(app.browser, "launch_app", stop_here)
    with pytest.raises(_Stop):
        app.run("https://ru.upjers.com/my-games", "panel", target="auto")
    assert zakryto == [exe], "штатное закрытие не позвали"
    assert okna == [], f"игроку не должно быть показано ни одного окна: {okna}"


def test_running_app_without_a_port_waits_for_ok_when_graceful_quit_fails(monkeypatch, tmp_path):
    """Штатно закрыть не вышло — запасной путь: просим закрыть, ждём «ОК», открываем сами."""
    okna = _run_auto(monkeypatch)
    exe = tmp_path / "upjers Home.exe"
    exe.write_bytes(b"x")
    monkeypatch.setattr(app.browser, "quit_upjers", lambda path, timeout=15.0: False)
    vyzovy = {"running": 0}

    def running():
        vyzovy["running"] += 1
        # пока «ОК» не нажат — работает; после окна — закрыто
        return [] if any(o[0] == "wait" for o in okna) else [4242]

    def stop_here(path, port=None):
        raise _Stop()

    monkeypatch.setattr(app.browser, "find_upjers", lambda report=None: exe)
    monkeypatch.setattr(app.browser, "upjers_running", running)
    monkeypatch.setattr(app.browser, "debug_port_of", lambda pids: None)
    monkeypatch.setattr(app.browser, "launch_app", stop_here)
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    with pytest.raises(_Stop):
        app.run("https://ru.upjers.com/my-games", "panel", target="auto")
    assert [o[0] for o in okna] == ["wait"], f"ожидалось одно окно-ожидание: {okna}"
    assert "Выход" in okna[0][1] and "ОК" in okna[0][1], okna[0][1]


def test_running_app_that_stays_open_after_ok_is_an_error(monkeypatch, tmp_path):
    """Нажали «ОК», а игра всё ещё открыта — честная ошибка, не запуск."""
    okna = _run_auto(monkeypatch)
    exe = tmp_path / "upjers Home.exe"
    exe.write_bytes(b"x")
    monkeypatch.setattr(app.browser, "find_upjers", lambda report=None: exe)
    monkeypatch.setattr(app.browser, "upjers_running", lambda: [4242])
    monkeypatch.setattr(app.browser, "debug_port_of", lambda pids: None)
    monkeypatch.setattr(app.browser, "quit_upjers", lambda path, timeout=15.0: False)
    monkeypatch.setattr(app.browser, "launch_app",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("запуска быть не должно")))
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    code = app.run("https://ru.upjers.com/my-games", "panel", target="auto")
    assert code == 6
    assert [o[0] for o in okna] == ["wait", "error"], okna


def test_log_rotation_keeps_the_previous_run(tmp_path):
    """Прошлый журнал не затирается, а остаётся рядом как `.1.log`."""
    log = tmp_path / "помощник.log"
    log.write_text("первый запуск", encoding="utf-8")
    app.rotate_log(log)
    assert not log.exists()
    prev = tmp_path / "помощник.1.log"
    assert prev.read_text(encoding="utf-8") == "первый запуск"

    log.write_text("второй запуск", encoding="utf-8")
    app.rotate_log(log)
    assert prev.read_text(encoding="utf-8") == "второй запуск", "второй запуск не заменил первый"


def test_find_upjers_scans_any_program_subfolder(monkeypatch, tmp_path):
    """Имя подпапки у установщика меняется — ищем во всех подпапках."""
    programs = tmp_path / "Programs" / "upjers-playground7"
    programs.mkdir(parents=True)
    exe = programs / "upjers Home.exe"
    exe.write_bytes(b"x")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "net"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "net"))
    report = []
    assert browser.find_upjers(report) == exe
    assert any("подпапка программ" in r for r in report), report
    # найденное запоминается — в следующий раз дорогой поиск не нужен
    assert browser.note_path().read_text(encoding="utf-8") == str(exe)
    report2 = []
    assert browser.find_upjers(report2) == exe
    assert report2 and report2[0].startswith("заметка прошлого запуска: " + str(exe)), report2


def test_find_upjers_reports_every_place_when_nothing_is_found(monkeypatch, tmp_path):
    """Не нашли — отчёт называет каждое место, где искали."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "net"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "net"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("PUBLIC", str(tmp_path / "net"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "net"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "net"))
    monkeypatch.setattr(browser, "_powershell", lambda script, timeout=25.0: "")
    monkeypatch.setattr(browser, "_upjers_from_registry",
                        lambda report: report.append("реестр (установленные программы): нет"))
    report = []
    assert browser.find_upjers(report) is None
    text = chr(10).join(report)
    for mesto in ("заметка", "обычные места", "подпапки программ", "работающее приложение",
                  "реестр", "ярлыки"):
        assert mesto in text, f"в отчёте нет места «{mesto}»: {report}"


def test_console_tools_never_open_a_window(monkeypatch):
    """`tasklist`, `netstat`, `taskkill`, PowerShell — всегда без окна.

    У сборки нет консоли: без флага каждый такой вызов вспыхивал пустым
    чёрным окном на экране игрока — «окно, где ничего не было».
    """
    vyzovy = []

    class _Out:
        def __init__(self, stdout=""):
            self.stdout = stdout
            self.returncode = 0

    schyot = {"tasklist": 0}

    def fake_run(cmd, *a, **k):
        vyzovy.append((cmd[0], k.get("creationflags", 0)))
        if cmd[0] == "tasklist":
            # первые три раза «приложение работает» — чтобы дошло до taskkill
            schyot["tasklist"] += 1
            if schyot["tasklist"] <= 3:
                return _Out('"upjers Home.exe","4242","Console","1","100 K"')
        return _Out()

    monkeypatch.setattr(browser.subprocess, "run", fake_run)
    browser.upjers_running()
    browser.debug_port_of([4242])
    browser.close_upjers(timeout=0.01)
    browser._powershell("Get-Date")

    assert browser.BEZ_OKNA, "на Windows флаг CREATE_NO_WINDOW должен быть ненулевым"
    imena = {c for c, _ in vyzovy}
    for utilita in ("tasklist", "netstat", "taskkill", "powershell"):
        assert utilita in imena, f"вызов {utilita} не дошёл до subprocess: {vyzovy}"
    s_oknom = [c for c, f in vyzovy if not (f & browser.BEZ_OKNA)]
    assert not s_oknom, f"эти вызовы откроют окно: {s_oknom}"


# ── работа вслепую: игрок ничего не присылает, программа объясняет себя окном ──


def _okna(monkeypatch):
    okna = []
    monkeypatch.setattr(app, "show_error", lambda text: okna.append(("error", text)))
    monkeypatch.setattr(app, "show_wait", lambda text: okna.append(("wait", text)))
    monkeypatch.setattr(app, "show_note", lambda text: okna.append(("note", text)))
    monkeypatch.setattr(app, "_say", lambda text="": None)
    return okna


def test_second_launch_brings_the_game_forward_instead_of_an_error(monkeypatch):
    """Повторный щелчок по значку — не ошибка: показываем окно игры и выходим."""
    okna = _okna(monkeypatch)
    monkeypatch.setattr(app.browser, "upjers_running", lambda: [4242])
    monkeypatch.setattr(app.browser, "show_app_window", lambda pids, title_part="upjers": "vpered")
    assert app._vtoroy_zapusk() == 0
    assert okna == [], f"окно игры вывели вперёд — говорить больше нечего: {okna}"


def test_second_launch_explains_the_tray(monkeypatch):
    """Окно игры в трее — говорим словами, где его открыть, и ждём нажатия."""
    okna = _okna(monkeypatch)
    monkeypatch.setattr(app.browser, "upjers_running", lambda: [4242])
    monkeypatch.setattr(app.browser, "show_app_window", lambda pids, title_part="upjers": "v-tree")
    assert app._vtoroy_zapusk() == 0
    assert [o[0] for o in okna] == ["wait"], okna
    assert "у часов" in okna[0][1]


def test_second_launch_without_the_game_asks_for_a_reboot(monkeypatch):
    """Помощник завис, игры нет — единственный понятный совет: перезагрузка."""
    okna = _okna(monkeypatch)
    monkeypatch.setattr(app.browser, "upjers_running", lambda: [])
    assert app._vtoroy_zapusk() == 0
    assert [o[0] for o in okna] == ["wait"], okna
    assert "Перезагрузите" in okna[0][1]


def test_main_routes_a_second_launch_to_the_gentle_path(monkeypatch):
    """`main()` при занятом замке идёт в `_vtoroy_zapusk`, а не в ошибку."""
    okna = _okna(monkeypatch)
    monkeypatch.setattr(app, "setup_output", lambda: None)
    monkeypatch.setattr(app, "take_single_run", lambda name="": False)
    monkeypatch.setattr(app, "_vtoroy_zapusk", lambda: 42)
    assert app.main([]) == 42
    assert okna == []


def test_login_reminder_comes_once_after_a_minute_without_the_game(monkeypatch):
    """Игра не открылась за минуту — одна подсказка «войдите как обычно»."""
    okna = _okna(monkeypatch)
    t0 = 1000.0
    now = {"t": t0}
    monkeypatch.setattr(app.time, "monotonic", lambda: now["t"])
    states = {"a": "страница без игры: ru.upjers.com"}

    assert app._napomnit_vhod(states, t0, False) is False        # рано
    now["t"] = t0 + app.VHOD_ZHDAT_S + 1
    assert app._napomnit_vhod(states, t0, False) is True         # пора
    assert [o[0] for o in okna] == ["note"] and "Войдите" in okna[0][1]
    assert app._napomnit_vhod(states, t0, True) is True          # второй раз не напоминаем
    assert len(okna) == 1


def test_login_reminder_stays_silent_when_the_game_is_seen(monkeypatch):
    """Игра замечена — хоть спрятан, хоть ждёт — подсказка не нужна."""
    okna = _okna(monkeypatch)
    now = {"t": 5000.0}
    monkeypatch.setattr(app.time, "monotonic", lambda: now["t"])
    for state in ("в саду; гномов 4", "спрятан: город", "жду игру (объектов игры на странице ещё нет)"):
        states = {"a": "страница без игры: ru.upjers.com", "b": state}
        assert app._napomnit_vhod(states, 0.0, False) is False, state
    assert okna == []


def test_quit_upjers_sends_the_apps_own_command_without_a_window(monkeypatch, tmp_path):
    """`quit_upjers` шлёт `upjers://quit` вторым экземпляром — без окна — и ждёт выхода."""
    exe = tmp_path / "upjers Home.exe"
    exe.write_bytes(b"x")
    zapusk = []

    class _P:
        pass

    def fake_popen(cmd, *a, **k):
        zapusk.append((cmd, k.get("creationflags", 0)))
        return _P()

    schyot = {"n": 0}

    def running():
        schyot["n"] += 1
        return [4242] if schyot["n"] < 3 else []

    monkeypatch.setattr(browser.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(browser, "upjers_running", running)
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    assert browser.quit_upjers(exe, timeout=5.0) is True
    assert zapusk == [([str(exe), "upjers://quit"], browser.BEZ_OKNA)], zapusk


def test_quit_upjers_reports_failure_when_the_app_stays(monkeypatch, tmp_path):
    """Приложение не вышло за отведённое время — честное «не вышло», не «готово»."""
    exe = tmp_path / "upjers Home.exe"
    exe.write_bytes(b"x")
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *a, **k: object())
    monkeypatch.setattr(browser, "upjers_running", lambda: [4242])
    now = {"t": 0.0}

    def tick():
        now["t"] += 0.5
        return now["t"]

    monkeypatch.setattr(browser.time, "monotonic", tick)
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    assert browser.quit_upjers(exe, timeout=3.0) is False


# ── игра в отдельном окне приложения ─────────────────────────────────
#
# Живой прогон 2026-09-11 на аккаунте игрока: приложение открывает игру в
# ОТДЕЛЬНОМ окне, вкладка портала остаётся на «Моих играх». Признак «игра
# открыта» должен быть общим на все вкладки, иначе портал открывает игру
# снова и снова.


class _FakeConn:
    """Подключение, которое лишь записывает, что у него спрашивали."""

    def __init__(self, href="https://ru.upjers.com/my-games"):
        self.href = href
        self.calls = []

    def evaluate(self, expr):
        self.calls.append(("evaluate", expr[:40]))
        if expr == "location.href":
            return self.href
        return ""

    def call(self, method, params=None, timeout=None):
        self.calls.append((method, params))
        return {}


def test_game_open_in_another_window_stops_every_attempt(monkeypatch):
    monkeypatch.setattr(app, "_say", lambda text="": None)
    opened = {}
    states = {"portal": "страница без игры: ru.upjers.com", "igra": "в саду; гномов 4"}
    app._otmetit_igru(opened, states)
    assert opened.get("_igra") is True

    conn = _FakeConn()
    app._open_game_once(conn, opened, "portal", True)
    assert conn.calls == [], f"игра открыта — портал трогать нельзя: {conn.calls}"


def test_click_fallback_waits_while_the_game_loads_elsewhere(monkeypatch):
    """Нажали, портал остался, но игра грузится в другом окне — не переходим."""
    monkeypatch.setattr(app, "_say", lambda text="": None)
    now = {"t": 100.0}
    monkeypatch.setattr(app.time, "monotonic", lambda: now["t"])
    opened = {"portal": {"tries": 1, "done": False, "went": False,
                         "ssylka": "/play/42384029", "kogda": 80.0}}
    app._otmetit_igru(opened, {"igra": "жду игру (объектов игры на странице ещё нет)"})
    assert opened["_zhdu"] is True

    conn = _FakeConn()
    app._open_game_once(conn, opened, "portal", True)
    assert not [c for c in conn.calls if c[0] == "Page.navigate"], (
        f"перешли по ссылке, хотя игра уже грузится в другом окне: {conn.calls}")

    # игра не грузится нигде и прошло больше 6 с — запасной переход законен
    app._otmetit_igru(opened, {"igra": "страница без игры: ru.upjers.com"})
    conn2 = _FakeConn()
    app._open_game_once(conn2, opened, "portal", True)
    assert [c for c in conn2.calls if c[0] == "Page.navigate"], "запасной переход не сработал"


def test_game_found_in_a_tab_marks_it_open_for_everyone(monkeypatch):
    """Вкладка на узле игры с `gardenjs` — общий признак ставится сразу."""
    monkeypatch.setattr(app, "_say", lambda text="": None)

    class _GameConn(_FakeConn):
        def evaluate(self, expr):
            self.calls.append(("evaluate", expr[:40]))
            if expr == "location.href":
                return "https://s5.ru.molehillempire.com/main.php?page=garden"
            return True      # typeof gardenjs !== 'undefined'

    opened = {}
    app._open_game_once(_GameConn(), opened, "igra", True)
    assert opened.get("_igra") is True


def test_quit_upjers_answers_the_apps_close_question(monkeypatch, tmp_path):
    """Приложение спрашивает «Действительно закрыть» — отвечаем «Закрыть» один
    раз, и ждём выхода дальше. Так стало после обновления приложения 2026-09-18."""
    exe = tmp_path / "upjers Home.exe"
    exe.write_bytes(b"x")
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *a, **k: object())
    nazhali = []
    zhivo = {"n": 0}

    def running():
        zhivo["n"] += 1
        # живёт, пока не ответили на вопрос, и ещё два круга после
        return [4242] if not nazhali or zhivo["n"] < len(nazhali) + 4 else []

    monkeypatch.setattr(browser, "upjers_running", running)
    monkeypatch.setattr(browser, "confirm_quit_dialog", lambda pids: nazhali.append(list(pids)) or True)
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    assert browser.quit_upjers(exe, timeout=5.0) is True
    assert nazhali == [[4242]], f"на вопрос надо ответить ровно один раз: {nazhali}"


def _dialog_child(*podpisi: str):
    import subprocess
    import sys as _sys
    return subprocess.Popen(
        [_sys.executable, str(Path(__file__).resolve().parent / "dialog_child.py"), *podpisi],
        stdout=subprocess.PIPE, text=True, encoding="utf-8",
    )


def _press_dialog(proc, tries: int = 30) -> bool:
    for _ in range(tries):
        time.sleep(0.3)
        if browser.confirm_quit_dialog([proc.pid]):
            return True
    return False


@s_oknami
def test_confirm_quit_dialog_presses_close_in_a_real_dialog():
    """Настоящее окно с «Отменить»/«Закрыть» — нажимаем именно «Закрыть»."""
    proc = _dialog_child()
    try:
        assert _press_dialog(proc), "кнопку «Закрыть» в настоящем диалоге не нашли"
        out, _ = proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert out.split() == ["0", "102"], f"нажата не та кнопка: {out!r}"


@s_oknami
def test_confirm_quit_dialog_leaves_an_unknown_dialog_alone():
    """Окно с незнакомыми подписями — не трогаем: чужой вопрос не наш ответ."""
    proc = _dialog_child("Отменить", "Удалить всё")
    try:
        assert not _press_dialog(proc, tries=8), "нажал кнопку, подписи которой не знает"
        assert proc.poll() is None, "диалог закрылся сам — проверка ничего не доказала"
    finally:
        proc.kill()


# ── вопросы приложения и его самообновление (2026-11-01) ─────────────
#
# Две открытые задачи с живых прогонов. 2026-09-19: при нехватке памяти
# приложение спросило своим окном «всё равно стартовать игру?», игра стояла,
# а помощник через минуту советовал «войдите как обычно» — не про то.
# Самообновление: приложение вышло, поставило новую версию и поднялось само
# с `--updated` — без отладочного порта; помощник попрощался «Закрыто. До
# встречи.», хотя игра была на экране, и спасал только второй запуск.

na_windows = pytest.mark.skipif(sys.platform != "win32", reason="окна и замки Windows")

# Точных слов окна у нас нет — узнаём его по «памяти», а не по фразе целиком.
VOPROS_PAMYATI = chr(10).join(
    ["upjers Home", "Свободной памяти мало.", "Всё равно стартовать игру?", "Да", "Нет"])


def _vopros(monkeypatch, text, okna=(777,)):
    """Приложение с одним окном-вопросом; возвращает, какие окна читали."""
    monkeypatch.setattr(app.browser, "upjers_running", lambda: [4242])
    monkeypatch.setattr(app.browser, "app_dialogs",
                        lambda pids: list(okna) if pids == [4242] else [])
    prochli = []
    monkeypatch.setattr(app.browser, "dialog_text", lambda hwnd: prochli.append(hwnd) or text)
    return prochli


class _Proc:
    """Процесс приложения: живёт `zhit` кругов главного цикла, потом выходит."""

    def __init__(self, zhit: int):
        self.zhit = zhit

    def poll(self):
        if self.zhit > 0:
            self.zhit -= 1
            return None
        return 0

    def terminate(self):
        return None


def _chasy(monkeypatch, t0: float = 1000.0) -> dict:
    """Часы, которые идут только во сне: `sleep(s)` сдвигает `monotonic` на s."""
    now = {"t": t0}
    monkeypatch.setattr(app.time, "monotonic", lambda: now["t"])
    monkeypatch.setattr(app.time, "sleep", lambda s: now.__setitem__("t", now["t"] + s))
    return now


def test_memory_question_is_explained_in_words(monkeypatch):
    """Окно «всё равно стартовать игру?» — объясняем словами сразу и один раз."""
    okna = _okna(monkeypatch)
    prochli = _vopros(monkeypatch, VOPROS_PAMYATI)
    now = _chasy(monkeypatch)
    states = {"a": "страница без игры: ru.upjers.com"}
    voprosy: dict = {}

    assert app._voprosy_prilozheniya(voprosy, states) is True
    assert [o[0] for o in okna] == ["note"], okna
    assert "памяти" in okna[0][1] and "«Да»" in okna[0][1], okna[0][1]

    now["t"] += app.VOPROS_SHAG_S + 1
    assert app._voprosy_prilozheniya(voprosy, states) is False, "то же окно — второй раз не говорим"
    assert len(okna) == 1 and prochli == [777], (okna, prochli)


def test_unknown_app_question_gets_a_general_hint(monkeypatch):
    """Вопрос не про память — не выдумываем причину, просим ответить в окне."""
    okna = _okna(monkeypatch)
    _vopros(monkeypatch, chr(10).join(["upjers Home", "Продолжить?", "Да", "Нет"]))
    _chasy(monkeypatch)
    assert app._voprosy_prilozheniya({}, {"a": "страница без игры: ru.upjers.com"}) is True
    assert [o[0] for o in okna] == ["note"], okna
    assert "памяти" not in okna[0][1] and "Ответьте" in okna[0][1], okna[0][1]


def test_no_question_hunting_once_the_game_is_open(monkeypatch):
    """Игра открыта — окна приложения не ищем и игрока не тревожим."""
    okna = _okna(monkeypatch)
    monkeypatch.setattr(app.browser, "upjers_running",
                        lambda: (_ for _ in ()).throw(AssertionError("игра открыта — окна не ищем")))
    _chasy(monkeypatch)
    assert app._voprosy_prilozheniya({}, {"a": "в саду; гномов 4"}) is False
    assert okna == []


def test_question_watch_does_not_spawn_tasklist_every_few_seconds(monkeypatch):
    """Окна смотрим часто, а список процессов спрашиваем редко.

    Приложение может весь день висеть в трее без открытой игры, и `tasklist`
    раз в 3 с — это десятки тысяч процессов в сутки. Окно-вопрос живёт у
    главного процесса, номер которого за жизнь приложения не меняется.
    """
    _okna(monkeypatch)
    _vopros(monkeypatch, VOPROS_PAMYATI, okna=())
    spisok = []
    monkeypatch.setattr(app.browser, "upjers_running", lambda: spisok.append(1) or [4242])
    smotreli = []
    monkeypatch.setattr(app.browser, "app_dialogs", lambda pids: smotreli.append(list(pids)) or [])
    now = _chasy(monkeypatch)
    voprosy: dict = {}
    states = {"a": "страница без игры: ru.upjers.com"}
    for _ in range(60):
        app._voprosy_prilozheniya(voprosy, states)
        now["t"] += 1.0
    assert len(smotreli) >= 15, f"окна смотрели слишком редко: {len(smotreli)} раз за минуту"
    assert set(map(tuple, smotreli)) == {(4242,)}, smotreli
    assert len(spisok) <= 2, f"tasklist за минуту — {len(spisok)} раз"

def test_memory_question_replaces_the_login_reminder(monkeypatch, tmp_path):
    """Главный цикл: вопрос о памяти объяснён — «войдите как обычно» не нужно.

    Ровно это и было 2026-09-19: на экране вопрос приложения, а помощник
    через минуту советует войти в игру.
    """
    okna = _okna(monkeypatch)
    _vopros(monkeypatch, VOPROS_PAMYATI)
    _chasy(monkeypatch)
    monkeypatch.setattr(app, "_sync_tabs",
                        lambda port, tabs, source, printed, opened, straight, states:
                        states.__setitem__("a", "страница без игры: ru.upjers.com"))
    zhit = int(app.VHOD_ZHDAT_S) + 10
    assert app._vesti(_Proc(zhit), 9333, "", tmp_path / "upjers Home.exe", True) is False
    assert [o[0] for o in okna] == ["note"], okna
    assert "памяти" in okna[0][1], okna[0][1]


def test_dialog_text_is_read_through_ui_automation(monkeypatch):
    """Текст вопроса — через UI Automation: `GetWindowText` у DirectUI пуст."""
    skripty = []

    def fake_ps(script, timeout=25.0):
        skripty.append(script)
        return chr(0xFEFF) + "upjers Home\r\nВсё равно стартовать игру?\r\nДа\r\nДа\r\n\r\nНет\r\n"

    monkeypatch.setattr(browser, "_powershell", fake_ps)
    assert browser.dialog_text(123456) == chr(10).join(
        ["upjers Home", "Всё равно стартовать игру?", "Да", "Нет"])
    assert "UIAutomationClient" in skripty[0] and "123456" in skripty[0], skripty[0]
    assert "UTF8" in skripty[0], "без UTF-8 русский текст придёт знаками вопроса"


def test_command_lines_of_the_app_come_from_powershell(monkeypatch):
    """Командные строки приложения — построчно, без пустых и без окна."""
    skripty = []

    def fake_ps(script, timeout=25.0):
        skripty.append(script)
        return ('"C:\\Users\\Игрок\\upjers Home.exe" --updated\r\n\r\n'
                '  "C:\\Users\\Игрок\\upjers Home.exe" --type=gpu-process  \r\n')

    monkeypatch.setattr(browser, "_powershell", fake_ps)
    assert browser.upjers_command_lines() == [
        '"C:\\Users\\Игрок\\upjers Home.exe" --updated',
        '"C:\\Users\\Игрок\\upjers Home.exe" --type=gpu-process',
    ]
    assert browser.UPJERS_PROCESS in skripty[0] and "CommandLine" in skripty[0], skripty[0]
    assert "UTF8" in skripty[0], "путь с русским именем папки придёт знаками вопроса"


def test_return_after_an_update_is_recognised_by_its_flag(monkeypatch):
    """Поднялось само с `--updated` — это возврат после обновления.

    Командную строку спрашиваем только при новых номерах процессов: это
    PowerShell, а остатки закрытого приложения (`--type=…`) — не возврат.
    """
    _chasy(monkeypatch)
    spiski = iter([[4242, 4243], [4243], [], [], [5100], [5100, 5101]])
    seychas = {"pids": []}

    def running():
        seychas["pids"] = next(spiski, [5100, 5101])
        return seychas["pids"]

    sprosili = []

    def command_lines():
        sprosili.append(list(seychas["pids"]))
        if 5100 in seychas["pids"]:
            return ['"C:\\upjers Home.exe" --updated']
        return ['"C:\\upjers Home.exe" --type=renderer']

    monkeypatch.setattr(app.browser, "upjers_running", running)
    monkeypatch.setattr(app.browser, "upjers_command_lines", command_lines)
    assert app._vernulos_posle_obnovleniya() is True
    assert sprosili == [[4242, 4243], [5100]], sprosili


def test_app_closed_by_the_player_is_not_a_return(monkeypatch):
    """Остатки закрытого приложения и тишина — не возврат: ждём срок и выходим."""
    now = _chasy(monkeypatch)
    spiski = iter([[4242, 4243], [4243]])
    monkeypatch.setattr(app.browser, "upjers_running", lambda: next(spiski, []))
    monkeypatch.setattr(app.browser, "upjers_command_lines",
                        lambda: ['"C:\\upjers Home.exe" --type=gpu-process'])
    t0 = now["t"]
    assert app._vernulos_posle_obnovleniya() is False
    assert now["t"] - t0 >= app.VOZVRAT_ZHDAT_S


def _run_app(monkeypatch, tmp_path, procs):
    """run() с приложением: на каждый запуск выдаётся следующий процесс из `procs`."""
    okna = _run_auto(monkeypatch)
    exe = tmp_path / "upjers Home.exe"
    exe.write_bytes(b"x")
    zapuski = []

    def launch_app(path, port=None):
        zapuski.append(path)
        return procs[len(zapuski) - 1], 9333 + len(zapuski)

    monkeypatch.setattr(app.browser, "find_upjers", lambda report=None: exe)
    monkeypatch.setattr(app.browser, "launch_app", launch_app)
    monkeypatch.setattr(app.cdp, "wait_for_port", lambda port, timeout=60: [])
    monkeypatch.setattr(app, "_sync_tabs", lambda *a: None)
    monkeypatch.setattr(app, "_voprosy_prilozheniya", lambda voprosy, states: False)
    monkeypatch.setattr(app, "_napomnit_vhod", lambda states, nachalo, uzhe: True)
    monkeypatch.setattr(app, "release_single_run", lambda: True)
    monkeypatch.setattr(app, "take_single_run", lambda name="": True)
    _chasy(monkeypatch)
    return okna, zapuski, exe


def test_app_that_updated_itself_is_reopened_and_the_helper_stays(monkeypatch, tmp_path):
    """Обновилось и поднялось без порта — закрываем штатно, открываем с портом, ведём дальше.

    Замок одного запуска на время ожидания отпускаем: иначе игрок, щёлкнув
    по значку в эти секунды, услышал бы «помощник завис, перезагрузите
    компьютер».
    """
    okna, zapuski, exe = _run_app(monkeypatch, tmp_path, [_Proc(3), _Proc(2)])
    vozvraty = iter([True, False])
    monkeypatch.setattr(app, "_vernulos_posle_obnovleniya", lambda *a, **k: next(vozvraty))
    zhivo = iter([[], [5100]])          # при старте приложения нет; после обновления — есть
    monkeypatch.setattr(app.browser, "upjers_running", lambda: next(zhivo, []))
    monkeypatch.setattr(app.browser, "debug_port_of", lambda pids: None)
    zakryli = []
    monkeypatch.setattr(app.browser, "quit_upjers",
                        lambda path, timeout=15.0: zakryli.append(path) or True)
    zamok = []
    monkeypatch.setattr(app, "release_single_run", lambda: zamok.append("отпустил") or True)
    monkeypatch.setattr(app, "take_single_run", lambda name="": zamok.append("взял") or True)

    assert app.run("https://ru.upjers.com/my-games", "panel", target="auto") == 0
    assert zapuski == [exe, exe], "после обновления приложение не открыли заново"
    assert zakryli == [exe], "поднявшееся без порта надо закрыть штатно"
    assert zamok == ["отпустил", "взял", "отпустил"], zamok
    assert okna == [], okna


def test_closed_app_ends_the_helper_without_reopening(monkeypatch, tmp_path):
    """Игрок закрыл приложение — помощник уходит и ничего не открывает."""
    okna, zapuski, exe = _run_app(monkeypatch, tmp_path, [_Proc(2)])
    monkeypatch.setattr(app.browser, "upjers_running", lambda: [])
    monkeypatch.setattr(app, "_vernulos_posle_obnovleniya", lambda *a, **k: False)
    monkeypatch.setattr(app.browser, "quit_upjers",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("закрывать нечего")))
    assert app.run("https://ru.upjers.com/my-games", "panel", target="auto") == 0
    assert zapuski == [exe]
    assert okna == [], okna


def test_reopening_is_left_to_the_helper_that_took_the_lock(monkeypatch, tmp_path):
    """Пока ждали, игрок запустил помощника снова — открывает он, а этот уходит."""
    okna, zapuski, exe = _run_app(monkeypatch, tmp_path, [_Proc(2)])
    monkeypatch.setattr(app.browser, "upjers_running", lambda: [])
    monkeypatch.setattr(app, "_vernulos_posle_obnovleniya", lambda *a, **k: True)
    monkeypatch.setattr(app, "take_single_run", lambda name="": False)
    assert app.run("https://ru.upjers.com/my-games", "panel", target="auto") == 0
    assert zapuski == [exe], "второй помощник уже ведёт игру — открывать ещё раз нельзя"


def test_reopening_after_updates_has_a_limit(monkeypatch, tmp_path):
    """Приложение поднимается снова и снова — после нескольких раз сдаёмся, а не крутимся."""
    procs = [_Proc(1) for _ in range(app.PEREZAPUSKI_MAX + 5)]
    okna, zapuski, exe = _run_app(monkeypatch, tmp_path, procs)
    monkeypatch.setattr(app.browser, "upjers_running", lambda: [])
    monkeypatch.setattr(app, "_vernulos_posle_obnovleniya", lambda *a, **k: True)
    assert app.run("https://ru.upjers.com/my-games", "panel", target="auto") == 0
    assert len(zapuski) == 1 + app.PEREZAPUSKI_MAX, len(zapuski)


@na_windows
def test_single_run_lock_can_be_released_and_taken_again():
    """Отпущенный замок берётся снова; отпускать дважды нечего."""
    import uuid

    imya = "SadovyPomoshnik-proverka-" + uuid.uuid4().hex[:8]
    assert app.take_single_run(imya) is True
    assert app.release_single_run() is True
    assert app.take_single_run(imya) is True, "после отпуска замок не взялся снова"
    assert app.release_single_run() is True
    assert app.release_single_run() is False, "отпускать уже нечего"


@s_oknami
@na_windows
def test_app_question_is_found_and_read_in_a_real_dialog():
    """Настоящее окно-вопрос: находим его у процесса и читаем сам вопрос."""
    proc = _dialog_child("Да", "Нет", "Свободной памяти мало. Всё равно стартовать игру?")
    try:
        okna: list[int] = []
        for _ in range(30):
            time.sleep(0.3)
            okna = browser.app_dialogs([proc.pid])
            if okna:
                break
        assert okna, "окно-вопрос у процесса не нашлось"
        text = browser.dialog_text(okna[0])
        assert "стартовать игру" in text, f"вопрос не прочитался: {text!r}"
        assert "Нет" in text, f"подписей кнопок нет: {text!r}"
        assert browser.app_dialogs([os.getpid()]) == [], "чужие окна — не вопросы приложения"
    finally:
        proc.kill()
