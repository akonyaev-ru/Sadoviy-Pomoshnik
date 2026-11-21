"""Проверка панели помощника на правдоподобной подделке игры.

Стенд `tools/stand-game.html` повторяет поведение настоящего сада: та же
`berechneFelder`, та же очередь с порогом шесть, те же условия «клетку можно
полить». Он записывает в `window.__sent` всё, что ушло бы на сервер, поэтому
проверять можно не «кнопка нажалась», а «политы ровно те растения».

В саду стенда нарочно намешано: 11 обычных овощей под полив, 5 политых час
назад, 4 созревших, 2 не-овоща, 1 из списка исключений игры и одна тыква 2×2.
Правильный ответ — 12 действий (тыква поливается один раз, а не четырежды).

Тыква стоит ШЕСТОЙ нарочно: игра сбрасывает очередь на шести действиях, и
ровно там её собственный отсев перестаёт защищать от повтора. Без этой
расстановки проверка зеленела даже со сломанным отсевом.
"""

from __future__ import annotations

import functools
import http.server
import shutil
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

import contextlib

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from si_helper import browser, cdp, payload  # noqa: E402

STAND = "tools/stand-game.html"
# Стенд открывается под именем игрового сервера: браузеру подменяется
# разрешение имён. Так граница «работаем только на страницах игры» проверяется
# честно, а не лазейкой в самом коде.
GAME_HOST = "s5.ru.molehillempire.com"
HOST_RULES = f"MAP {GAME_HOST} 127.0.0.1"
EXPECTED_COUNT = 12
EXPECTED_FIELDS = {1, 2, 3, 4, 5, 30, 100, 101, 102, 103, 104, 105}


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture(scope="module")
def server():
    handler = functools.partial(QuietHandler, directory=str(ROOT))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        yield {
            "game": f"http://{GAME_HOST}:{port}",   # «как будто игра»
            "alien": f"http://127.0.0.1:{port}",    # чужой узел
        }
        httpd.shutdown()


@contextlib.contextmanager
def fresh_browser():
    """Chrome со СВОИМ профилем на каждый запуск.

    Общий профиль здесь не годится: если предыдущий Chrome ещё не закрылся,
    новый запуск с тем же профилем просто передаёт ему адрес и завершается,
    не открыв отладочный порт. Поодиночке проверки проходили, а пачкой падали
    по таймауту — поймано 2026-09-09.
    """
    profile = Path(tempfile.mkdtemp(prefix="si-test-"))
    proc, port = browser.launch("about:blank", headless=True,
                                host_rules=HOST_RULES, profile=profile)
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


@pytest.fixture
def game(server):
    """Стенд под именем игрового сервера, с вставленной панелью."""
    with fresh_browser() as conn:
        conn.inject_on_every_load(payload.load("panel.js"))
        conn.navigate(f"{server['game']}/{STAND}")
        _wait(conn, "!!document.getElementById('si-helper')", "панель не появилась")
        yield conn


def _wait(conn, expr: str, message: str, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = conn.evaluate(expr)
            if value:
                return value
        except cdp.CdpError:
            pass
        time.sleep(0.2)
    raise AssertionError(f"{message} (ждали {timeout:.0f} с, выражение: {expr})")


def _status(conn) -> str:
    return conn.evaluate("document.getElementById('si-helper-status').textContent")


def _press(conn, key: str) -> None:
    """Нажимает кнопку по постоянной метке: подписи меняются по ходу игры."""
    ok = conn.evaluate(
        "(function(){var b=document.querySelector('#si-helper [data-si-button="
        + repr(key).replace("'", '"') + "]');if(!b)return false;b.click();return true})()"
    )
    assert ok, f"не нашёл кнопку: {key}"


# ── проверки ─────────────────────────────────────────────────────────


def test_panel_appears(game):
    """Строка кнопок нарисовалась и она одна."""
    assert game.evaluate("document.querySelectorAll('#si-helper').length") == 1


@pytest.mark.parametrize(("label", "action_started"), [
    ("Обойти", "/Собираю|Поливаю|Обход/.test(document.getElementById('si-helper-status').textContent)"),
    ("Продать", "window.__city.length > 0"),
    ("Пасека:", "window.__pasekaZaprosy.some(function(q){return q.do === 'bees_init';})"),
], ids=["round", "sell", "bees"])
def test_fallback_buttons_accept_mouse_clicks(server, label, action_started):
    """Текстовые кнопки ловят клики мыши при ошибке загрузки картинок."""
    with fresh_browser() as conn:
        conn.inject_on_every_load(
            "window._GFX = location.origin + '/missing-game-images/';\n" + payload.load("panel.js")
        )
        conn.navigate(f"{server['game']}/{STAND}")
        selector = f"#si-helper button[title^='{label}']"
        _wait(conn, f'!!document.querySelector("{selector}")', f"нет текстовой кнопки {label}")
        point = conn.evaluate(f'''(function(){{
            var button = document.querySelector("{selector}");
            button.scrollIntoView({{block: 'center'}});
            var rect = button.getBoundingClientRect();
            var x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
            var hit = document.elementFromPoint(x, y);
            return {{x: x, y: y, hits: hit === button, target: hit && (hit.id || hit.tagName)}};
        }})()''')
        assert point["hits"], f"мышь попадает мимо кнопки {label}: {point}"
        # Вызов button.click() обошёл бы проверку, попадает ли мышь в кнопку.
        for event, buttons in [("mousePressed", 1), ("mouseReleased", 0)]:
            conn.call("Input.dispatchMouseEvent", {
                "x": point["x"], "y": point["y"], "type": event,
                "button": "left", "buttons": buttons, "clickCount": 1,
            })
        _wait(conn, action_started, f"клик по кнопке {label} не запустил действие ({_status(conn)})", timeout=4)


def test_stand_is_honest(game):
    """Сам стенд считает свой сад так же, как мы ожидаем."""
    assert game.evaluate("EXPECTED.length") == EXPECTED_COUNT
    assert game.evaluate("Object.keys(_grid).length") == 204


def test_nothing_happens_without_press(game):
    """ГЛАВНОЕ ПО БЕЗОПАСНОСТИ: сама по себе панель не делает ничего.

    Требование AC-7 контракта. Если проверка покраснеет — помощник стал ботом.
    """
    time.sleep(2.0)
    assert game.evaluate("window.__sent.length") == 0, "панель полила без нажатия"
    assert game.evaluate("window.__cacheCalls") == 0, "панель трогала сад без нажатия"


def test_waters_exactly_the_right_plants(game):
    """Политы ровно те растения, и тыква 2×2 — один раз."""
    _press(game, "water")
    _wait(game, "/Полито|нечего|политы|Не /.test(" + _status_expr() + ")", "полив не завершился")

    sent = game.evaluate(
        "window.__sent.reduce(function(a,e){return a.concat(e.felder)},[])"
    )
    assert sorted(sent) == sorted(EXPECTED_FIELDS), f"ушло не то: {sorted(sent)}"
    assert len(sent) == EXPECTED_COUNT
    assert _status(game) == f"Полито {EXPECTED_COUNT} растений"


def test_no_dangerous_side_effects(game):
    """ГЛАВНОЕ ПО ПОСЛЕДСТВИЯМ: полив не трогает ничего, кроме полива.

    У игровой `cache()` есть ветки, срабатывающие ДО проверки режима: снос
    постройки (платный, с диалогом), молчаливая прополка сорняка и
    подтверждение ускорения. Слепой обход всех 204 клеток запускает их.
    Именно это и случилось на живом аккаунте 2026-09-09: помощник открыл
    «В самом деле убрать? Цена: 2,50 сТ».
    """
    _press(game, "water")
    _wait(game, "/Полито|нечего|политы/.test(" + _status_expr() + ")", "полив не завершился")

    danger = game.evaluate("window.__danger")
    assert danger == [], f"помощник полез не в своё дело: {danger}"


def test_mode_is_restored(game):
    """Режим игры возвращается на место — иначе следующий клик игрока польёт."""
    before = game.evaluate("window.mode")
    _press(game, "water")
    _wait(game, "/Полито|нечего|политы/.test(" + _status_expr() + ")", "полив не завершился")
    assert game.evaluate("window.mode") == before


def test_second_run_finds_nothing(game):
    """Повторный полив ничего не делает: игра уже отметила клетки политыми.

    Проверяем, что мы не шлём серверу заведомо отказные действия по кругу.
    """
    _press(game, "water")
    _wait(game, "/Полито/.test(" + _status_expr() + ")", "первый полив не завершился")
    first = game.evaluate("window.__sent.length")

    game.evaluate(
        "for (var k in _grid) { if (_grid[k].pid) _grid[k].water = Zeit.Client; } true"
    )
    _press(game, "water")
    _wait(game, "/уже политы/.test(" + _status_expr() + ")", "второй полив не сказал «уже политы»")
    assert game.evaluate("window.__sent.length") == first


def test_shopping_list_blocks_and_explains(game):
    """Открытый список покупок игра не даёт обойти — объясняем это словами."""
    game.evaluate("document.getElementById('einkaufszettel').style.display='block'; true")
    _press(game, "water")
    _wait(game, "/список покупок/.test(" + _status_expr() + ")", "нет объяснения про список")
    assert game.evaluate("window.__sent.length") == 0


def _status_expr() -> str:
    return "document.getElementById('si-helper-status').textContent"


# ── посадка ──────────────────────────────────────────────────────────


def test_no_widgets_of_our_own(game):
    """Никаких своих плашек и списков — только картинки игры.

    Владелец потребовал вид «как у Ивана»: у него на экране не висело ничего
    своего. Эта проверка не даёт вернуть выпадающий список семян.
    """
    stray = game.evaluate(
        "(function(){var b=document.getElementById('si-helper');"
        "return {select:b.querySelectorAll('select').length,"
        " input:b.querySelectorAll('input,textarea').length,"
        " text:b.textContent.trim()}})()"
    )
    assert stray["select"] == 0, "вернулся выпадающий список"
    assert stray["input"] == 0, "появилось поле ввода"
    assert stray["text"] == "", f"в ряду помощников есть текст: {stray['text']!r}"


def test_planting_fills_empty_cells_until_seeds_run_out(game):
    """Сажаем морковь: 40 семян — 40 растений, потом честно говорим, что кончились."""
    game.evaluate("selected = 1; true")   # игрок выбрал морковь на полке игры
    _press(game, "plant")
    _wait(game, "/Посажено|нет|Не /.test(" + _status_expr() + ")", "посадка не завершилась", 40)

    fields = game.evaluate(
        "window.__planted.reduce(function(a,e){return a.concat(e.felder)},[])"
    )
    assert len(fields) == 40, f"посажено не 40: {len(fields)}"
    assert len(set(fields)) == 40, "есть повторы клеток"
    assert _status(game) == "Посажено 40, семена кончились."
    assert game.evaluate("window.__danger") == [], "посадка полезла не в своё дело"


def test_planting_never_touches_occupied_cells(game):
    """Ни одна занятая клетка не попала под посадку."""
    busy_before = game.evaluate(
        "(function(){var o=[];for(var i=1;i<=204;i++){if(_grid[i].pid)o.push(i)}return o})()"
    )
    game.evaluate("selected = 1; true")   # игрок выбрал морковь на полке игры
    _press(game, "plant")
    _wait(game, "/Посажено|нет|Не /.test(" + _status_expr() + ")", "посадка не завершилась", 40)

    fields = game.evaluate(
        "window.__planted.reduce(function(a,e){return a.concat(e.felder)},[])"
    )
    overlap = set(fields) & set(busy_before)
    assert not overlap, f"посадили в занятые клетки: {sorted(overlap)}"


# ── что выгодно ──────────────────────────────────────────────────────


# ── оформление по системе CupIvan ────────────────────────────────────


def test_no_permanent_box(game):
    """Своей панели-коробки быть не должно: только строка кнопок.

    Владелец отверг коробку в углу; система CupIvan — вживление в игру.
    """
    box = game.evaluate(
        "(function(){var b=document.getElementById('si-helper');"
        "var s=getComputedStyle(b);"
        "return {fon:s.backgroundColor, ramka:s.borderTopWidth,"
        " kn:b.querySelectorAll('[data-si-button]').length}})()"
    )
    # лейка, жук, обход, фонарь, рынок, почта (с 2026.7) и пасека (с 2026.9)
    assert box["kn"] == 7, f"кнопок должно быть 7: {box}"
    assert box["fon"] in ("rgba(0, 0, 0, 0)", "transparent"), f"у строки есть фон: {box}"
    assert box["ramka"] in ("0px", ""), f"у строки есть рамка: {box}"


def test_beetle_up_and_the_rest_on_the_path(game):
    """Жук — наверх, в строку гномов; остальные трое — вниз, на дорожку.

    Так распорядился владелец: у жука в картинке зелень, и на плитке она
    смотрится чужеродно, а гномам там просторно. Агент сперва утащил наверх
    всех четверых — неверно прочёл просьбу.
    """
    tops = game.evaluate(
        "(function(){var s=document.getElementById('wimpareaDiv').getBoundingClientRect();"
        "var o={};[].forEach.call(document.querySelectorAll('#si-helper [data-si-button]'),"
        "function(e){o[e.getAttribute('data-si-button')]="
        "Math.round(e.getBoundingClientRect().top-s.top)});return o})()"
    )
    assert tops["plant"] < 46, f"жук не в строке гномов: {tops}"
    for key in ("water", "paint"):
        assert tops[key] > 50, f"гном {key} остался наверху: {tops}"


def test_beetle_stands_next_to_the_barker(game):
    """Жук-сажальщик стоит сразу за зазывалой — так просил владелец."""
    near = game.evaluate(
        "(function(){"
        "var b=document.querySelector('#si-helper [data-si-button=plant]').getBoundingClientRect();"
        "var z=document.getElementById('barkerOpener').getBoundingClientRect();"
        "return {zazor: Math.round(b.left - z.right), poVerhu: Math.round(b.top - z.top)}})()"
    )
    assert 0 <= near["zazor"] < 20, f"жук не у зазывалы: {near}"
    assert abs(near["poVerhu"]) < 10, f"жук не в одной строке с зазывалой: {near}"


def test_helpers_never_cover_the_games_own_things(game):
    """Ни один гном не накрывает вещи игры в полосе.

    Проверяем КАЖДОГО по отдельности: сама накладка теперь лежит поверх всей
    полосы, и по ней судить нельзя.

    Одно исключение НАРОЧНОЕ: прозрачная накладка посадки лежит ровно поверх
    автомата игры (`#wimpareaAutoplant`) — чтобы жук на экране был один, а
    нажатие доставалось нам. Она ничего не закрывает: картинки у неё нет.
    """
    clash = game.evaluate(
        "(function(){"
        "var bar=document.getElementById('si-helper');"
        "var s=document.getElementById('wimpareaDiv');"
        "var sw=s.getBoundingClientRect().width;"
        "var bad=[];"
        "[].forEach.call(bar.querySelectorAll('[data-si-button]'),function(g){"
        "  var b=g.getBoundingClientRect();"
        "  [].forEach.call(s.querySelectorAll('*'),function(e){"
        "    if(e===bar||bar.contains(e))return;"
        "    if(g.getAttribute('data-si-nakladka')&&e.id==='wimpareaAutoplant')return;"
        "    var r=e.getBoundingClientRect();"
        "    if(!r.width||!r.height)return;"
        "    if(r.width>=sw*0.9)return;"
        "    var dx=Math.min(b.right,r.right)-Math.max(b.left,r.left);"
        "    var dy=Math.min(b.bottom,r.bottom)-Math.max(b.top,r.top);"
        "    if(dx>6&&dy>10)bad.push(g.getAttribute('data-si-button')+'/'"
        "      +(e.id||e.className||e.tagName)+' '+Math.round(dx)+'x'+Math.round(dy));"
        "  });"
        "});return bad})()"
    )
    assert clash == [], f"накрыли вещи игры: {clash}"


def test_overlay_does_not_swallow_clicks(game):
    """Накладка не перехватывает мышь: игра под ней остаётся кликабельной."""
    assert game.evaluate(
        "getComputedStyle(document.getElementById('si-helper')).pointerEvents") == "none"
    assert game.evaluate(
        "getComputedStyle(document.querySelector('#si-helper [data-si-button=water]'))"
        ".pointerEvents") == "auto"


def test_bar_never_covers_the_garden(game):
    """Ряд помощников не накрывает ни одной грядки."""
    overlap = game.evaluate(
        "(function(){var b=document.getElementById('si-helper').getBoundingClientRect();"
        "var bad=[];for(var i=1;i<=204;i++){var e=document.getElementById('gardenTile'+i);"
        "if(!e)continue;var r=e.getBoundingClientRect();"
        "if(!(r.right<b.left||r.left>b.right||r.bottom<b.top||r.top>b.bottom))bad.push(i)}"
        "return bad})()"
    )
    assert overlap == [], f"кнопки легли поверх клеток: {overlap[:12]}"


def test_message_appears_as_balloon_and_fades(game):
    """Сообщения всплывают шариком и гаснут, а не висят на экране."""
    _press(game, "water")
    _wait(game, "document.getElementById('si-helper-notify').style.display==='block'",
          "шарик не появился")
    # Шарик по ходу работы меняет текст («Идёт полив…» → «Поливаю 7»),
    # поэтому проверяем смысл, а не мгновение.
    shown = game.evaluate("document.getElementById('si-helper-notify').textContent")
    assert "полив" in shown.lower() or "поливаю" in shown.lower(), shown

    _wait(game, "/Полито/.test(" + _status_expr() + ")", "полив не завершился")
    time.sleep(3.6)
    assert game.evaluate("document.getElementById('si-helper-notify').style.display") == "none",         "шарик не погас сам"


def test_highlight_marks_cells_and_can_be_turned_off(game):
    """Подсветка красит клетки и гасится второй раз — по решению владельца."""
    assert game.evaluate(
        "[].filter.call(document.querySelectorAll('[id^=gardenTile]'),"
        "function(e){return e.style.boxShadow}).length") == 0, "подсветка горит без нажатия"

    _press(game, "paint")
    _wait(game, "[].filter.call(document.querySelectorAll('[id^=gardenTile]'),"
                "function(e){return e.style.boxShadow}).length>0", "подсветка не зажглась")

    def count(rgb: str) -> int:
        return game.evaluate(
            "[].filter.call(document.querySelectorAll('[id^=gardenTile]'),"
            "function(e){return e.style.boxShadow.indexOf('rgba(" + rgb + "')===0}).length"
        )

    assert count("42, 111, 212") > 0, "нет синего «полить»"
    assert count("0, 0, 0") > 0, "нет чёрного «пусто»"
    assert count("34, 177, 76") > 0, "нет зелёного «созрело»"

    _press(game, "paint")
    _wait(game, "[].filter.call(document.querySelectorAll('[id^=gardenTile]'),"
                "function(e){return e.style.boxShadow}).length===0", "подсветка не погасла")


# ── граница: чужие страницы не наше дело ─────────────────────────────


def test_silent_on_foreign_pages(server):
    """ГЛАВНОЕ ПО БЕЗОПАСНОСТИ: на не-игровой странице помощник не показывается.

    Программа доставляет код через отладочный протокол браузера, а он адреса
    НЕ фильтрует: `@match` в шапке действует только для Tampermonkey. Значит,
    без этой границы скрипт выполнялся бы на КАЖДОЙ открытой странице —
    на входе через upjers, в почте, где угодно. Не наше дело и небезопасно.
    """
    with fresh_browser() as conn:
        if True:
            conn.inject_on_every_load(payload.load("panel.js"))
            conn.navigate(f"{server['alien']}/{STAND}")   # тот же стенд, но чужой узел
            time.sleep(1.5)

            assert conn.evaluate("!!document.getElementById('si-helper')") is False,                 "помощник показался на чужой странице"
            assert conn.evaluate("!!document.getElementById('si-helper-notify')") is False,                 "шарик выскочил на чужой странице"
            assert conn.evaluate("window.__sent.length") == 0, "и ещё что-то сделал"


def test_silent_when_game_objects_absent(server):
    """Правильный узел, но игры на странице нет — тоже молчим.

    Так выглядит страница входа: адрес игровой, а `gardenjs` ещё нет.
    """
    with fresh_browser() as conn:
        if True:
            conn.inject_on_every_load(payload.load("panel.js"))
            conn.navigate(f"{server['game']}/tools/stand.html")   # стенд без gardenjs
            time.sleep(1.5)
            assert conn.evaluate("!!document.getElementById('si-helper')") is False,                 "помощник показался там, где игры нет"


def test_buttons_are_the_games_own_gnomes(server):
    """Кнопки — картинки самой игры, как у CupIvan, а не наши плашки.

    Владелец выбрал «полностью по Ивану», а агент сперва поставил текстовые
    плашки, решив за него. Эта проверка не даёт откатиться обратно.
    """
    # Без автомата игры помощник рисует своего жука — тут и проверяем набор.
    with stand(server, "?bez_avtomata=1") as conn:
        _wait(conn, "document.querySelectorAll('#si-helper [data-si-button]').length===7",
              "кнопки не появились")
        srcs = conn.evaluate(
            "(function(){var a=document.querySelectorAll('#si-helper [data-si-button]'),o=[];"
            "for(var i=0;i<a.length;i++)o.push(a[i].getAttribute('src')||'');return o})()"
        )
    assert len(srcs) == 7, f"кнопок-картинок должно быть 7: {srcs}"

    joined = " ".join(srcs)
    assert "kannenzwerg.gif" in joined, "нет гнома с лейкой — того самого, что был у Ивана"
    assert "anpflanzautomat.gif" in joined, "нет посадочного автомата"
    assert "forscherzwerg.gif" not in joined, (
        "вернулся гном-исследователь: таблицу выгоды сняли 2026-09-09 по решению владельца"
    )
    assert "questzwerg_klein.png" in joined, "нет гнома с фонарём"
    assert "marktplatz_neu.png" in joined, "нет рыночной площади — быстрой продажи"
    assert "boosterzwerg_klein.png" in joined, "нет гнома-ускорителя — обхода всех садов"
    # Гном почты — единственная НАША картинка: её нарисовал владелец
    # 2026-09-19, и она лежит внутри программы, а не грузится с сервера игры.
    pochta = [s for s in srcs if s.startswith("data:image/png;base64,")]
    assert len(pochta) == 1, f"гном почты должен быть одной встроенной картинкой: {srcs}"
    assert "Vogelposticon01.gif" not in joined, "табличка почты вернулась в ряд гномов"
    assert "pics/bees/beekeeper.png" in joined, "нет гнома-пчеловода — пасеки"
    # Жнец игры в стенде НАНЯТ, значит своего жнеца рисовать нельзя:
    # два жнеца в полосе — та же ошибка, что была с двумя жуками.
    assert "sensenzwerg.gif" not in joined, "свой жнец при нанятом жнеце игры — это второй жнец"
    assert "sonne.gif" not in joined, "вернулось солнце — оно выбивалось из ряда гномов"
    for s in srcs:
        if s in pochta:
            continue
        assert s.startswith("http"), f"путь к картинке игры должен быть полным: {s}"


def test_dimmed_automat_explains_itself(game):
    """Нажатие на притушенный автомат объясняет причину, а не пропадает."""
    game.evaluate("selected = null; true")
    _press(game, "plant")
    _wait(game, "/на полке/.test(" + _status_expr() + ")", "автомат промолчал")


def test_helpers_are_always_visible(game):
    """Ни один помощник не гасится до невидимости.

    Жук-сажальщик гасился до 45% прозрачности, когда семена не выбраны, и на
    траве попросту исчезал — владелец его не нашёл. Помощник должен быть виден
    всегда, а причину сказать шариком при нажатии.
    """
    game.evaluate("selected = null; true")
    time.sleep(1.4)   # даём переспросить состояние полки

    faded = game.evaluate(
        "[].filter.call(document.querySelectorAll('#si-helper [data-si-button]'),"
        "function(e){return parseFloat(getComputedStyle(e).opacity) < 0.85})"
        ".map(function(e){return e.getAttribute('data-si-button')})"
    )
    assert faded == [], f"эти помощники почти невидимы: {faded}"


def test_the_rest_stand_right_and_spaced(game):
    """Остальные стоят правее, вровень с гномами игры, и с просветами.

    Владелец попросил: не жаться к домику и не стоять вплотную, а стоять как
    гномы самой игры — правее и с воздухом.
    """
    info = game.evaluate(
        "(function(){var s=document.getElementById('wimpareaDiv').getBoundingClientRect();"
        "var g=document.querySelector('#wimpareaHelper .gnomes').getBoundingClientRect();"
        "var a=[].map.call(document.querySelectorAll('#si-helper [data-si-button]'),"
        "function(e){var r=e.getBoundingClientRect();"
        "return {k:e.getAttribute('data-si-button'),l:r.left-s.left,r:r.right-s.left,t:r.top-s.top}});"
        "a=a.filter(function(x){return x.k!=='plant'}).sort(function(x,y){return x.l-y.l});"
        "var gaps=[];for(var i=1;i<a.length;i++)gaps.push(Math.round(a[i].l-a[i-1].r));"
        "return {nachalo: Math.round(a[0].l), gnomyIgry: Math.round(g.left-s.left),"
        " prosvety: gaps, konec: Math.round(a[a.length-1].r), shirinaPolosy: Math.round(s.width)}})()"
    )
    assert info["nachalo"] >= info["gnomyIgry"] - 10,         f"трое всё ещё жмутся влево: {info}"
    assert info["konec"] <= info["shirinaPolosy"], f"вылезли за край полосы: {info}"
    for gap in info["prosvety"]:
        assert 14 <= gap <= 30, f"просветы между гномами не как у игры: {info}"


def test_two_injections_draw_one_set_of_gnomes(server):
    """Скрипт, вставленный дважды, рисует ОДИН набор гномов.

    Программа привязывается и к `page`, и к `webview`, а при перезагрузке
    цель заводится заново — вставка легко случается второй раз. Владелец
    прислал снимок: жук нарисован дважды, второй уехал влево на крышу
    домика. Проверка ловит именно это, а не «панель появилась».
    """
    with fresh_browser() as conn:
        conn.inject_on_every_load(payload.load("panel.js"))
        conn.inject_on_every_load(payload.load("panel.js"))
        conn.navigate(f"{server['game']}/{STAND}")
        _wait(conn, "!!document.getElementById('si-helper')", "панель не появилась")
        time.sleep(1.5)   # даём второй вставке дорисовать, если она это делает
        assert conn.evaluate("document.querySelectorAll('#si-helper').length") == 1
        # Столбик садов живёт отдельной накладкой — его тоже должно быть по одному.
        assert conn.evaluate("document.querySelectorAll('#si-helper-gardens').length") == 1
        for key in ("water", "plant", "paint"):
            n = conn.evaluate(
                "document.querySelectorAll('[data-si-button=" + repr(key).replace("'", '"') + "]').length"
            )
            assert n == 1, f"гном {key} нарисован {n} раз(а)"


def test_beetle_never_stands_on_the_house_roof(server):
    """На тесной полосе жук уходит ВНИЗ, а не на крышу домика.

    У кого куплено больше помощников, блок гномов игры шире и просвет за
    зазывалой закрыт. Прежний запасной путь ставил жука в левый верхний
    угол — поверх домика, — и владелец прочитал его как второго, лишнего.
    """
    with fresh_browser() as conn:
        conn.inject_on_every_load(payload.load("panel.js"))
        conn.navigate(f"{server['game']}/{STAND}?tesnaya_polosa=1")
        _wait(conn, "!!document.getElementById('si-helper')", "панель не появилась")
        time.sleep(1.2)
        r = conn.evaluate("""(function(){
            function box(e){var b=e.getBoundingClientRect();return {l:b.left,t:b.top,r:b.right,b:b.bottom};}
            var g=box(document.querySelector('[data-si-button="plant"]'));
            var h=box(document.getElementById('wimpareaGardenhouse'));
            var s=box(document.getElementById('wimpareaDiv'));
            var over=Math.max(0,Math.min(g.r,h.r)-Math.max(g.l,h.l))
                    *Math.max(0,Math.min(g.b,h.b)-Math.max(g.t,h.t));
            return {over:Math.round(over), left:Math.round(g.l-s.l), top:Math.round(g.t-s.t),
                    inside:(g.l>=s.l-1&&g.r<=s.r+1&&g.t>=s.t-1&&g.b<=s.b+1)};
        })()""")
        assert r["over"] == 0, f"жук налез на домик: {r}"
        assert r["inside"], f"жук вылез за полосу: {r}"


@pytest.fixture
def water_stand(server):
    """Стенд, открытый в ВОДНОМ саду: там свой объект игры и свои правила."""
    with fresh_browser() as conn:
        conn.inject_on_every_load(payload.load("panel.js"))
        conn.navigate(f"{server['game']}/{STAND}?wg=1")
        _wait(conn, "!!document.getElementById('si-helper')", "панель не появилась")
        _wait(conn, "window.watergarden && watergarden.isOpen === true", "водный сад не открылся")
        yield conn


@contextlib.contextmanager
def stand(server, query: str = ""):
    """Стенд с нужным набором условий, панель уже вставлена."""
    with fresh_browser() as conn:
        conn.inject_on_every_load(payload.load("panel.js"))
        conn.navigate(f"{server['game']}/{STAND}{query}")
        _wait(conn, "!!document.getElementById('si-helper')", "панель не появилась")
        yield conn


def _garden_icons(conn):
    return conn.evaluate(
        "[].map.call(document.querySelectorAll("
        "'#si-helper-gardens [data-si-garden]'),"
        "function(e){return e.getAttribute('src')})"
    )


def test_garden_row_shows_every_owned_garden(server):
    """На каждый КУПЛЕННЫЙ сад — своя иконка, как у Ивана.

    Ровно ради этого владелец и попросил вернуть пятый сад: у Ивана кнопка
    на него была. Иконки — картинки самой игры `pics/garten/gartenN.jpg`.
    Теплица в ряд попасть не должна: это не сад.
    """
    with stand(server) as conn:
        _wait(conn, "document.querySelectorAll('#si-helper-gardens [data-si-garden]').length>1",
              "ряд садов не появился")
        srcs = _garden_icons(conn)
        assert len(srcs) == 5, f"садов должно быть пять: {srcs}"
        for n in range(1, 6):
            assert any(f"garten{n}.jpg" in s for s in srcs), f"нет сада {n}: {srcs}"
        assert not any("greenhouse" in s for s in srcs), f"теплица попала в сады: {srcs}"
        for s in srcs:
            assert s.startswith("http"), f"путь к картинке игры должен быть полным: {s}"


def test_garden_click_switches_the_garden(server):
    """Нажатие на пятый сад переключает игру именно в пятый."""
    with stand(server) as conn:
        _wait(conn, "document.querySelectorAll('#si-helper-gardens [data-si-garden]').length>1",
              "ряд садов не появился")
        assert conn.evaluate("window.__gardenSwitches.length") == 0, "переключил без нажатия"
        ok = conn.evaluate(
            "(function(){var a=document.querySelectorAll('#si-helper-gardens img:not([data-si-city])');"
            r"for(var i=0;i<a.length;i++){if(/garten5\.jpg/.test(a[i].src)){a[i].click();return true}}"
            "return false})()"
        )
        assert ok, "не нашёл иконку пятого сада"
        _wait(conn, "window.__gardenSwitches.length>0", "сад не переключился")
        assert conn.evaluate("window.__gardenSwitches") == [5]


def test_garden_row_silent_when_the_game_has_its_own(server):
    """У игры свои кнопки садов есть — второго ряда не рисуем.

    Первой строкой это проверял и Иван: `if (…indexOf('garten_quick') != -1) return`.
    """
    with stand(server, "?sady_igry=1") as conn:
        time.sleep(1.5)
        assert _garden_icons(conn) == [], "нарисовали второй ряд поверх игрового"


def test_garden_row_asks_the_server_only_on_press(server):
    """Списка садов нет — сами на сервер не идём; спрашиваем по нажатию.

    Требование АС-7: без нажатия панель не делает ничего. Поэтому вместо
    ряда показывается одна иконка, и запрос уходит только от неё.
    """
    with stand(server, "?sady_neizvestny=1") as conn:
        time.sleep(2.0)
        assert conn.evaluate("window.__citymapAsks") == 0, "спросил сервер без нажатия"
        assert _garden_icons(conn) == [], "садов знать неоткуда — их иконок быть не должно"
        assert conn.evaluate("document.querySelectorAll('#si-helper-gardens [data-si-ask]').length") == 1, "должна остаться одна иконка «спросить»"
        conn.evaluate("document.querySelector('#si-helper-gardens [data-si-ask]').click()")
        _wait(conn, "document.querySelectorAll('#si-helper-gardens [data-si-garden]').length===5",
              "после нажатия ряд садов не появился")
        assert conn.evaluate("window.__citymapAsks") == 1


def test_garden_column_stands_in_the_right_frame(server):
    """Сады стоят СТОЛБИКОМ В ПРАВОЙ РАМКЕ, а не рядом внизу.

    Так было у CupIvan: `#asd` — узкая полоса у правого края поля (20×640 в
    её же `style.css`), а столбик начинался с `top:250px`, ниже картинки
    рамки. Игрок помнит сады «сбоку справа» — это оно.
    """
    with stand(server) as conn:
        # Окно как у настоящей игры. В низком окне столбик прижимается вверх,
        # чтобы не уехать за край, и высота Ивана недостижима физически —
        # проверять на таком окне значит проверять не то. Столбик подрос до
        # девяти иконок, и разница стала видна: 227 вместо 250.
        conn.call("Emulation.setDeviceMetricsOverride",
                  {"width": 1100, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        conn.evaluate("window.dispatchEvent(new Event('resize')); true")
        time.sleep(1.2)
        _wait(conn, "document.querySelectorAll('#si-helper-gardens [data-si-garden]').length>1",
              "столбик садов не появился")
        r = conn.evaluate("""(function(){
            function b(e){return e.getBoundingClientRect();}
            function over(x,y){return Math.max(0,Math.min(x.right,y.right)-Math.max(x.left,y.left))
                                     *Math.max(0,Math.min(x.bottom,y.bottom)-Math.max(x.top,y.top));}
            var c=b(document.getElementById('si-helper-gardens'));
            var a=b(document.getElementById('asd'));
            return {vRamke:(c.left>=a.left-2&&c.right<=a.right+2),
                    otstupSverhu:Math.round(c.top-a.top),
                    gryadki:Math.round(over(c,b(document.getElementById('garden')))),
                    nizhnyayaPolosa:Math.round(over(c,b(document.getElementById('wimpareaDiv'))))};
        })()""")
        assert r["vRamke"], f"столбик не в правой рамке: {r}"
        assert 240 <= r["otstupSverhu"] <= 262, f"столбик не на высоте Ивана: {r}"
        assert r["gryadki"] == 0, f"столбик закрыл грядки: {r}"
        assert r["nizhnyayaPolosa"] == 0, f"столбик остался в нижней полосе: {r}"


def test_garden_column_is_a_column(server):
    """Иконки идут друг под другом, а не в строку."""
    with stand(server) as conn:
        _wait(conn, "document.querySelectorAll('#si-helper-gardens img').length>2",
              "столбик садов не появился")
        r = conn.evaluate("""(function(){
            var a=[].map.call(document.querySelectorAll('#si-helper-gardens img'),
                function(e){var x=e.getBoundingClientRect();
                    return {l:Math.round(x.left),t:Math.round(x.top),b:Math.round(x.bottom)}});
            var vStrochku=0, levye={};
            for(var i=1;i<a.length;i++) if(a[i].t < a[i-1].b-1) vStrochku++;
            for(i=0;i<a.length;i++) levye[a[i].l]=1;
            return {shtuk:a.length, vStrochku:vStrochku, raznyhLevyh:Object.keys(levye).length};
        })()""")
        assert r["vStrochku"] == 0, f"иконки встали в строку: {r}"
        assert r["raznyhLevyh"] == 1, f"столбик неровный: {r}"
        # пять садов, почта, грибы, улитки и две кнопки города
        assert r["shtuk"] == 10, f"в столбике не то число иконок: {r}"


def test_city_buttons_go_to_the_city(server):
    """Две кнопки города — как у Ивана: рынок и карта.

    У него это `zeigeStadtMain(1)` и `(2)`; обе функции в нынешней игре живы,
    проверено по её исходнику. Сами по себе они не срабатывают — только по
    нажатию (требование АС-7).
    """
    with stand(server) as conn:
        _wait(conn, "document.querySelectorAll('#si-helper-gardens [data-si-city]').length===2",
              "кнопок города нет")
        time.sleep(1.0)
        assert conn.evaluate("window.__city") == [], "открыл город без нажатия"
        conn.evaluate("document.querySelector('#si-helper-gardens [data-si-city=\"1\"]').click()")
        _wait(conn, "window.__city.length>0", "рынок не открылся")
        conn.evaluate("document.querySelector('#si-helper-gardens [data-si-city=\"2\"]').click()")
        _wait(conn, "window.__city.length>1", "карта города не открылась")
        assert conn.evaluate("window.__city") == [1, 2]


# ── водный сад ───────────────────────────────────────────────────────
#
# Он живёт ОТДЕЛЬНЫМ объектом игры (`watergarden`) со своей сеткой и своей
# очередью. `gardenjs` при этом держит предыдущий обычный сад, которого игрок
# не видит, — поэтому стенд считает всякое обращение к нему в водном саду
# опасным и записывает в `window.__danger`.


def test_water_garden_never_touches_the_normal_one(water_stand):
    """В водном саду помощник не трогает обычный сад и не зовёт платное.

    Это главный дефект захода: панель работала через `gardenjs`, а он в
    водном саду заряжен ПРЕДЫДУЩИМ садом. Нажатие лейки или жука уходило в
    невидимый игроку сад. Заодно следим, чтобы не позвали `waterAll()` —
    серверное «полить всё одним запросом», почти наверняка платное.
    """
    conn = water_stand
    _press(conn, "water")
    _wait(conn, "/Полито|нет растений|уже полит/.test(" + _status_expr() + ")",
          "полив не завершился")
    _press(conn, "plant")
    _wait(conn, "/Посажено|Свободных|полке/.test(" + _status_expr() + ")",
          "посадка не завершилась")
    assert conn.evaluate("window.__danger") == [], "тронули обычный сад или платную возможность"
    assert conn.evaluate("window.__cacheCalls") == 0, "звали cache() обычного сада"


def test_water_garden_waters_exactly_the_right_plants(water_stand):
    """Политы ровно те растения, что можно полить, и каждое по разу.

    В стенде: кувшинка 20 (не поливали ни разу) и камыш 5 на берегу — можно;
    лотос 2×1 на клетках 22–23 — можно, но ОДНИМ действием по левой верхней;
    кувшинка 21 полита час назад, кувшинка 24 созрела, 25 — сорняк, 26 —
    украшение: их трогать нельзя вовсе.
    """
    conn = water_stand
    _press(conn, "water")
    _wait(conn, "/Полито/.test(" + _status_expr() + ")", "полив не прошёл")
    cells = conn.evaluate(
        "(function(){var out=[];window.__sent.forEach(function(s){"
        "(s.water||[]).forEach(function(n){out.push(n)})});return out.sort(function(a,b){return a-b})})()"
    )
    # 22 и 23 — две половины одного лотоса: игра сама разложила действие
    assert cells == [5, 20, 22, 23], f"полито не то: {cells}"
    assert "Полито 3" in _status(conn), _status(conn)


def test_water_garden_plants_only_into_free_water(water_stand):
    """Сажаем только в свободную воду и ровно столько, сколько есть семян.

    Берег, закрытые клетки и занятые — не наши. Кувшинок на полке
    четырнадцать — БОЛЬШЕ, чем порог очереди игры: на этом числе открывается
    окно, в котором её собственная защита слепа (очередь уже отправлена,
    а новый счёт с сервера ещё не пришёл). Свой запас мы поэтому считаем сами.
    """
    conn = water_stand
    _press(conn, "plant")
    _wait(conn, "/Посажено/.test(" + _status_expr() + ")", "посадка не прошла")
    r = conn.evaluate("""(function(){
        var pos=[];
        window.__sent.forEach(function(s){(s.plant||[]).forEach(function(p){pos.push(p.pos)})});
        var vidy={};
        pos.forEach(function(n){vidy[watergarden.gridDefinition[n]]=1});
        var zanyatye=pos.filter(function(n){return window.WG_FREE_WATER.indexOf(n)===-1});
        return {shtuk:pos.length, vidy:Object.keys(vidy), chuzhie:zanyatye};
    })()""")
    assert r["chuzhie"] == [], f"посадили не в свободную воду: {r}"
    assert r["vidy"] == ["water"], f"посадили не только в воду: {r}"
    assert r["shtuk"] == 14, f"посажено не по числу семян: {r}"


def test_water_garden_seeds_are_recognised(water_stand):
    """Водные семена принимаются, а овощ объясняет причину отказа.

    Это ровно то, на что жаловался игрок: в водном саду семена выбраны, а
    жук отвечал «сначала выберите семена». Проверка ждала категорию овоща
    ('v'), а полка водного сада выкладывает водные растения ('w').
    """
    conn = water_stand
    title = conn.evaluate(
        "document.querySelector('#si-helper [data-si-button=plant]').title")
    assert "Кувшинка" in title, f"водные семена не признаны: {title}"

    conn.evaluate("selected = 1; true")          # морковь — семена обычного сада
    _press(conn, "plant")
    _wait(conn, "/водны/i.test(" + _status_expr() + ")",
          "не объяснил, что это семена не того сада")




# ── попрошайки ───────────────────────────────────────────────────────
#
# Листок рисует сама игра (`wimparea.show`), мы дописываем в него выгодность.
# Главная тонкость: игра ДЕРЖИТ КЭШ листков и на сервер ходит один раз на
# попрошайку. Значит перехвата одного лишь ajax мало — и это проверяется.
#
# В стенде два попрошайки:
#   11 — просит 10 моркови (по 3) и 5 огурцов (по 2), это 40 «по цене игры»,
#        а предлагает 48 → +20 %. Всё нужное на полке есть.
#   12 — просит 20 тыкв (по 4), это 80, предлагает 60 → −25 %.
#        Тыква на полке одна, не хватает 19.


def _open_wimp(conn, wimp_id: int):
    """Открывает листок и ждёт, пока блок будет ПЕРЕРИСОВАН.

    Ждать одного лишь наличия блока мало: от прошлого попрошайки он ещё
    висит, а листок игра дорисовывает ответом сервера. Поэтому сперва
    убираем свой блок и ждём, когда он появится заново.
    """
    conn.evaluate(
        "(function(){var b=document.getElementById('si-wimp');"
        "if(b&&b.parentNode)b.parentNode.removeChild(b);})(); true"
    )
    conn.evaluate(f"wimparea.show({wimp_id}); true")
    _wait(conn, "!!document.getElementById('si-wimp')",
          f"блок выгодности не появился (попрошайка {wimp_id})")


def _wimp_text(conn) -> str:
    return conn.evaluate("document.getElementById('si-wimp').textContent")


def test_wimp_sheet_shows_the_percent(game):
    """Хорошее предложение показано процентом и разложено числами."""
    _open_wimp(game, 11)
    text = _wimp_text(game)
    assert "+20%" in text, f"процент посчитан не так: {text}"
    assert "48" in text and "40" in text, f"нет чисел, из которых получен ответ: {text}"
    colour = game.evaluate(
        "document.querySelector('#si-wimp [data-si-wimp=percent]').className")
    assert colour == "blau", f"выгодное предложение показано не цветом игры: {colour}"


def test_wimp_sheet_marks_a_bad_offer(game):
    """Невыгодное предложение — красным и с минусом."""
    _open_wimp(game, 12)
    text = _wimp_text(game)
    assert "-25%" in text or "−25%" in text, f"процент посчитан не так: {text}"
    colour = game.evaluate(
        "document.querySelector('#si-wimp [data-si-wimp=percent]').className")
    assert colour == "rot", f"невыгодное предложение показано не цветом игры: {colour}"


def test_wimp_sheet_says_what_is_missing(game):
    """Сколько товара не хватает — словами и числом.

    Игра красит строку красным, но НЕ говорит, сколько докупить. Кнопка «Да»
    при этом не нажимается, и человек остаётся гадать.
    """
    _open_wimp(game, 12)
    missing = game.evaluate(
        "(document.querySelector('#si-wimp [data-si-wimp=missing]')||{}).textContent||''")
    assert "Тыква" in missing and "19" in missing, f"нехватка названа неверно: {missing}"


def test_wimp_sheet_is_silent_when_nothing_is_missing(game):
    """Всё есть — строки про нехватку нет вовсе."""
    _open_wimp(game, 11)
    assert game.evaluate(
        "!document.querySelector('#si-wimp [data-si-wimp=missing]')"
    ), "написали про нехватку там, где всё есть"


def test_wimp_percent_counts_the_games_bonus(server):
    """Процент считается от той суммы, которую видит игрок, — с бонусом.

    Игра прибавляет к предложению бонусы и показывает уже увеличенное число
    (`D = ceil(sum * (1 + бонус))`). Считать от голой суммы значило бы
    показывать процент не от того, что написано в строке «Сумма».
    """
    with stand(server, "?bonus=1") as conn:
        _open_wimp(conn, 11)
        text = _wimp_text(conn)
        # 48 * 1.2 = 57.6 → игра округляет ВВЕРХ до 58; 58/40 - 1 = +45 %.
        # Без округления вверх вышло бы +44 % — то есть проверка ловит и его.
        assert "+45%" in text, f"бонус игры не учтён: {text}"


def test_wimp_block_follows_the_shown_sheet_not_the_last_loaded(game):
    """Вернулись к первому попрошайке — показан ЕГО расчёт, а не последнего.

    Здесь и ломается перехват одного лишь ajax, как было у Ивана: на сервер
    игра ходит по разу на попрошайку, а дальше рисует из своего кэша
    (`q[i].sheet`). Перехватив только ответы, мы знали бы лишь того, чей
    листок пришёл ПОСЛЕДНИМ, и на возврате стрелкой показали бы чужие числа.
    Поэтому слушаем ещё и `wimparea.show(id)`.
    """
    _open_wimp(game, 11)
    assert "+20%" in _wimp_text(game)

    _open_wimp(game, 12)
    assert "-25%" in _wimp_text(game) or "−25%" in _wimp_text(game)

    # Возврат к первому: игра берёт его из кэша, на сервер не идёт.
    game.evaluate("document.getElementById('wimpVerkaufProducts').innerHTML=''; true")
    _open_wimp(game, 11)
    assert game.evaluate("window.__wimpAsks") == 2, "сходили на сервер лишний раз"
    text = _wimp_text(game)
    assert "+20%" in text, f"на возврате показан чужой расчёт: {text}"


def test_wimp_block_never_asks_the_server_itself(game):
    """Сам блок на сервер не ходит: только то, что игра и так запросила.

    Требование АС-7. Иван на этом месте держал скрытый iframe и обходил рынок
    по всем овощам сам — мы так не делаем.
    """
    _open_wimp(game, 11)
    _open_wimp(game, 12)
    assert game.evaluate("window.__wimpAsks") == 2, "лишние обращения за листками"
    assert game.evaluate("window.__sent.length") == 0, "блок что-то отправил на сервер"
    assert game.evaluate("window.__citymapAsks") == 0, "блок полез в карту города"


# ── рынок: помощь без своих плашек ───────────────────────────────────
#
# Своего окна со списком товаров у помощника больше нет: владелец отверг его
# 2026-09-10 — «уберем таблицы эти и панельки, уверен у Ивана это было
# как-то иначе реализовано». Иван и правда своих окон не рисовал.
#
# Теперь помощник ЗАПОЛНЯЕТ форму самой игры: игрок нажимает товар в палатке,
# помощник подставляет весь запас и цену. Нажимает игрок сам — и «предложить
# на рынке», и «O.K.» на комиссии: это его деньги, комиссию он должен видеть.
#
# Стенд повторяет палатку целиком, включая то, что каждый шаг перезагружает
# страницу и что пустое поле количества игра понимает как «весь запас».

STALL = ("document.getElementById('stadtframe').contentWindow.document"
         ".getElementById('shopframe').contentWindow")


def _stall_expr(inner: str) -> str:
    return "(function(){try{var w=%s;var d=w.document;return %s;}catch(e){return null}})()" % (
        STALL, inner)


def _open_stall(conn) -> None:
    """Открывает палатку и ждёт, пока помощник к ней ПОДВЕСИТСЯ.

    Ждать одного лишь появления формы мало: подвешивание происходит сразу
    после открытия, но это отдельный шаг, и нажатие по товару раньше него
    просто ничего не даёт. Признак готовности — метка на документе палатки.
    """
    _press(conn, "sell")
    _wait(conn, _stall_expr("!!d.getElementById('produkt_anzahl')"),
          "палатка не открылась")
    _wait(conn, _stall_expr("!!d.__siPalatka"), "помощник не подвесился к палатке")


def _click_tile(conn, pid: int) -> None:
    ok = conn.evaluate(_stall_expr(
        "(function(){var e=d.getElementById('p%d');if(!e)return false;e.click();return true})()" % pid))
    assert ok, f"не нашёл товар {pid} в палатке"


def _fields(conn):
    return conn.evaluate(_stall_expr(
        "({anzahl:(d.getElementById('produkt_anzahl')||{}).value,"
        " p1:(d.getElementById('produkt_preis1')||{}).value,"
        " p2:(d.getElementById('produkt_preis2')||{}).value})"))


def _wait_filled(conn, pid: int, skolko: str) -> None:
    _wait(conn, _stall_expr(
        "(d.getElementById('produkt_anzahl')||{}).value === '%s'" % skolko),
        f"помощник не подставил количество по товару {pid}")


def test_market_gnome_opens_the_stall(game):
    """Гном рыночной площади открывает палатку — через функции самой игры."""
    assert game.evaluate("window.__city.length") == 0, "полез в город без нажатия"
    _open_stall(game)
    assert game.evaluate("window.__city") == [1], "город открыт не функцией игры"


def test_market_asks_the_server_only_on_a_product_click(game):
    """Цену рынка спрашиваем только когда нажали КОНКРЕТНЫЙ товар.

    Требование АС-7. У Ивана рынок обходил скрытый кадр по всем овощам —
    это работа без игрока, и мы так не делаем.
    """
    _open_stall(game)
    time.sleep(1.5)
    assert game.evaluate("window.__zaprosyRynka") == [], "спросил рынок без нажатия"
    _click_tile(game, 6)
    _wait(game, "window.__zaprosyRynka.length>0", "не спросил цену после нажатия")
    assert game.evaluate("window.__zaprosyRynka") == [6], "спросил лишнее"


def test_market_fills_the_games_own_form(game):
    """Морковь: подставлен весь запас и цена на грош дешевле самой дешёвой.

    Своих окон при этом не появляется — заполняется форма игры.
    """
    _open_stall(game)
    _click_tile(game, 6)
    _wait_filled(game, 6, "163")
    assert _fields(game) == {"anzahl": "163", "p1": "0", "p2": "07"}, _fields(game)
    assert game.evaluate("document.querySelectorAll('#si-helper-market').length") == 0, \
        "вернулась своя плашка со списком товаров"


def test_market_never_prices_below_the_games_floor(game):
    """Салат: самое дешёвое 0,02 уже равно нижней границе — упираемся в неё."""
    _open_stall(game)
    _click_tile(game, 2)
    _wait_filled(game, 2, "40")
    f = _fields(game)
    assert (f["p1"], f["p2"]) == ("0", "02"), f"ушли ниже пола игры: {f}"


def test_market_without_offers_asks_the_ceiling(game):
    """Огурец: предложений нет — подрезать некого, просим по верхней границе."""
    _open_stall(game)
    _click_tile(game, 12)
    _wait_filled(game, 12, "7")
    f = _fields(game)
    assert (f["p1"], f["p2"]) == ("0", "42"), f"верхняя граница 0,42, а подставили: {f}"


def test_market_leaves_every_press_to_the_player(game):
    """ГЛАВНОЕ: помощник только заполняет. Отправляет игрок.

    Комиссию рынка (10 %) игра показывает отдельным шагом, и это настоящие
    деньги. Нажимать за игрока нельзя: на молчаливой трате проект уже
    обжигался платным диалогом сноса.
    """
    _open_stall(game)
    _click_tile(game, 6)
    _wait_filled(game, 6, "163")
    time.sleep(1.5)
    assert game.evaluate("window.__podgotovleno || []") == [], "отправил подготовку сам"
    assert game.evaluate("window.__predlozheno || []") == [], "выставил товар сам"

    # Дальше — руками игрока, родными кнопками игры.
    game.evaluate(_stall_expr("(function(){d.getElementById('verkaufe_markt').click();return true})()"))
    _wait(game, _stall_expr("!!d.forms['form_verkaufe_markt']"), "игра не дошла до комиссии")
    game.evaluate(_stall_expr(
        "(function(){d.forms['form_verkaufe_markt'].elements['verkaufe_markt'].click();return true})()"))
    _wait(game, "(window.__predlozheno||[]).length>0", "предложение не ушло")
    got = game.evaluate("window.__predlozheno")[0]
    assert got["p_id"] == "p6", got
    assert got["skolko"] == "163", got
    assert (got["preis1"], got["preis2"]) == ("0", "07"), got


def test_market_recovers_when_the_stall_is_stuck_on_the_commission(server):
    """Палатка застряла на комиссии от прошлой сделки — выправляемся сами."""
    with stand(server, "?rynok_zastryal_na_komissii=1") as conn:
        _open_stall(conn)
        _click_tile(conn, 6)
        _wait_filled(conn, 6, "163")


def test_helpers_stay_out_of_the_city(game):
    """Гномы и столбик живут ТОЛЬКО в саду.

    Город и рынок игра рисует накладкой ПОВЕРХ сада: `#wimpareaDiv` остаётся
    на месте, и наши гномы, которые по нему равняются, оказывались поверх
    рыночной палатки. Владелец прислал снимок: «помощники перемещаются и на
    рынок, это неправильно».

    Судить по `current_location` нельзя — замер живой игры показал, что в
    городе оно остаётся `garden`. Смотрим на флаг `stadt` самой игры.
    """
    _wait(game, "document.querySelectorAll('#si-helper [data-si-button]').length>0",
          "гномы не появились")
    vidno = ("(function(){var b=document.getElementById('si-helper');"
             "var g=document.getElementById('si-helper-gardens');"
             "return {polosa:b?getComputedStyle(b).display:'нет',"
             " stolbik:g?getComputedStyle(g).display:'нет'}})()")
    v = game.evaluate(vidno)
    assert v["polosa"] != "none", f"в саду гномов не видно: {v}"

    game.evaluate("zeigeStadtMain(1); true")
    _wait(game, "(function(){var b=document.getElementById('si-helper');"
                "return b && getComputedStyle(b).display === 'none'})()",
          "в городе гномы остались на экране")
    v = game.evaluate(vidno)
    assert v["stolbik"] == "none", f"столбик остался в городе: {v}"

    game.evaluate("stadtVerlassen(); true")
    _wait(game, "(function(){var b=document.getElementById('si-helper');"
                "return b && getComputedStyle(b).display !== 'none'})()",
          "вернулись в сад, а гномы не вернулись")

# ── грибы и улитки в столбике ────────────────────────────────────────
#
# Были в карточке пропавшего расширения; владелец попросил их отдельно.
# Точки входа, картинки и русские подписи — собственные у игры, из её
# быстрой навигации (`quicknavi`) и её таблицы стилей:
#   грибы   megafruit   → initMegafruit(),   pilzgarten_premiumwahl.jpg
#   улитки  snailracing → snailracing.init(), SchnellreiseIcon_02.gif

def _nav_icons(conn):
    return conn.evaluate(
        "[].slice.call(document.querySelectorAll('#si-helper-gardens [data-si-nav]'))"
        ".map(function(e){return e.getAttribute('data-si-nav') + '|' + (e.getAttribute('src')||'')})"
    )


def test_mushrooms_and_snails_appear_with_the_games_own_icons(game):
    """Кнопки грибов и улиток стоят в столбике картинками самой игры."""
    _wait(game, "document.querySelectorAll('#si-helper-gardens [data-si-nav]').length>2",
          "столбик не собрался")
    icons = _nav_icons(game)
    griby = [i for i in icons if i.startswith("megafruit|")]
    ulitki = [i for i in icons if i.startswith("snailracing|")]
    assert len(griby) == 1, f"грибов не одна кнопка: {icons}"
    assert len(ulitki) == 1, f"улиток не одна кнопка: {icons}"
    assert "pilzgarten_premiumwahl.jpg" in griby[0], griby
    assert "SchnellreiseIcon_02.gif" in ulitki[0], ulitki
    assert all(i.split("|", 1)[1].startswith("http") for i in icons), icons
    titles = game.evaluate(
        "[].slice.call(document.querySelectorAll('#si-helper-gardens [data-si-nav]'))"
        ".map(function(e){return e.getAttribute('title')})")
    assert "Выращивание грибов" in titles, titles
    assert "Улиточные бега" in titles, titles


def test_mushrooms_and_snails_open_the_right_place(game):
    """Нажатие открывает именно ту локацию и сообщает игре, где мы."""
    _wait(game, "document.querySelectorAll('#si-helper-gardens [data-si-nav]').length>2",
          "столбик не собрался")
    assert game.evaluate("window.__otkryto") == [], "открыл что-то без нажатия"
    for key in ("megafruit", "snailracing"):
        ok = game.evaluate(
            "(function(){var e=document.querySelector('#si-helper-gardens "
            "[data-si-nav=\"%s\"]');if(!e)return false;e.click();return true})()" % key)
        assert ok, f"не нашёл кнопку {key}"
        _wait(game, "window.__otkryto.indexOf('%s')>=0" % key, f"{key} не открылся")
        assert key in game.evaluate("window.__gdeMy"), \
            f"игре не сказали, что мы в {key}: {game.evaluate('window.__gdeMy')}"
    assert game.evaluate("window.__otkryto") == ["megafruit", "snailracing"]


def test_no_buttons_for_places_the_player_does_not_have(server):
    """ГЛАВНОЕ: нет локации — нет и кнопки.

    Игра перечисляет в быстрой навигации только доступное. Кнопка на
    неоткрытое привела бы в никуда или в платное окно — на таком проект уже
    обжёгся диалогом сноса. Столбик при этом остаётся: сады и город на месте.
    """
    with stand(server, "?net_gribov_i_ulitok=1") as conn:
        _wait(conn, "document.querySelectorAll('#si-helper-gardens [data-si-city]').length===2",
              "столбик не собрался")
        time.sleep(1.0)
        icons = _nav_icons(conn)
        assert not [i for i in icons if i.startswith("megafruit|")], icons
        assert not [i for i in icons if i.startswith("snailracing|")], icons
        assert conn.evaluate(
            "document.querySelectorAll('#si-helper-gardens [data-si-gardens] , "
            "#si-helper-gardens img').length") > 2, "столбик опустел целиком"


def test_second_injection_leaves_no_dead_column(server):
    """Повторная вставка кода не оставляет мёртвый столбик и листок.

    Столбик мест и листок рынка живут ОТДЕЛЬНО от `#si-helper`, и защита
    «панель уже есть» их не покрывала: на живой игре 2026-09-10 остался
    висеть прежний столбик, а новый лёг поверх. Снаружи это выглядит как
    двойные кнопки, а нажимается мёртвая.
    """
    with fresh_browser() as conn:
        conn.inject_on_every_load(payload.load("panel.js"))
        conn.navigate(f"{server['game']}/{STAND}")
        _wait(conn, "document.querySelectorAll('#si-helper-gardens [data-si-garden]').length>1",
              "столбик не появился")
        # вставляем код второй раз, как это делает перезаход
        conn.evaluate("var e=document.getElementById('si-helper'); if(e) e.remove(); true")
        conn.evaluate(payload.load("panel.js"))
        _wait(conn, "document.querySelectorAll('#si-helper-gardens [data-si-garden]').length>1",
              "столбик не пересобрался")
        time.sleep(1.0)
        assert conn.evaluate("document.querySelectorAll('#si-helper-gardens').length") == 1, \
            "остался мёртвый столбик"
        assert conn.evaluate("document.querySelectorAll('#si-helper').length") == 1
        assert conn.evaluate(
            "document.querySelectorAll('#si-helper-gardens [data-si-garden]').length") == 5


def test_market_reports_the_price_with_a_balloon(game):
    """О подставленной цене помощник сообщает шариком, а не плашкой.

    Реплика гнома в палатке не годится: у игры это подсказка по наведению
    мыши, соседний `clrSprechText` стирает её сразу — на живой игре
    2026-09-10 текст до глаз игрока не доживал.
    """
    _open_stall(game)
    _click_tile(game, 6)
    _wait_filled(game, 6, "163")
    _wait(game, "/163/.test(" + _status_expr() + ") && /0,07/.test(" + _status_expr() + ")",
          "шарик не назвал количество и цену")
    assert game.evaluate("document.querySelectorAll('#si-helper-market').length") == 0


def test_market_reopens_the_city_after_leaving_it(game):
    """Вышли из города — гном снова его открывает.

    Живая игра, выходя из города, лишь ПРЯЧЕТ накладку: страница палатки
    остаётся в кадре. Помощник из-за этого решал, что палатка уже открыта,
    и нажатие на гнома не делало ничего видимого — поймано 2026-09-10.
    Признак «палатка открыта» теперь включает открытый город.
    """
    _open_stall(game)
    assert game.evaluate("window.__city") == [1]

    game.evaluate("stadtVerlassen(); true")
    _wait(game, "(function(){var e=document.getElementById('stadt');"
                "return e && getComputedStyle(e).display === 'none'})()",
          "город не закрылся")
    # Нажатие, попавшее в занятый момент, помощник отклоняет словами —
    # поэтому жмём до результата, а не один раз наудачу.
    for _ in range(6):
        _press(game, "sell")
        if game.evaluate("window.__city.length") == 2:
            break
        time.sleep(1.0)
    assert game.evaluate("window.__city.length") == 2, "гном не открыл город заново" 
    _wait(game, _stall_expr("!!d.getElementById('produkt_anzahl')"),
          "палатка не открылась во второй раз")


# ── аудит 2026-09-11: первая живая проверка игроком ──────────────────
#
# «Панель с садами пропала, поливайка и сажалка не работали». Разбор по CSS
# игры: `#multiframe` не спрятан стилями, его прячет код по событиям, а до
# того он стоит на странице видимым и ПУСТЫМ. Прежняя проверка «мы в саду»
# судила по одному `display` — и гасила помощника целиком. Стенд с тех пор
# держит такой контейнер всегда: на старом коде гномы не появляются вовсе.


def test_helpers_ignore_an_empty_overlay(game):
    """Пустой контейнер накладки — не повод прятаться."""
    _wait(game, "document.querySelectorAll('#si-helper [data-si-button]').length>0",
          "гномы не появились")
    v = game.evaluate("""(function(){
        var m=document.getElementById('multiframe');
        var b=document.getElementById('si-helper');
        var g=document.getElementById('si-helper-gardens');
        return {konteyner:m?getComputedStyle(m).display:'нет',
                polosa:b?getComputedStyle(b).display:'нет',
                stolbik:g?getComputedStyle(g).display:'нет'};
    })()""")
    assert v["konteyner"] == "block", f"стенд должен держать пустую накладку видимой: {v}"
    assert v["polosa"] != "none", f"гномы спрятались из-за пустого контейнера: {v}"
    assert v["stolbik"] != "none", f"столбик спрятался из-за пустого контейнера: {v}"


def test_helpers_hide_behind_a_loaded_overlay(server):
    """Накладка с настоящим содержимым — прячемся, и говорим почему."""
    with stand(server, "?multiframe=polny") as conn:
        _wait(conn, "(function(){var b=document.getElementById('si-helper');"
                    "return b && getComputedStyle(b).display === 'none'})()",
              "за открытой накладкой гномы остались на экране")
        state = conn.evaluate("window.SI_HELPER && window.SI_HELPER.sostoyanie")
        assert "multiframe" in state, f"самоотчёт не назвал причину: {state}"


def test_helper_waits_for_a_late_game(server):
    """Игра дописала свои объекты через три секунды — помощник дождался.

    Прежний запуск проверял игру один раз на `load` и уходил навсегда.
    """
    with stand(server, "?pozdno=1") as conn:
        _wait(conn, "document.querySelectorAll('#si-helper [data-si-button]').length>0",
              "помощник не дождался игры")
        state = conn.evaluate("window.SI_HELPER && window.SI_HELPER.sostoyanie")
        assert state.startswith("в саду"), f"после ожидания состояние не «в саду»: {state}"


def test_panel_reports_its_own_state(game):
    """Панель выкладывает состояние — программа пишет его в журнал.

    Единственное, что возвращается с чужой машины, — журнал; по нему должно
    быть видно, появился ли помощник, спрятался ли и почему, сколько садов.
    """
    _wait(game, "document.querySelectorAll('#si-helper [data-si-button]').length>0",
          "гномы не появились")
    _wait(game, "/^в саду/.test((window.SI_HELPER||{}).sostoyanie||'')", "состояние не выложено")
    state = game.evaluate("window.SI_HELPER.sostoyanie")
    assert "гномов 7" in state, f"число гномов не сходится: {state}"
    assert "столбике" in state and "сад 1" in state, f"в состоянии нет садов: {state}"

    game.evaluate("zeigeStadtMain(1); true")
    _wait(game, "/^спрятан/.test((window.SI_HELPER||{}).sostoyanie||'')",
          "в городе состояние не переключилось")


def test_one_failing_step_does_not_stop_the_helper(server):
    """Сбой одного шага цикла не останавливает панель и попадает в самоотчёт.

    `tick()` идёт раз в секунду и держит всё разом. Прежде исключение в
    любом шаге обрывало цикл молча и навсегда. Здесь полоса помощников
    ломает замер — а столбик всё равно собирается, и причина сбоя названа.
    """
    with stand(server, "?slomat=polosa") as conn:
        _wait(conn, "/^в саду/.test((window.SI_HELPER||{}).sostoyanie||'')",
              "цикл остановился на первом же сбое")
        state = conn.evaluate("window.SI_HELPER.sostoyanie")
        assert "сбой" in state and "расстановка" in state, f"сбой не назван: {state}"
        assert "сломано нарочно" in state, f"причина сбоя не дошла до отчёта: {state}"
        n = conn.evaluate("document.querySelectorAll('#si-helper-gardens img').length")
        assert n > 2, f"столбик не собрался, хотя сбой был в другом шаге: {n}"


# ── живая игра 2026-09-11: две находки, которых стенд не показывал ────


def test_wimp_block_works_when_sheets_arrive_with_the_garden(server):
    """Листки пришли ВМЕСТЕ с садом — блок выгодности всё равно есть.

    На живой игре `verkaufajax` + `do:getData` не вызывается ни разу: листки
    лежат в данных, пришедших с садом. Расчёт, ждавший ответа сервера, не
    появлялся у игрока вовсе — он так и написал: «процентов выгоды у
    попрошаек нет». Читать надо то, что игра УЖЕ нарисовала.
    """
    with stand(server, "?listki=srazu") as conn:
        _open_wimp(conn, 11)
        assert conn.evaluate("window.__wimpAsks") == 0, "на сервер ходить не должны были"
        text = _wimp_text(conn)
        assert "+20%" in text, f"процент не посчитан из нарисованного: {text}"


def test_the_games_own_beetle_is_not_duplicated(game):
    """Жук один. Если у игры свой автомат посадки — своего не рисуем.

    Игрок прислал снимок: «у тебя два жука, а должен быть один у гнома
    сверху». Игра рисует `#wimpareaAutoplant` сама, а мы дорисовывали такой
    же ниже.
    """
    _wait(game, "document.querySelectorAll('#si-helper [data-si-button]').length>0",
          "гномы не появились")
    r = game.evaluate("""(function(){
        var svoy = document.querySelector('#si-helper [data-si-button=plant]');
        var igry = document.getElementById('wimpareaAutoplant');
        var n = 0, imgs = document.getElementsByTagName('img');
        for (var i=0;i<imgs.length;i++){
            if ((imgs[i].getAttribute('src')||'').indexOf('anpflanzautomat')>=0) n++;
        }
        if (igry && /anpflanz/.test(getComputedStyle(igry).backgroundImage||'')) n++;
        var kart = svoy && (svoy.getAttribute('src')||'').indexOf('anpflanz')>=0
                   ? svoy.getAttribute('src') : null;
        return {жуковВсего:n, нашЖук:!!svoy, жукИгры:!!igry, нашаКартинка:kart};
    })()""")
    assert r["жукИгры"], "стенд должен держать автомат посадки игры"
    assert r["жуковВсего"] == 1, f"жуков на экране должно быть ровно один: {r}"
    # Наш элемент остаётся — но прозрачной накладкой, а не вторым жуком.
    assert r["нашЖук"], "накладка для нажатия должна быть на месте"
    assert r["нашаКартинка"] is None, f"наш жук всё ещё рисует свою картинку: {r}"


def test_our_pad_covers_the_games_beetle(game):
    """Наш жук — прозрачная накладка ровно поверх жука игры.

    Видимый жук один — её. Нажатие ловим мы: платный автомат игры работает
    на покупаемых «зарядах», и тратить их молча нельзя.
    """
    _wait(game, "!!document.querySelector('#si-helper [data-si-nakladka]')",
          "накладка поверх жука игры не появилась")
    r = game.evaluate("""(function(){
        function b(e){var x=e.getBoundingClientRect();
            return {l:Math.round(x.left),t:Math.round(x.top),w:Math.round(x.width),h:Math.round(x.height)};}
        var nash=b(document.querySelector('#si-helper [data-si-button=plant]'));
        var igry=b(document.getElementById('wimpareaAutoplant'));
        return {наш:nash, игры:igry,
                прозрачный:(document.querySelector('#si-helper [data-si-button=plant]').getAttribute('src')||'').indexOf('anpflanz')<0,
                вышеПоСлоям: getComputedStyle(document.getElementById('si-helper')).zIndex};
    })()""")
    assert r["прозрачный"], f"наш жук всё ещё рисует свою картинку: {r}"
    assert abs(r["наш"]["l"] - r["игры"]["l"]) <= 2 and abs(r["наш"]["t"] - r["игры"]["t"]) <= 2, (
        f"накладка не совпала с жуком игры: {r}")
    assert r["наш"]["w"] == r["игры"]["w"] and r["наш"]["h"] == r["игры"]["h"], (
        f"размер накладки не тот: {r}")

    game.evaluate("window.__igraAvtomat = 0; selected = 1; true")
    _press(game, "plant")
    _wait(game, "/Посажено|Свободных|полке/.test(" + _status_expr() + ")",
          "нажатие по накладке ничего не сделало")
    assert game.evaluate("window.__igraAvtomat") == 0, (
        "сработал платный автомат игры — заряды тратить нельзя")


def test_own_beetle_returns_without_the_games_one(server):
    """У игры автомата нет — рисуем своего жука, возможность не пропадает."""
    with stand(server, "?bez_avtomata=1") as conn:
        _wait(conn, "document.querySelectorAll('#si-helper [data-si-button=plant]').length>0",
              "своего жука нет")
        src = conn.evaluate(
            "document.querySelector('#si-helper [data-si-button=plant]').getAttribute('src')")
        assert "anpflanzautomat" in src, f"свой жук без картинки игры: {src}"


def test_own_beetle_comes_back_when_the_games_one_disappears(game):
    """Жук игры пропал посреди работы — свой возвращается с картинкой и размером.

    Игра перерисовывает полосу сама, и её автомат может исчезнуть (другой
    сад, другой уровень). Накладка без жука под ней — невидимая кнопка,
    которую человеку не нажать: возможность посадки пропала бы молча.
    """
    _wait(game, "!!document.querySelector('#si-helper [data-si-nakladka]')",
          "накладка не появилась")
    game.evaluate("document.getElementById('wimpareaAutoplant').remove(); true")
    _wait(game, "!document.querySelector('#si-helper [data-si-nakladka]')",
          "накладка осталась, хотя жука игры уже нет")
    r = game.evaluate("""(function(){
        var e=document.querySelector('#si-helper [data-si-button=plant]');
        var b=e.getBoundingClientRect();
        return {src:e.getAttribute('src')||'', w:Math.round(b.width), h:Math.round(b.height)};
    })()""")
    assert "anpflanzautomat" in r["src"], f"свой жук не вернул картинку: {r}"
    assert r["w"] == 45 and r["h"] == 45, f"свой жук не вернул размер: {r}"


# ── сбор урожая и обход всех садов (2026-09-14) ──────────────────────
#
# Замер живой игры: у игрока ПЯТЬ садов и в каждом по 204 созревших. Жнец
# игры у него НАНЯТ (`.harvest` без `off`, onclick `gardenjs.harvestAll()`),
# а поливальщик нет. Отсюда два правила, которые тут и проверяются:
#   • пока жнец игры нанят, своей кнопки сбора быть не должно — иначе в
#     полосе окажется два жнеца, как было с двумя жуками;
#   • обход должен звать ЕЁ жнеца, когда он нанят, и собирать сам, когда нет.
#
# В стенде сады 2–5 маленькие по делу: созрело 2+1+0+3, сухо 1+2+1+0.
# Вместе с садом 1 (созрело 4, полить 12) обход без семян даёт
# собрано 10, полито 16.


def _svodka(conn):
    """Что ушло бы на сервер: сбор своими руками, сбор жнецом, полив."""
    return conn.evaluate("""(function(){
        var ernte=[], zhnec=[], wasser=[];
        window.__sent.forEach(function(s){
            if(s.file==='ernte') s.felder.forEach(function(n){ernte.push(n)});
            if(s.file==='harvestAll') zhnec.push({сад:s.сад, клеток:s.felder.length});
            if(s.file==='wasser') s.felder.forEach(function(n){wasser.push(n)});
        });
        return {ernte:ernte.sort(function(a,b){return a-b}), zhnec:zhnec, wasser:wasser.length,
                danger:window.__danger, switches:window.__gardenSwitches};
    })()""")


def test_harvest_button_hidden_while_the_game_reaper_is_hired(game):
    """Жнец игры нанят — своего не рисуем вовсе."""
    _wait(game, "document.querySelectorAll('#si-helper [data-si-button]').length>0",
          "гномы не появились")
    assert game.evaluate(
        "!document.querySelector('#si-helper [data-si-button=harvest]')"
    ), "при нанятом жнеце игры в полосе появился второй жнец"


def test_harvest_button_appears_when_the_reaper_is_not_hired(server):
    """Жнеца игры нет — своя кнопка сбора появляется, и это её картинка."""
    with stand(server, "?zhnec=net") as conn:
        _wait(conn, "!!document.querySelector('#si-helper [data-si-button=harvest]')",
              "своя кнопка сбора не появилась")
        src = conn.evaluate(
            "document.querySelector('#si-helper [data-si-button=harvest]').getAttribute('src')")
        assert "sensenzwerg.gif" in src, f"жнец нарисован не картинкой игры: {src}"


def test_harvest_takes_only_ripe_vegetables(server):
    """Собираем ровно созревшие овощи и не трогаем опасное.

    В саду стенда созревших четыре: 9, 10, 25, 26. Рядом лежат постройка
    (платный снос), трава с сорняком (молчаливая прополка) и растение из
    списка ускорения — всё это сбор обязан обойти.
    """
    with stand(server, "?zhnec=net") as conn:
        _wait(conn, "!!document.querySelector('#si-helper [data-si-button=harvest]')",
              "своя кнопка сбора не появилась")
        _press(conn, "harvest")
        _wait(conn, "/Собрано|Созревшего/.test(" + _status_expr() + ")", "сбор не завершился")
        s = _svodka(conn)
        assert s["ernte"] == [9, 10, 25, 26], f"собрано не то: {s['ernte']}"
        assert s["danger"] == [], f"сбор задел опасное: {s['danger']}"
        assert "Собрано 4" in _status(conn), _status(conn)


def test_harvest_skips_a_plant_the_game_would_ask_about(server):
    """Клетка созрела по данным, а картинка говорит «не выросло» — не трогаем.

    На такой игра показывает окно «действительно собрать?», и сбор потерял бы
    урожай. Стенд ставит этот рассинхрон на клетку 9.
    """
    with stand(server, "?zhnec=net&nedozrelo=1") as conn:
        _wait(conn, "!!document.querySelector('#si-helper [data-si-button=harvest]')",
              "своя кнопка сбора не появилась")
        _press(conn, "harvest")
        _wait(conn, "/Собрано|Созревшего/.test(" + _status_expr() + ")", "сбор не завершился")
        s = _svodka(conn)
        assert 9 not in s["ernte"], f"собрали клетку, о которой игра спросила бы: {s['ernte']}"
        assert s["ernte"] == [10, 25, 26], f"собрано не то: {s['ernte']}"
        assert s["danger"] == [], f"игра показала бы окно: {s['danger']}"


def test_harvest_in_the_water_garden_takes_only_ripe_water_plants(server):
    """Сбор в водном саду: берём созревшее и не трогаем сорняк.

    В стенде созревших три — 24, 40, 41. Рядом лежит СОЗРЕВШИЙ ПО ВОЗРАСТУ
    сорняк (45): уборка сорняка в водном саду платная, и сбор без проверки
    вида полез бы в него. Недозревшие рядом тоже есть — на них игра показала
    бы окно «действительно собрать?», и урожай бы пропал.

    Кнопку «собрать всё» водного сада запираем (`wg_zhnec=net`): иначе сбор
    пойдёт ею, и проверка клетка за клеткой не состоится.
    """
    with stand(server, "?wg=1&wg_zhnec=net") as conn:
        _wait(conn, "window.watergarden && watergarden.isOpen === true", "водный сад не открылся")
        _wait(conn, "!!document.querySelector('#si-helper [data-si-button=harvest]')",
              "своей кнопки сбора нет")
        _press(conn, "harvest")
        _wait(conn, "/Собрано|Созревшего/.test(" + _status_expr() + ")", "сбор не завершился")
        собрано = conn.evaluate("""(function(){
            var o=[]; window.__sent.forEach(function(s){
                (s.harvest||[]).forEach(function(n){o.push(n)});
            });
            return o.sort(function(a,b){return a-b});
        })()""")
        assert собрано == [24, 40, 41], f"собрано не то: {собрано}"
        assert conn.evaluate("window.__danger") == [], "сбор задел опасное"
        assert conn.evaluate("window.__cacheCalls") == 0, "тронули обычный сад из водного"


def test_water_garden_harvest_uses_its_own_button_when_it_is_free(server):
    """Кнопка «собрать всё» водного сада свободна — зовём её, а не двести клеток.

    Право лежит в разметке: метод есть всегда, а кнопка бывает заперта.
    Замер живой игры 2026-09-14: она свободна.
    """
    with stand(server, "?wg=1") as conn:
        _wait(conn, "window.watergarden && watergarden.isOpen === true", "водный сад не открылся")
        assert conn.evaluate(
            "!document.querySelector('#si-helper [data-si-button=harvest]')"
        ), "нарисовали свой сбор поверх свободной кнопки игры"


def test_round_walks_every_garden_and_does_the_work(game):
    """Обход: по каждому саду собрать → полить, и вернуться домой.

    Семена не выбраны — значит посадки нет, и это сказано словами.
    Ожидание по стенду: собрано 10 (4+2+1+0+3), полито 16 (12+1+2+1+0).
    """
    _wait(game, "document.querySelectorAll('#si-helper [data-si-button=round]').length>0",
          "кнопки обхода нет")
    game.evaluate("selected = null; true")
    _press(game, "round")
    _wait(game, "/Обход/.test(" + _status_expr() + ")", "обход не закончился", timeout=90)

    s = _svodka(game)
    итог = _status(game)
    assert s["danger"] == [], f"обход задел опасное: {s['danger']}"
    # жнец игры нанят — звали его, а не собирали руками
    assert s["ernte"] == [], f"собирали руками при нанятом жнеце: {s['ernte']}"
    собрано = sum(z["клеток"] for z in s["zhnec"])
    assert собрано == 10, f"по обычным садам собрано не то: {s['zhnec']}"
    assert s["wasser"] == 16, f"по обычным садам полито не то: {s['wasser']}"

    # Водный сад — последняя остановка. Там своя очередь и своя кнопка
    # «собрать всё»: созревших три (24, 40, 41), полить три (5, 20, 22).
    водный = game.evaluate("""(function(){
        var собрано=0, полито=0;
        window.__sent.forEach(function(x){
            if(x.file==='watergardenHarvestAll') собрано += x.felder.length;
            if(x.file==='watergardenCache' && x.water) полито += x.water.length;
        });
        return {собрано:собрано, полито:полито};
    })()""")
    assert водный["собрано"] == 3, f"в водном саду собрано не то: {водный}"
    assert водный["полито"] >= 3, f"в водном саду полито не то: {водный}"

    assert "собрано 13" in итог and "полито 19" in итог, итог
    assert "Водный сад тоже" in итог, итог
    assert "Семена не выбраны" in итог, итог
    # побывали во всех садах и вернулись в первый
    assert s["switches"][-1] == 1, f"не вернулись в исходный сад: {s['switches']}"
    for n in (2, 3, 4, 5):
        assert n in s["switches"], f"сад {n} пропущен: {s['switches']}"


def test_round_harvests_by_hand_when_the_reaper_is_not_hired(server):
    """Жнеца игры нет — обход собирает сам и его не зовёт."""
    with stand(server, "?zhnec=net") as conn:
        _wait(conn, "document.querySelectorAll('#si-helper [data-si-button=round]').length>0",
              "кнопки обхода нет")
        conn.evaluate("selected = null; true")
        _press(conn, "round")
        _wait(conn, "/Обход/.test(" + _status_expr() + ")", "обход не закончился", timeout=90)
        s = _svodka(conn)
        assert "жнец не нанят, а его позвали" not in s["danger"], s["danger"]
        assert s["zhnec"] == [], f"позвали жнеца, которого нет: {s['zhnec']}"
        assert len(s["ernte"]) == 10, f"своими руками собрано не то: {s['ernte']}"


def test_round_plants_when_seeds_are_chosen(game):
    """Семена выбраны — обход ещё и сажает освободившееся."""
    _wait(game, "document.querySelectorAll('#si-helper [data-si-button=round]').length>0",
          "кнопки обхода нет")
    game.evaluate("regal.selectProduct(1); true")     # морковь, её 40 штук
    _press(game, "round")
    _wait(game, "/Обход/.test(" + _status_expr() + ")", "обход не закончился", timeout=120)
    итог = _status(game)
    посажено = game.evaluate(
        "(function(){var n=0;window.__planted.forEach(function(p){n+=p.felder.length});return n})()")
    assert посажено > 0, f"обход ничего не посадил: {итог}"
    assert "Семена не выбраны" not in итог, итог


def test_round_refuses_in_the_water_garden(water_stand):
    """Обход идёт по обычным садам: из водного — отказ словами."""
    conn = water_stand
    _press(conn, "round")
    _wait(conn, "/водный он обойдёт сам/.test(" + _status_expr() + ")",
          "не подсказал начать обход из обычного сада")
    assert conn.evaluate("window.__gardenSwitches.length") == 0, "переключал сады из водного"


# ── птичья почта ─────────────────────────────────────────────────────


def _pochta(conn):
    """Что почта отправила бы на сервер, и что стало с хозяйством."""
    return conn.evaluate("""(function(){
        var q = [];
        window.__pochtaZaprosy.forEach(function(z){
            var s = z['do'];
            if (z.slot !== undefined) s += ' slot=' + z.slot;
            if (z.jobslot !== undefined) s += ' jobslot=' + z.jobslot;
            if (z.house !== undefined) s += ' house=' + z.house;
            if (z.bird !== undefined) s += ' bird=' + z.bird;
            q.push(s);
        });
        return {zaprosy: q, danger: window.__danger, gde: window.__gdeMy,
                dengi: parseFloat(player_bar), polka: {1: regal.getCount(1), 2: regal.getCount(2),
                11: regal.getCount(11), 48: regal.getCount(48)},
                otkryta: getComputedStyle(document.getElementById('birds')).display !== 'none',
                dialogs: window.__dialogs.length};
    })()""")


def _zhdat_pochtu(conn, timeout: float = 60.0) -> str:
    # Шарики по ходу тоже начинаются с «Почта:» — ждём именно итог.
    _wait(conn, "/Почта: забрано/.test(" + _status_expr() + ")", "почта не закончилась", timeout=timeout)
    return _status(conn)


def test_post_gnome_and_icon_appear_only_when_the_player_has_the_post(game, server):
    """Кнопка почты и её иконка в столбике — только если почта у игрока есть.

    Гном — встроенный рисунок владельца 30×45; иконка в столбике — игры."""
    _wait(game, "document.querySelectorAll('#si-helper [data-si-button=post]').length>0",
          "гнома почты нет")
    gnome = game.evaluate(
        "(function(){var b=document.querySelector('#si-helper [data-si-button=post]');"
        "return {src:(b.getAttribute('src')||'').slice(0,22), w:b.getAttribute('data-si-w'), h:b.getAttribute('data-si-h'),"
        " ok:b.complete && b.naturalWidth>0}})()")
    assert gnome["src"] == "data:image/png;base64,", gnome
    assert (gnome["w"], gnome["h"]) == ("30", "45"), gnome
    assert gnome["ok"], "встроенная картинка гнома почты не загрузилась"
    icons = _nav_icons(game)
    assert any(i.startswith("birds|") and "Vogelposticon01.gif" in i for i in icons), icons

    with stand(server, "?pochty=net") as conn:
        time.sleep(1.5)
        assert conn.evaluate("document.querySelectorAll('#si-helper [data-si-button=post]').length") == 0, \
            "гном почты нарисован игроку без почты"
        assert not any(i.startswith("birds|") for i in _nav_icons(conn)), "иконка почты без почты"


def test_post_collects_feeds_sends_and_buys(game):
    """Одно нажатие: забрать готовые, купить птицу в опустевший скворечник,
    покормить и разослать — всё функциями игры и ровно в этом порядке.

    Стенд: готовы заказы 2 и 5; птица дома 3 после сдачи уходит на пенсию;
    новый заказ слота 5 (выносливость 8) берёт свежекупленная Ласточка,
    заказ слота 2 (7) — Ласточка дома 1 после корма (было 6); заказ 3 (груз 6)
    тянет только Попугай, а он летит. Летящих не кормят и не шлют — Голубь
    дома 4 слабее Ласточек и достался бы лёгкому заказу первым.
    """
    _wait(game, "document.querySelectorAll('#si-helper [data-si-button=post]').length>0",
          "гнома почты нет")
    _press(game, "post")
    итог = _zhdat_pochtu(game)
    p = _pochta(game)

    assert p["danger"] == [], f"почта задела опасное: {p['danger']}"
    assert p["zaprosy"] == [
        "birds_init",
        "birds_finish_job slot=2",
        "birds_finish_job slot=5",
        "birds_buy_bird slot=3 bird=5",
        "birds_start_job jobslot=5 house=3",
        "birds_feed_bird slot=1",
        "birds_start_job jobslot=2 house=1",
    ], p["zaprosy"]
    assert "забрано 2" in итог and "отправлено 2" in итог and "покормлено 1" in итог, итог
    assert "Куплено: Ласточки за 4.000,00 сТ" in итог, итог
    assert "заказ 3 — нет свободной птицы: нужна сила 6 и выносливость 8" in итог, итог
    assert "Перьев +123" in итог, итог          # 111 + 12 с двух наград
    # деньги: две награды минус птица; полка: продукты заказов и корм ушли
    assert p["dengi"] == 20000 + 5719 + 1035 - 4000, p["dengi"]
    assert p["polka"] == {"1": 35, "2": 8, "11": 27, "48": 8}, p["polka"]
    # экран закрыт, игрок вернулся в свой сад, окон наград не осталось
    assert not p["otkryta"], "почта осталась открытой"
    assert p["gde"][-1] == "garden1", p["gde"]
    assert p["dialogs"] == 0, "игра показала окно, а помощник не подменил награду"


def test_post_never_pays_coins_for_a_retired_parrot(server):
    """Ушёл Попугай — его продают за Coins, и помощник его НЕ покупает,
    а говорит об этом. Свободных птиц на второй заказ тогда нет."""
    with stand(server, "?pochta_popugay_ushel=1") as conn:
        _wait(conn, "document.querySelectorAll('#si-helper [data-si-button=post]').length>0",
              "гнома почты нет")
        _press(conn, "post")
        итог = _zhdat_pochtu(conn)
        p = _pochta(conn)
        assert p["danger"] == [], p["danger"]
        assert not any(z.startswith("birds_buy_bird") for z in p["zaprosy"]), p["zaprosy"]
        assert "скворечник 3 пуст: Попугай продаётся не за сТ" in итог, итог
        assert "отправлено 1" in итог and "покормлено 1" in итог, итог
        assert "заказ 3 — не хватает Морковь ×460" in итог, итог
        assert "заказ 2 — нет свободной птицы" in итог, итог
        assert p["dengi"] == 20000 + 5719 + 1035, p["dengi"]


def test_post_stops_with_the_games_own_words_when_the_server_refuses(server):
    """Сервер отказал — цикл останавливается, отказ пересказан словами игры,
    сделанное названо, экран закрыт. Не крутится до бесконечности."""
    with stand(server, "?pochta_otkaz=1") as conn:
        _wait(conn, "document.querySelectorAll('#si-helper [data-si-button=post]').length>0",
              "гнома почты нет")
        _press(conn, "post")
        итог = _zhdat_pochtu(conn, timeout=40)
        p = _pochta(conn)
        assert "Игра ответила" in итог and "Сервер отказал нарочно" in итог, итог
        assert "Успел: Почта: забрано 2" in итог, итог
        assert sum(1 for z in p["zaprosy"] if z.startswith("birds_start_job")) == 1, p["zaprosy"]
        assert not p["otkryta"], "после отказа почта осталась открытой"
        assert conn.evaluate("!document.querySelector('#si-helper [data-si-button=post]').disabled"), \
            "после отказа гномы остались притушенными"


def test_helpers_hide_behind_the_post_screen(server):
    """Экран почты ложится поверх сада и полосы — гномов там быть не должно
    (снимок живой игры 2026-09-18: они стояли на её слоте заказов 10)."""
    with stand(server, "?pochta_otkryta=1") as conn:
        _wait(conn, "getComputedStyle(document.getElementById('birds')).display !== 'none'",
              "почта не открылась")
        _wait(conn, "(function(){var b=document.getElementById('si-helper');"
                    "return b && getComputedStyle(b).display === 'none'})()",
              "за открытой почтой гномы остались на экране")
        state = conn.evaluate("window.SI_HELPER && window.SI_HELPER.sostoyanie")
        assert "почта" in state, f"самоотчёт не назвал причину: {state}"
        conn.evaluate("birds.close(); true")
        _wait(conn, "getComputedStyle(document.getElementById('si-helper')).display !== 'none'",
              "после закрытия почты гномы не вернулись")


def test_round_ends_at_the_post(game):
    """Обход: сады → водный сад → почта, и домой. Почта — последняя."""
    _wait(game, "document.querySelectorAll('#si-helper [data-si-button=round]').length>0",
          "кнопки обхода нет")
    game.evaluate("selected = null; true")
    _press(game, "round")
    _wait(game, "/Обход/.test(" + _status_expr() + ")", "обход не закончился", timeout=120)
    итог = _status(game)
    assert "Почта: забрано 2, отправлено 2, покормлено 1" in итог, итог
    порядок = game.evaluate("window.__sent.map(function(s){return s.file})")
    assert "birds" in порядок and "watergardenCache" in порядок, порядок
    assert порядок.index("watergardenCache") < порядок.index("birds"), f"почта раньше водного сада: {порядок}"
    p = _pochta(game)
    assert p["danger"] == [], p["danger"]
    assert not p["otkryta"], "после обхода почта осталась открытой"
    s = _svodka(game)
    assert s["switches"][-1] == 1, f"не вернулись в исходный сад: {s['switches']}"


def test_round_skips_the_post_when_the_player_has_none(server):
    """Нет почты — обход её молча пропускает, без слов и без запросов."""
    with stand(server, "?pochty=net") as conn:
        _wait(conn, "document.querySelectorAll('#si-helper [data-si-button=round]').length>0",
              "кнопки обхода нет")
        conn.evaluate("selected = null; true")
        _press(conn, "round")
        _wait(conn, "/Обход/.test(" + _status_expr() + ")", "обход не закончился", timeout=120)
        итог = _status(conn)
        assert "Почта" not in итог, итог
        assert conn.evaluate("window.__pochtaZaprosy.length") == 0, "обход ходил на почту, которой нет"


# ── пасека ───────────────────────────────────────────────────────────


def _paseka(conn):
    """Что пасека отправила бы на сервер, и что стало с ульями и сотами."""
    return conn.evaluate("""(function(){
        var q = [];
        window.__pasekaZaprosy.forEach(function(z){
            var s = z['do'];
            if (z.id !== undefined) s += ' id=' + z.id;
            if (z.tour !== undefined) s += ' tour=' + z.tour;
            q.push(s);
        });
        var letyat = [];
        for (var k in BEES_STATE.hives) { var h = BEES_STATE.hives[k]; if (h.tour_start && h.tour_remain > 0) letyat.push(+k); }
        return {zaprosy: q, danger: window.__danger, gde: window.__gdeMy, letyat: letyat,
                soty312: BEES_STOCK[312], polka312: regal.getCount(312),
                otkryta: getComputedStyle(document.getElementById('bees')).display !== 'none'};
    })()""")


def _zhdat_paseku(conn, timeout: float = 60.0) -> str:
    _wait(conn, "/Пасека: отправлено/.test(" + _status_expr() + ")", "пасека не закончилась", timeout=timeout)
    return _status(conn)


def test_bees_gnome_appears_only_when_the_player_has_the_apiary(game, server):
    """Гном-пчеловод — картинка игры, и только тем, у кого пасека есть."""
    _wait(game, "document.querySelectorAll('#si-helper [data-si-button=bees]').length>0",
          "гнома пасеки нет")
    src = game.evaluate("document.querySelector('#si-helper [data-si-button=bees]').getAttribute('src')")
    assert "pics/bees/beekeeper.png" in src, src
    with stand(server, "?paseki=net") as conn:
        time.sleep(1.5)
        assert conn.evaluate("document.querySelectorAll('#si-helper [data-si-button=bees]').length") == 0, \
            "гном пасеки нарисован игроку без пасеки"


def test_bees_sends_returned_hives_their_own_way_and_bottles_full_combs(game):
    """Одно нажатие: разлить полные соты, отправить вернувшихся тем же маршрутом.

    Стенд: улей 1 летал «Тщательно» → снова «Тщательно»; 2 в полёте — не
    трогать; 3 — цветы не посажены, сказать; 4 без истории → маршрутом
    большинства («Тщательно»); 5 — соты полны → сперва разлить, потом
    отправить; 6 не куплен; 7 летал платным маршрутом → сказать, не слать.
    """
    _wait(game, "document.querySelectorAll('#si-helper [data-si-button=bees]').length>0",
          "гнома пасеки нет")
    _press(game, "bees")
    итог = _zhdat_paseku(game)
    p = _paseka(game)
    assert p["danger"] == [], f"пасека задела опасное: {p['danger']}"
    assert p["zaprosy"] == [
        "bees_init",
        "bees_fill",
        "bees_startflight id=1 tour=2",
        "bees_startflight id=4 tour=2",
        "bees_startflight id=5 tour=2",
    ], p["zaprosy"]
    assert "Пасека: отправлено 3 («Тщательно»), в полёте 1 (ближайший вернётся через 1 ч 23 мин)." in итог, итог
    assert "Мёд разлит из 1 соты." in итог, итог
    assert "улей 3: в садах не посажено Салат" in итог, итог
    assert "улей 7: прошлый маршрут платный — не отправляю" in итог, итог
    assert sorted(p["letyat"]) == [1, 2, 4, 5], p["letyat"]
    assert p["soty312"] == 0 and p["polka312"] == 120000, p   # мёд ушёл на полку
    assert not p["otkryta"], "пасека осталась открытой"
    assert p["gde"][-1] == "garden1", p["gde"]


def test_bees_stops_with_the_games_own_words_when_the_server_refuses(server):
    """Три отказа словами подряд — остановка с этими словами (сервер что-то
    говорит, долбить дальше незачем), экран закрыт, гномы отпущены."""
    with stand(server, "?paseka_otkaz=1") as conn:
        _wait(conn, "document.querySelectorAll('#si-helper [data-si-button=bees]').length>0",
              "гнома пасеки нет")
        _press(conn, "bees")
        итог = _zhdat_paseku(conn, timeout=40)
        p = _paseka(conn)
        assert "Игра ответила" in итог and "Сервер отказал нарочно" in итог, итог
        assert sum(1 for z in p["zaprosy"] if z.startswith("bees_startflight")) == 3, p["zaprosy"]
        assert not p["otkryta"], "после отказа пасека осталась открытой"
        assert conn.evaluate("!document.querySelector('#si-helper [data-si-button=bees]').disabled"), \
            "после отказа гномы остались притушенными"


def test_helpers_hide_behind_the_apiary_screen(server):
    """Экран пасеки ложится поверх сада — гномы прячутся, как за почтой."""
    with stand(server, "?paseka_otkryta=1") as conn:
        _wait(conn, "getComputedStyle(document.getElementById('bees')).display !== 'none'",
              "пасека не открылась")
        _wait(conn, "(function(){var b=document.getElementById('si-helper');"
                    "return b && getComputedStyle(b).display === 'none'})()",
              "за открытой пасекой гномы остались на экране")
        state = conn.evaluate("window.SI_HELPER && window.SI_HELPER.sostoyanie")
        assert "пасека" in state, f"самоотчёт не назвал причину: {state}"
        conn.evaluate("bees.close(); true")
        _wait(conn, "getComputedStyle(document.getElementById('si-helper')).display !== 'none'",
              "после закрытия пасеки гномы не вернулись")


def test_bees_keeps_a_full_hive_home_when_the_honey_cannot_be_bottled(server):
    """Разлить некуда — остальных всё равно отправляем, а улей с полными
    сотами остаётся дома со словами: игра такой вылет не примет."""
    with stand(server, "?paseka_banki_polny=1") as conn:
        _wait(conn, "document.querySelectorAll('#si-helper [data-si-button=bees]').length>0",
              "гнома пасеки нет")
        _press(conn, "bees")
        итог = _zhdat_paseku(conn)
        p = _paseka(conn)
        assert p["danger"] == [], p["danger"]
        assert [z for z in p["zaprosy"] if z.startswith("bees_startflight")] == [
            "bees_startflight id=1 tour=2", "bees_startflight id=4 tour=2"], p["zaprosy"]
        assert "отправлено 2" in итог, итог
        assert "мёд не разлит: Игра ответила: Ошибка Банки полны" in итог, итог
        assert "улей 5: соты полны, мёд некуда складывать" in итог, итог
        assert not p["otkryta"], "пасека осталась открытой"


def test_bees_skips_a_silent_hive_and_finishes_the_rest(server):
    """Один улей без ответа сервера не останавливает пасеку: остальные летят,
    молчавший пробуем ещё раз в конце. Живая игра 2026-09-19: на 13-м улье из
    20 ответ не пришёл за 15 с, помощник бросил всех — игроку пришлось жать снова."""
    with stand(server, "?paseka_molchit=4") as conn:
        _wait(conn, "document.querySelectorAll('#si-helper [data-si-button=bees]').length>0",
              "гнома пасеки нет")
        _press(conn, "bees")
        итог = _zhdat_paseku(conn, timeout=90)
        p = _paseka(conn)
        assert p["danger"] == [], p["danger"]
        starts = [z for z in p["zaprosy"] if z.startswith("bees_startflight")]
        assert starts == ["bees_startflight id=1 tour=2", "bees_startflight id=4 tour=2",
                          "bees_startflight id=5 tour=2", "bees_startflight id=4 tour=2"], starts
        assert "отправлено 3" in итог, итог
        assert "не закончена" not in итог and "не ответила" not in итог, итог
        assert sorted(p["letyat"]) == [1, 2, 4, 5], p["letyat"]


def test_bees_ignores_the_games_own_popup_while_waiting(server):
    """Окно игры не по нашему поводу (достижение) посреди работы — не отказ:
    данные приходят следом, и пасека идёт дальше."""
    with stand(server, "?paseka_okno=1") as conn:
        _wait(conn, "document.querySelectorAll('#si-helper [data-si-button=bees]').length>0",
              "гнома пасеки нет")
        _press(conn, "bees")
        итог = _zhdat_paseku(conn)
        p = _paseka(conn)
        assert p["danger"] == [], p["danger"]
        assert "отправлено 3" in итог, итог
        assert "Игра ответила" not in итог and "Достижение" not in итог, итог
