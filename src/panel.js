// ==UserScript==
// @name         Садовый помощник
// @namespace    si-helper
// @version      2026.11
// @description  Кнопки-помощники внутри игры. Действует только по нажатию.
// @match        https://*.molehillempire.com/*
// @match        https://*.sadowajaimperija.ru/*
// @match        https://*.wurzelimperium.de/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

/*
 * КАК ЭТО РАБОТАЕТ
 *   Полив выполняется функцией самой игры — `gardenjs.action.cache(n)`. Это
 *   ровно то, что происходит при клике мышью по клетке: игра сама проверяет,
 *   можно ли полить, сама копит очередь и сама отправляет её пачками по шесть.
 *   Мы не переписываем её правила и ничего не ускоряем сверх обычного — просто
 *   избавляем человека от двух сотен движений рукой.
 *
 * ЧЕГО ЗДЕСЬ НЕТ И НЕ БУДЕТ
 *   Ни одного действия само по себе: всё только по нажатию.
 *   Не вызывается `gardenjs.waterAll()` — это серверный `gardenWaterAll`,
 *   почти наверняка платная возможность игры. Разбор — в docs/game-api.md.
 *   Пароль не читается и никуда не передаётся.
 */

(function () {
	'use strict';

	// Узлы, на которых живёт игра (у неё много языковых версий).
	var GAME_HOSTS = [
		'molehillempire.com', 'sadowajaimperija.ru', 'wurzelimperium.de',
		'zieloneimperium.pl', 'kertbirodalom.hu', 'zeleneimperium.cz',
		'bahcivanlardiyari.com', 'molehillempire.es', 'molehillempire.nl',
		'molehillempire.ro'
	];

	function onGameHost() {
		var h = String(location.hostname || '').toLowerCase();
		for (var i = 0; i < GAME_HOSTS.length; i++) {
			if (h === GAME_HOSTS[i] || h.slice(-(GAME_HOSTS[i].length + 1)) === '.' + GAME_HOSTS[i]) {
				return true;
			}
		}
		return false;
	}

	function looksLikeGame() {
		// Мало правильного адреса: на странице входа игры тоже нет. Ждём, что
		// игра выложит свои объекты, иначе не показываемся вовсе.
		return onGameHost() && typeof window.gardenjs !== 'undefined';
	}

	// Скрипт доставляется программой на document-start, когда DOM ещё пуст.
	if (document.readyState === 'complete') { boot(); }
	else { window.addEventListener('load', function () { boot(); }, { once: true }); }

	/*
	 * ИГРА МОЖЕТ ОПОЗДАТЬ К `load`. Её объекты появляются из своих скриптов,
	 * и если они не успели к моменту нашей проверки, прежний код уходил
	 * НАВСЕГДА — молча, без второй попытки. Поэтому на узле игры ждём до
	 * тридцати секунд. Не на узле игры — уходим сразу: граница важнее.
	 */
	var BOOT_RETRIES = 30;
	var bootTries = 0;

	function bootLater() {
		if (bootTries >= BOOT_RETRIES) {
			try { window.SI_HELPER = { sostoyanie: 'игра так и не появилась на странице' }; } catch (e) {}
			return;
		}
		bootTries++;
		setTimeout(boot, 1000);
	}

	function boot() {
		if (window.top !== window.self) return;          // только верхнее окно
		if (document.getElementById('si-helper')) return; // уже нарисованы

		// Программа вставляет код и в пустую страницу `about:blank`, которую
		// браузер показывает до перехода на игру. Рисовать там кнопки незачем,
		// а вреда достаточно: проверка сборки находила их на пустой странице и
		// спрашивала у неё про игру. Поймано 2026-09-09.
		if (location.protocol === 'about:' || location.href === 'about:blank') return;

		/*
		 * ГРАНИЦА: работаем ТОЛЬКО на страницах самой игры.
		 *
		 * Строка `@match` в шапке действует лишь для Tampermonkey. Программа
		 * доставляет код через отладочный протокол браузера, а он адреса НЕ
		 * фильтрует — скрипт выполняется на каждой открытой странице, включая
		 * вход через upjers, почту и что угодно ещё. Это и небезопасно, и
		 * попросту не наше дело.
		 *
		 * Поэтому проверяем дважды: сначала имя узла, потом наличие самой игры
		 * на странице. Нет игры — молча уходим, ничего не рисуя.
		 */
		if (!looksLikeGame()) {
			if (onGameHost()) {
				try { window.SI_HELPER = { sostoyanie: 'жду игру (объектов игры на странице ещё нет)' }; } catch (e) {}
				bootLater();
			}
			return;
		}

		var CELLS = 204;   // сад 17 × 12
		// Пауза между действиями. Игра сама шлёт пачками по шесть, но подавать
		// ей клетки в тугом цикле — значит выдать за миллисекунды то, на что у
		// человека уходят секунды. Задержка ставится только после ДЕЙСТВИТЕЛЬНО
		// принятой клетки: отказы серверу ничего не стоят и ждать их незачем.
		var PACE_MS = 140;

		// ── работа ───────────────────────────────────────────────────
		function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

		function setMode(m) {
			// selectMode правит ещё и подписи в интерфейсе игры, поэтому зовём
			// её — чтобы во время полива игра не показывала чужой режим.
			try {
				if (typeof window.selectMode === 'function') window.selectMode(m, false, null, true);
			} catch (e) { /* интерфейс мог измениться — режим всё равно поставим ниже */ }
			window.mode = m;
		}

		function cellsOf(garden, n) {
			// Клетки, занятые растением: у тыквы 2×2 их четыре, и полить её надо
			// один раз, а не четыре. Считает функция игры.
			try {
				var fd = garden.getFieldData(n);
				if (typeof window.berechneFelder === 'function') {
					var list = window.berechneFelder(n, fd.x, fd.y, fd.sx, fd.sy);
					if (Object.prototype.toString.call(list) === '[object Array]') {
						var out = [];
						for (var i = 0; i < list.length; i++) if (list[i]) out.push(list[i]);
						if (out.length) return out;
					}
				}
			} catch (e) { /* не смогли — считаем клетку одиночной */ }
			return [n];
		}

		// ── кого вообще можно трогать ────────────────────────────────
		// Растения, которые игра пропускает в режиме полива.
		var SKIP_PIDS = [238, 253, 263, 285, 459];

		function nowSec() {
			// Время сервера, а не браузера: по нему игра считает сутки полива.
			try {
				if (window.Zeit && typeof window.Zeit.Client === 'number') {
					return window.Zeit.Client - (window.Zeit.Verschiebung || 0);
				}
			} catch (e) { /* нет Zeit — обойдёмся часами браузера */ }
			return Math.floor(Date.now() / 1000);
		}

		/*
		 * ПОЧЕМУ ЗДЕСЬ СВОЯ ПРОВЕРКА, ХОТЯ РАНЬШЕ Я ПИСАЛ ОБРАТНОЕ
		 *
		 * У игровой `cache()` есть ветки, срабатывающие ДО проверки режима:
		 *   category 'u'          → открывает ПЛАТНЫЙ диалог сноса постройки;
		 *   category 'h' + weed   → молча полет сорняк;
		 *   speedup_reduction     → предлагает ускорение за деньги.
		 * То есть слепой обход всех 204 клеток запускает действия, к поливу
		 * не относящиеся вовсе. Именно это случилось на живом аккаунте
		 * 2026-09-09: помощник открыл «В самом деле убрать? Цена: 2,50 сТ».
		 *
		 * Поэтому до вызова `cache()` мы сами отсеиваем всё, что не является
		 * обычным овощем под полив. Если наша проверка окажется строже
		 * игровой — польём меньше; если мягче — `cache()` откажет сама, и на
		 * овощах это безвредно. Ошибиться в опасную сторону она не даёт.
		 */
		function canWater(grid, n, now) {
			var c = grid && grid[n];
			if (!c || !c.pid) return false;
			if (c.category !== 'v') return false;      // отсекает 'u', 'h', 'w', 'wd', 'z'
			if (SKIP_PIDS.indexOf(c.pid) !== -1) return false;
			var prod = window.data_products && window.data_products[c.pid];
			if (prod && prod.speedup_reduction) return false;
			if (c.finished <= now) return false;       // уже созрело
			if (c.water >= now - 86400) return false;  // полито меньше суток назад
			return true;
		}

		/*
		 * ═══ ВОДНЫЙ САД — НЕ ЕЩЁ ОДИН САД, А ДРУГОЙ ОБЪЕКТ ═══════════
		 *
		 * Разобрано по исходнику игры (`WatergardenClass` в `wurzel_all.js`):
		 * у него СВОЯ сетка `watergarden.grid`, СВОЯ очередь
		 * (`do=watergardenCache`) и свои правила — клетка бывает `water`,
		 * `edge` или `blocked`, растения лежат категорией 'w', украшения —
		 * 'wd', сорняк — 'u'.
		 *
		 * `gardenjs` при этом никуда не девается: он продолжает держать
		 * ПРЕДЫДУЩИЙ обычный сад, которого игрок сейчас не видит. Значит,
		 * всякое действие через него в водном саду — действие вслепую по
		 * чужому саду. До 2026-09-09 помощник делал ровно это: игрок жал
		 * жука в водном саду, а клетки уходили в обычный.
		 *
		 * Клетка водного сада — МАССИВ, а не объект:
		 *   [0] pid, [1] x, [2] y — для `berechneFelder`,
		 *   [3] возраст созревания, [4] возраст последнего полива,
		 *   [10] + [11] — нынешний возраст растения.
		 */
		var WATER_GARDEN_ID = 101;   // его номер в `window.currentGarden`

		function waterGarden() { return window.watergarden || null; }

		function inWaterGarden() {
			var w = waterGarden();
			if (!w) return false;
			if (w.isOpen === true) return true;
			return parseInt(window.currentGarden, 10) === WATER_GARDEN_ID;
		}

		function wgAge(c) {
			var w = waterGarden();
			var sync = (w && w.timeSinceLastSync) || 0;
			return (c[10] || 0) + (c[11] || 0) + sync;
		}

		// Клетки, занятые одним растением: у лотоса 2×1 их две, и полить его
		// надо один раз. Считает та же `berechneFelder`, что и в обычном саду.
		function wgCells(w, n) {
			var c = w.grid[n];
			if (!c || !c[0]) return [n];
			var d = window.data_products && window.data_products[c[0]];
			try {
				var list = window.berechneFelder(n, c[1], c[2], (d && d.sx) || 1, (d && d.sy) || 1);
				if (Object.prototype.toString.call(list) === '[object Array]') {
					var out = [];
					for (var i = 0; i < list.length; i++) if (list[i]) out.push(list[i]);
					if (out.length) return out;
				}
			} catch (e) { /* не смогли — считаем клетку одиночной */ }
			return [n];
		}

		/*
		 * Кого поливаем. Условия — из её же `gridAction`, режим 2:
		 * сутки с прошлого полива и растение ещё не созрело. Плюс наше
		 * собственное правило: трогаем ТОЛЬКО водные растения. Сорняк ('u')
		 * в водном саду тоже открывает платный диалог прополки, а украшение
		 * поливать нечего — как и в обычном саду, ошибиться тут дороже, чем
		 * недополить.
		 */
		function wgCanWater(w, n) {
			var c = w.grid[n];
			if (!c || !c[0]) return false;
			var d = window.data_products && window.data_products[c[0]];
			if (!d || d.category !== 'w') return false;
			var age = wgAge(c);
			if (c[3] && age >= c[3]) return false;          // созрело
			if (c[4] && age - c[4] < 86400) return false;   // сутки ещё не вышли
			return true;
		}

		/*
		 * Куда сажаем. Правила её `gridAction`, случай 0:
		 *   • клетка свободна и существует в разметке;
		 *   • растение с `edge` идёт на берег, обычное — на воду;
		 *   • растение не переносится на следующий ряд.
		 */
		function wgCanPlant(w, pid, n) {
			var d = window.data_products && window.data_products[pid];
			if (!d) return false;
			var cells;
			try { cells = w.getFields(n, d.sx || 1, d.sy || 1); } catch (e) { return false; }
			if (!cells || !cells.length) return false;

			// Перенос строки: у игры это `n[0] % 17 == 0` → соседние клетки
			// справа и справа-снизу считаются несуществующими.
			if (cells.length > 1 && n % 17 === 0) {
				for (var j = 0; j < cells.length; j++) {
					if (cells[j] === n + 1 || cells[j] === n + 18) return false;
				}
			}

			for (var k = 0; k < cells.length; k++) {
				var cell = cells[k];
				if (cell > CELLS) return false;
				if (!document.getElementById('wgGrid' + cell)) return false;
				var kind = w.gridDefinition && w.gridDefinition[cell];
				if (d.edge ? kind !== 'edge' : kind !== 'water') return false;
				var c = w.grid[cell];
				if (c && c[0]) return false;                 // занято
			}
			return true;
		}

		/*
		 * СБОР В ВОДНОМ САДУ — правило из её `watergarden.harvest(n)`:
		 *   age = c[10] + c[11] + timeSinceLastSync
		 *   age < c[3]  → окно «действительно собрать?» (не созрело)
		 *   иначе       → cache(pid, n, 'harvest')
		 * Значит созревшим считается `age >= c[3]`.
		 *
		 * Категорию проверяем сами и строго: в водном саду 'u' — сорняк, а
		 * его уборка платная (`watergardenRemoveWeedCosts`), 'wd' —
		 * украшение, собирать там нечего.
		 */
		function wgCanHarvest(w, n) {
			var c = w.grid[n];
			if (!c || !c[0]) return false;
			var d = window.data_products && window.data_products[c[0]];
			if (!d || d.category !== 'w') return false;
			if (!c[3]) return false;
			return wgAge(c) >= c[3];
		}

		function wgRipeCells() {
			var w = waterGarden();
			if (!w || !w.grid) return 0;
			var n = 0;
			for (var i = 1; i <= CELLS; i++) if (wgCanHarvest(w, i)) n++;
			return n;
		}

		/*
		 * Есть ли у водного сада своя СВОБОДНАЯ кнопка «собрать всё».
		 *
		 * Судим по разметке, а не по наличию метода: метод есть всегда, а
		 * право — нет. Замер живой игры 2026-09-14: кнопка
		 * `watergarden.harvestAll()` стоит в его панели с классом `link`,
		 * без `off` и без замка, то есть игроку доступна.
		 */
		function wgHarvestAllReady() {
			try {
				var el = document.querySelectorAll('#watergarden_container [class*=link]');
				for (var i = 0; i < el.length; i++) {
					var oc = el[i].getAttribute('onclick') || '';
					if (oc.indexOf('harvestAll') === -1) continue;
					if (/(^|\s)off(\s|$)/.test(el[i].className || '')) return false;
					if (el[i].querySelector && el[i].querySelector('.locked')) return false;
					return true;
				}
			} catch (e) { /* разметка могла смениться — считаем, что кнопки нет */ }
			return false;
		}

		function harvestAllWater() {
			var w = waterGarden();
			var problem = wgReady(w);
			if (problem) return Promise.reject(new Error(problem));
			if (typeof w.harvest !== 'function') {
				return Promise.reject(new Error('Водный сад не даёт собирать.'));
			}

			var done = {}, harvested = 0, ripe = wgRipeCells();

			var i = 1;
			function step() {
				for (; i <= CELLS; i++) {
					if (done[i]) continue;
					done[i] = true;
					var c = w.grid[i];
					if (!c || !c[0]) continue;

					var cells = wgCells(w, i), k, suitable = true;
					for (k = 0; k < cells.length; k++) {
						if (!wgCanHarvest(w, cells[k])) { suitable = false; break; }
					}
					for (k = 0; k < cells.length; k++) done[cells[k]] = true;
					if (!suitable) continue;

					// Собираем по ЛЕВОЙ ВЕРХНЕЙ клетке растения: её `harvest`
					// сама разложит действие на всю площадь.
					try { w.harvest(cells[0]); } catch (e) { continue; }
					harvested++;
					notify.wait('Собираю… ' + harvested);
					i++;
					return sleep(PACE_MS).then(step);
				}
				return Promise.resolve();
			}

			function flush() { try { w.sendCache(); } catch (e) { /* очередь уйдёт сама */ } }

			return step().then(function () {
				flush();
				return { harvested: harvested, ripe: ripe };
			}, function (err) { flush(); throw err; });
		}

		function wgReady(w) {
			if (!w || !w.grid || typeof w.water !== 'function' || typeof w.getFields !== 'function') {
				return 'Водный сад ещё не загрузился. Подождите и попробуйте снова.';
			}
			return null;
		}

		function waterAllWater() {
			var w = waterGarden();
			var problem = wgReady(w);
			if (problem) return Promise.reject(new Error(problem));

			var done = {}, watered = 0, plants = 0, v;
			for (v = 1; v <= CELLS; v++) {
				var cv = w.grid[v];
				if (!cv || !cv[0]) continue;
				var dv = window.data_products && window.data_products[cv[0]];
				if (dv && dv.category === 'w') plants++;
			}

			var i = 1;
			function step() {
				for (; i <= CELLS; i++) {
					if (done[i]) continue;
					done[i] = true;
					var c = w.grid[i];
					if (!c || !c[0]) continue;

					var cells = wgCells(w, i), k, suitable = true;
					for (k = 0; k < cells.length; k++) {
						if (!wgCanWater(w, cells[k])) { suitable = false; break; }
					}
					for (k = 0; k < cells.length; k++) done[cells[k]] = true;
					if (!suitable) continue;

					// Игра поливает по ЛЕВОЙ ВЕРХНЕЙ клетке растения: её же
					// `water()` сама разложит действие на всю площадь.
					try { w.water(cells[0]); } catch (e) { continue; }
					watered++;
					notify.wait('Поливаю… ' + watered);
					i++;
					return sleep(PACE_MS).then(step);
				}
				return Promise.resolve();
			}

			function flush() { try { w.sendCache(); } catch (e) { /* очередь уйдёт сама */ } }

			return step().then(function () {
				flush();
				return { watered: watered, vegetables: plants };
			}, function (err) { flush(); throw err; });
		}

		function plantAllWater(pid) {
			var w = waterGarden(), shelf = window.regal;
			var problem = wgReady(w);
			if (problem) return Promise.reject(new Error(problem));
			if (typeof w.plant !== 'function') return Promise.reject(new Error(problem || 'Водный сад ещё не загрузился.'));
			if (!shelf || typeof shelf.getCount !== 'function') {
				return Promise.reject(new Error('Не вижу полку с семенами.'));
			}

			/*
			 * СКОЛЬКО МОЖНО ПОСАДИТЬ — СЧИТАЕМ САМИ.
			 *
			 * В обычном саду игра уменьшает счётчик полки сразу, и цикл может
			 * на него смотреть. В водном счётчик приходит ОТВЕТОМ сервера
			 * (`incomingAjax` → `regal.setCount`), то есть во время работы он
			 * не меняется вовсе. Поэтому запас берём один раз и считаем
			 * посаженное — иначе ушли бы сажать больше, чем есть семян.
			 */
			var budget = 0;
			try { budget = parseInt(shelf.getCount(pid), 10) || 0; } catch (e) { budget = 0; }
			if (budget < 1) return Promise.reject(new Error('Этих семян не осталось.'));

			var planted = 0, ranOut = false;
			var i = 1;
			function step() {
				for (; i <= CELLS; i++) {
					if (planted >= budget) { ranOut = true; break; }
					if (!wgCanPlant(w, pid, i)) continue;

					try { w.plant(pid, i); } catch (e) { continue; }
					// Приняла ли игра посадку, видно по её же сетке: `cache()`
					// помечает клетку занятой сразу, не дожидаясь сервера.
					var c = w.grid[i];
					if (!c || String(c[0]) !== String(pid)) continue;

					planted++;
					notify.wait('Сажаю… ' + planted);
					i++;
					return sleep(PACE_MS).then(step);
				}
				return Promise.resolve();
			}

			function flush() { try { w.sendCache(); } catch (e) { /* очередь уйдёт сама */ } }

			return step().then(function () {
				flush();
				return { planted: planted, ranOut: ranOut };
			}, function (err) { flush(); throw err; });
		}

		function checkReady() {
			var g = window.gardenjs;
			if (!g || !g.action || typeof g.action.cache !== 'function') {
				return 'Не вижу сад. Откройте свой сад в игре и попробуйте снова.';
			}
			if (typeof g.getGrid !== 'function' || !g.getGrid()) {
				// Без состояния сада мы не можем отсеять опасные клетки —
				// значит не работаем вовсе. Лучше отказаться, чем навредить.
				return 'Сад ещё не загрузился. Подождите и попробуйте снова.';
			}
			var ez = document.getElementById('einkaufszettel');
			if (ez && ez.style.display === 'block') {
				return 'Закройте список покупок — с ним игра не даёт поливать.';
			}
			return null;
		}

		/*
		 * ═══ СБОР УРОЖАЯ ═══════════════════════════════════════════
		 *
		 * ПРАВИЛО — из её же `cache()`, ветка `case 1` (разобрано по
		 * исходнику 2026-09-14):
		 *   • на клетке что-то растёт (`pid != 0`) — и на КАЖДОЙ клетке
		 *     растения, иначе она отказывает;
		 *   • это обычный овощ. Категорию проверяем сами и строго: 'u'
		 *     открывает ПЛАТНЫЙ диалог сноса, 'h' с сорняком молча полет —
		 *     обе ветки срабатывают ДО проверки режима, и ровно на них
		 *     2026-09-09 напоролся живой аккаунт;
		 *   • растение созрело. Игра судит об этом по картинке клетки:
		 *     `elements[n].b.alt == 0` значит «ещё не выросло», и она
		 *     спрашивает окном «действительно собрать?» — недозревший сбор
		 *     теряет урожай. Спрашиваем то же самое, чтобы окно не
		 *     выскочило, и вдобавок смотрим `finished`.
		 *
		 * `speedup_reduction` сбору НЕ мешает: у неё эта ветка стоит под
		 * условием `K != 1`, то есть на сбор не распространяется.
		 * Замер живой игры 2026-09-14: у созревших `alt` равен строке "6".
		 */
		function canHarvest(garden, grid, n, now) {
			var c = grid && grid[n];
			if (!c || !c.pid) return false;
			if (c.category !== 'v') return false;
			if (!c.finished || c.finished > now) return false;
			try {
				var el = garden.getElements && garden.getElements()[n];
				// Сравнение нестрогое — ровно как у игры: `alt` это строка.
				if (el && el.b && el.b.alt == 0) return false;
			} catch (e) { /* разметка могла смениться — хватит `finished` */ }
			return true;
		}

		// Сколько созревших клеток в текущем саду.
		function ripeCells() {
			try {
				var garden = window.gardenjs, grid = garden.getGrid();
				var now = nowSec(), n = 0;
				for (var i = 1; i <= CELLS; i++) if (canHarvest(garden, grid, i, now)) n++;
				return n;
			} catch (e) { return 0; }
		}

		/*
		 * НАНЯТ ЛИ ЖНЕЦ САМОЙ ИГРЫ.
		 *
		 * В её полосе помощников стоит `.harvest` с
		 * `onclick="gardenjs.harvestAll()"`. Ненанятый помощник помечен
		 * классом `off` и ведёт не в сбор, а на страницу покупки — так у
		 * игрока с поливальщиком (`link water off` плюс накладка `.locked`).
		 * Замер живой игры 2026-09-14: жнец нанят, поливальщик нет.
		 *
		 * Пока жнец нанят, СВОЕЙ кнопки сбора не рисуем вовсе: иначе в
		 * полосе окажется два жнеца — ровно то, за что игрок справедливо
		 * отчитал нас за двух жуков.
		 */
		function gameHarvestHired() {
			try {
				var e = document.querySelector('#wimpareaHelper .harvest');
				if (!e) return false;
				return !/(^|\s)off(\s|$)/.test(e.className || '');
			} catch (e) { return false; }
		}

		function harvestAll() {
			var problem = checkReady();
			if (problem) return Promise.reject(new Error(problem));

			var garden = window.gardenjs;
			var prevMode = (typeof window.mode === 'undefined') ? -1 : window.mode;
			var done = {}, harvested = 0;
			var grid = garden.getGrid();
			var now = nowSec();
			var ripe = ripeCells();

			setMode(1);

			var i = 1;
			function step() {
				for (; i <= CELLS; i++) {
					if (done[i]) continue;

					var cells = cellsOf(garden, i), k, suitable = true;
					// Растение целиком: у тыквы 2×2 собираем один раз, а не
					// четыре, и только если созрела вся площадь.
					for (k = 0; k < cells.length; k++) {
						if (!canHarvest(garden, grid, cells[k], now)) { suitable = false; break; }
					}
					for (k = 0; k < cells.length; k++) done[cells[k]] = true;
					done[i] = true;
					if (!suitable) continue;

					var accepted;
					try { accepted = garden.action.cache(i) !== false; } catch (e) { accepted = false; }
					if (accepted) {
						harvested++;
						notify.wait('Собираю… ' + harvested);
						i++;
						return sleep(PACE_MS).then(step);
					}
				}
				return Promise.resolve();
			}

			function finish() {
				try { garden.action.cacheFlush(); } catch (e) { /* очередь уйдёт сама */ }
				setMode(prevMode);
			}

			return step().then(function () {
				finish();
				return { harvested: harvested, ripe: ripe };
			}, function (err) { finish(); throw err; });
		}

		function waterAll() {
			var problem = checkReady();
			if (problem) return Promise.reject(new Error(problem));

			var garden = window.gardenjs;
			var prevMode = (typeof window.mode === 'undefined') ? -1 : window.mode;
			var done = {}, watered = 0;
			var grid = garden.getGrid();
			var now = nowSec();
			var vegetables = 0;   // сколько овощей в саду вообще
			for (var v = 1; v <= CELLS; v++) {
				if (grid[v] && grid[v].pid && grid[v].category === 'v') vegetables++;
			}

			setMode(2);

			var i = 1;
			function step() {
				for (; i <= CELLS; i++) {
					if (done[i]) continue;

					var cells = cellsOf(garden, i);
					var k;

					// Растение целиком: у тыквы 2×2 проверяем все четыре клетки,
					// как это делает сама игра.
					var suitable = true;
					for (k = 0; k < cells.length; k++) {
						if (!canWater(grid, cells[k], now)) { suitable = false; break; }
					}
					for (k = 0; k < cells.length; k++) done[cells[k]] = true;
					done[i] = true;

					// Не наше — не трогаем ВООБЩЕ. Один вызов `cache()` по
					// постройке или сорняку уже был бы действием.
					if (!suitable) continue;

					var accepted;
					try {
						accepted = garden.action.cache(i) !== false;
					} catch (e) {
						accepted = false;
					}

					if (accepted) {
						watered++;
						notify.wait('Поливаю… ' + watered);
						i++;
						return sleep(PACE_MS).then(step);
					}
				}
				return Promise.resolve();
			}

			return step().then(function () {
				try { garden.action.cacheFlush(); } catch (e) { /* очередь могла уйти сама */ }
				setMode(prevMode);
				return { watered: watered, vegetables: vegetables };
			}, function (err) {
				try { garden.action.cacheFlush(); } catch (e) { }
				setMode(prevMode);
				throw err;
			});
		}

		// ── посадка ──────────────────────────────────────────────────
		/*
		 * Тот же принцип, что и с поливом: сажает игра, своей `cache()`.
		 * Заряды платного «автомата посадки» при этом не тратятся — мы им не
		 * пользуемся, как не пользовался им и CupIvan в 2012-м.
		 *
		 * Опасных веток тут можно не бояться по построению: сажаем только в
		 * ПУСТЫЕ клетки, а у пустой клетки нет ни категории, ни сорняка.
		 */
		function plantAll(pid) {
			var problem = checkReady();
			if (problem) return Promise.reject(new Error(problem));

			var garden = window.gardenjs, shelf = window.regal;
			if (!shelf || typeof shelf.getCount !== 'function') {
				return Promise.reject(new Error('Не вижу полку с семенами.'));
			}
			if (shelf.getCount(pid) < 1) {
				return Promise.reject(new Error('Этих семян не осталось.'));
			}

			var info = {};
			try { info = shelf.getProductInfos(pid) || {}; } catch (e) { }
			var sx = info.sx || 1, sy = info.sy || 1;

			var grid = garden.getGrid();
			var prevMode = (typeof window.mode === 'undefined') ? -1 : window.mode;
			var prevSelected = window.selected;
			var done = {}, planted = 0, ranOut = false;

			window.selected = pid;
			setMode(0);

			var i = 1;
			function step() {
				for (; i <= CELLS; i++) {
					if (done[i]) continue;
					if (shelf.getCount(pid) < 1) { ranOut = true; break; }

					var cells;
					try { cells = window.berechneFelder(i, 1, 1, sx, sy); } catch (e) { cells = [i]; }

					// Площадь должна помещаться в сад и быть пустой целиком.
					var fits = !!(cells && cells.length) && cells.indexOf(false) === -1;
					if (fits) {
						for (var k = 0; k < cells.length; k++) {
							var c = grid[cells[k]];
							if (!c || c.pid !== 0) { fits = false; break; }
						}
					}
					// Не поместилось — помечаем ТОЛЬКО эту клетку: соседняя ещё
					// может оказаться левым верхним углом подходящей площадки.
					if (!fits) { done[i] = true; continue; }

					var accepted;
					try { accepted = garden.action.cache(i) !== false; } catch (e) { accepted = false; }
					if (!accepted) { done[i] = true; continue; }

					for (k = 0; k < cells.length; k++) done[cells[k]] = true;
					planted++;
					notify.wait('Сажаю… ' + planted);
					i++;
					return sleep(PACE_MS).then(step);
				}
				return Promise.resolve();
			}

			function restore() {
				try { garden.action.cacheFlush(); } catch (e) { }
				setMode(prevMode);
				window.selected = prevSelected;
			}

			return step().then(function () {
				restore();
				return { planted: planted, ranOut: ranOut };
			}, function (err) { restore(); throw err; });
		}

		// ═══════════════════════════════════════════════════════════
		// ОФОРМЛЕНИЕ — по системе CupIvan: своего окна нет
		//
		// Разбор его кода (reference/cupivan-5.2) показал цельный подход:
		//   • никакой собственной панели — только места и стили игры;
		//   • сообщения не висят на экране, а всплывают шариком и гаснут;
		//   • состояние сада показывается раскраской самих клеток, а не
		//     текстом: синий — полить, зелёный — созрело, чёрный — пусто.
		// Повторяем систему, но своих персонажей не рисуем: прошлый
		// нарисованный гном проиграл игровой графике и был убран.
		// ═══════════════════════════════════════════════════════════

		var COLORS = {
			water: '#2a6fd4',   // синий — надо полить
			ripe: '#22b14c',    // зелёный — созрело
			empty: '#000000'    // чёрный — пусто
		};

		// ── шарики вместо строки состояния ───────────────────────────
		var notify = (function () {
			var box = null, hideTimer = 0;

			function ensure() {
				if (box) return box;
				box = document.createElement('div');
				box.id = 'si-helper-notify';
				box.setAttribute('style', [
					'position:fixed', 'z-index:2147483646', 'top:12px', 'left:50%',
					'transform:translateX(-50%)', 'padding:7px 16px', 'border-radius:6px',
					'border:2px solid rgba(0,0,0,.55)', 'box-shadow:0 3px 12px rgba(0,0,0,.45)',
					'font:bold 13px/1.3 system-ui,Segoe UI,sans-serif', 'color:#1a1a1a',
					'cursor:pointer', 'display:none', 'max-width:70vw', 'text-align:center'
				].join(';'));
				box.onclick = hide;
				(document.body || document.documentElement).appendChild(box);
				return box;
			}

			function show(text, colour, ms) {
				var b = ensure();
				b.textContent = text;
				b.style.background = colour;
				b.style.display = 'block';
				mirror(text);
				clearTimeout(hideTimer);
				if (ms) hideTimer = setTimeout(hide, ms);
			}

			function hide() { if (box) box.style.display = 'none'; }

			return {
				wait: function (t) { show(t, '#ffe680', 0); },      // жёлтый, висит до итога
				info: function (t) { show(t, '#a6f09a', 3000); },   // зелёный
				error: function (t) { show(t, '#ff9d9d', 6000); },  // красный, дольше
				hide: hide
			};
		})();

		// Невидимая строка с последним сообщением. Её читают программы чтения
		// с экрана, и по ней же проверяют работу автоматические проверки:
		// шарик гаснет, а знать, что он сказал, надо.
		function mirror(text) {
			var m = document.getElementById('si-helper-status');
			if (!m) {
				m = document.createElement('div');
				m.id = 'si-helper-status';
				m.setAttribute('aria-live', 'polite');
				m.setAttribute('style', 'position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap');
				(document.body || document.documentElement).appendChild(m);
			}
			m.textContent = text;
		}

		// ── раскраска клеток ─────────────────────────────────────────
		/*
		 * Приём Ивана один в один: полупрозрачная заливка поверх игровых
		 * клеток. Клетки теперь зовутся `#gardenTile<N>` вместо `#b<N>`.
		 * Включается кнопкой и гасится ею же — решение владельца: постоянные
		 * цветные пятна мешают смотреть на сад.
		 *
		 * Заливка идёт внутренней тенью (`inset box-shadow`): она не трогает
		 * ни фон клетки, ни её размеры, поэтому сдвинуть вёрстку игры не может.
		 */
		var painted = false;

		function paint(on) {
			if (inWaterGarden()) return paintWater(on);
			var garden = window.gardenjs;
			if (!garden || typeof garden.getGrid !== 'function') {
				notify.error('Не вижу сад.');
				return false;
			}
			var grid = garden.getGrid(), now = nowSec(), n = 0;

			for (var i = 1; i <= CELLS; i++) {
				var el = document.getElementById('gardenTile' + i);
				if (!el) continue;

				if (!on) {
					el.style.boxShadow = '';
					el.style.outline = '';
					continue;
				}

				var c = grid[i], colour = '';
				if (!c || !c.pid) colour = COLORS.empty;
				else if (c.category === 'z' || c.category === 'u') colour = '';   // декор и постройки не трогаем
				else if (c.finished && c.finished <= now) colour = COLORS.ripe;
				else if (canWater(grid, i, now)) colour = COLORS.water;

				// «Пусто» заливаем заметно слабее: пустых клеток бывает почти
				// весь сад, и на полной силе он превращается в чёрное поле.
				// У Ивана этого не было видно — его сад был засажен.
				var strength = (colour === COLORS.empty) ? 0.16 : 0.32;
				el.style.boxShadow = colour ? ('inset 0 0 0 999px ' + hexA(colour, strength)) : '';
				el.style.outline = colour ? ('1px solid ' + hexA(colour, strength + 0.2)) : '';
				if (colour) n++;
			}
			painted = on;
			return n;
		}

		/*
		 * Подсветка водного сада. Клетки там зовутся `#wgGrid<N>`, состояние
		 * лежит массивами. Закрытые клетки и берег пустыми не считаем: сажать
		 * туда нельзя, и чёрная заливка врала бы про свободное место.
		 */
		function paintWater(on) {
			var w = waterGarden();
			if (!w || !w.grid) { notify.error('Не вижу водный сад.'); return false; }
			var n = 0;

			for (var i = 1; i <= CELLS; i++) {
				var el = document.getElementById('wgGrid' + i);
				if (!el) continue;

				if (!on) {
					el.style.boxShadow = '';
					el.style.outline = '';
					continue;
				}

				var kind = w.gridDefinition && w.gridDefinition[i];
				var c = w.grid[i], colour = '';
				var d = (c && c[0] && window.data_products) ? window.data_products[c[0]] : null;

				if (kind === 'blocked') colour = '';
				else if (!c || !c[0]) colour = (kind === 'water') ? COLORS.empty : '';
				else if (!d || d.category !== 'w') colour = '';          // сорняк и украшения не наши
				else if (c[3] && wgAge(c) >= c[3]) colour = COLORS.ripe;
				else if (wgCanWater(w, i)) colour = COLORS.water;

				var strength = (colour === COLORS.empty) ? 0.16 : 0.32;
				el.style.boxShadow = colour ? ('inset 0 0 0 999px ' + hexA(colour, strength)) : '';
				el.style.outline = colour ? ('1px solid ' + hexA(colour, strength + 0.2)) : '';
				if (colour) n++;
			}
			painted = on;
			return n;
		}

		function hexA(hex, a) {
			var r = parseInt(hex.slice(1, 3), 16),
				g = parseInt(hex.slice(3, 5), 16),
				b = parseInt(hex.slice(5, 7), 16);
			return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')';
		}

		// ── действия ─────────────────────────────────────────────────
		var running = false;

		/*
		 * Отпустить кнопки — обязательно, чем бы дело ни кончилось.
		 *
		 * Раньше стояло `.then(успех).then(уборка)`. Так уборка НЕ
		 * выполняется, если обработчик успеха бросит исключение: обещание
		 * становится отклонённым, а `.then` с одним доводом пропускает
		 * отклонение мимо. `running` оставался true навсегда, и помощник
		 * молча переставал отзываться на нажатия. Поймано 2026-09-10:
		 * гном рынка перестал открывать город со второго раза.
		 */
		function otpustit() { busy(false); }

		function busy(on) {
			running = on;
			var bs = bar.querySelectorAll('[data-si-button], button, select');
			for (var i = 0; i < bs.length; i++) {
				bs[i].disabled = on;
				bs[i].style.opacity = on ? '.7' : '1';
				bs[i].style.cursor = on ? 'default' : 'pointer';
			}
		}

		function plural(n, one, few, many) {
			var d = n % 10;
			if (n > 4 && n < 21) return many;
			if (d === 1) return one;
			if (d > 1 && d < 5) return few;
			return many;
		}

		function runWatering() {
			if (running) return;
			busy(true);
			notify.wait('Идёт полив…');
			(inWaterGarden() ? waterAllWater() : waterAll()).then(function (res) {
				if (res.watered) {
					notify.info('Полито ' + res.watered + ' ' + plural(res.watered, 'растение', 'растения', 'растений'));
				} else if (!res.vegetables) {
					notify.error('В саду нет растений — поливать нечего.');
				} else {
					notify.info('Все растения уже политы.');
				}
				if (painted) paint(true);
			}, function (err) {
				notify.error(err && err.message ? err.message : 'Не получилось.');
			}).then(otpustit, otpustit);   // отпускаем в ЛЮБОМ случае, см. `otpustit`
		}

		function runPlanting() {
			if (running) return;
			var pid = currentSeed();
			if (!pid) { notify.error(seedHint()); return; }
			busy(true);
			notify.wait('Идёт посадка…');
			(inWaterGarden() ? plantAllWater(pid) : plantAll(pid)).then(function (res) {
				if (!res.planted) {
					notify.error('Свободных клеток нет.');
				} else if (res.ranOut) {
					notify.info('Посажено ' + res.planted + ', семена кончились.');
				} else {
					notify.info('Посажено ' + res.planted + ' ' + plural(res.planted, 'растение', 'растения', 'растений'));
				}
				if (painted) paint(true);
			}, function (err) {
				notify.error(err && err.message ? err.message : 'Не получилось.');
			}).then(function () { otpustit(); refreshPlantButton(); },
			       function () { otpustit(); refreshPlantButton(); });
		}

		function runHarvesting() {
			if (running) return;
			busy(true);
			notify.wait('Идёт сбор…');
			// В водном саду СВОЙ объект игры и своя очередь: работать там
			// через `gardenjs` значило бы собирать вслепую в невидимом
			// обычном саду — ошибка, что чинилась в 3.4.0.
			(inWaterGarden() ? harvestAllWater() : harvestAll()).then(function (res) {
				if (res.harvested) {
					notify.info('Собрано ' + res.harvested + ' ' +
						plural(res.harvested, 'растение', 'растения', 'растений'));
				} else if (!res.ripe) {
					notify.info('Созревшего нет.');
				} else {
					notify.error('Собрать не вышло — игра не приняла.');
				}
				if (painted) paint(true);
			}, function (err) {
				notify.error(err && err.message ? err.message : 'Не получилось.');
			}).then(otpustit, otpustit);
		}

		/*
		 * ═══ ОБХОД ВСЕХ САДОВ ══════════════════════════════════════
		 *
		 * Зачем. Замер живой игры 2026-09-14: у игрока пять садов и в
		 * каждом по 204 созревших растения. По отдельности сбор, посадка и
		 * полив уже быстрые — медленно то, что весь круг надо повторить
		 * пять раз, переключаясь вручную. Это около двадцати действий.
		 *
		 * Порядок внутри сада — собрать, посадить, полить — не случаен:
		 * пока не собрано, клетки заняты и сажать некуда; пока не посажено,
		 * поливать нечего.
		 *
		 * Сбор идёт ЕЁ ЖНЕЦОМ, если он нанят: это её собственная кнопка,
		 * для игрока бесплатная, и один запрос вместо двух сотен. Не нанят
		 * — собираем сами, клетка за клеткой.
		 */
		function gardenReady(n) {
			try {
				return currentGarden() === n && !!(window.gardenjs && window.gardenjs.getGrid());
			} catch (e) { return false; }
		}

		function goToGarden(n) {
			if (currentGarden() === n) return Promise.resolve();
			if (!switchGarden(n)) {
				return Promise.reject(new Error('Не получилось перейти в сад ' + n + '.'));
			}
			// Переключение идёт ответом сервера (`do:changeGarden`), поэтому
			// ждём, а не считаем сделанным.
			return waitFor(function () { return gardenReady(n); }, 25000);
		}

		function harvestHere() {
			var before = ripeCells();
			if (!before) return Promise.resolve({ harvested: 0, ripe: 0 });
			if (!gameHarvestHired()) return harvestAll();

			try { window.gardenjs.harvestAll(); }
			catch (e) { return harvestAll(); }
			// Сад вернётся с сервера обновлённым — ждём, пока созревшее уйдёт.
			return waitFor(function () { return ripeCells() === 0; }, 20000).then(
				function () { return { harvested: before, ripe: before }; },
				function () { return { harvested: 0, ripe: before }; }
			);
		}

		/*
		 * Водный сад — последняя остановка обхода.
		 *
		 * Он у игры ОТДЕЛЬНЫЙ объект со своей очередью, поэтому и работа в
		 * нём своя: `harvestAllWater`, `plantAllWater`, `waterAllWater`.
		 * Семена там тоже свои (категория 'w'), и спрашиваем мы их уже
		 * ПОСЛЕ входа — иначе проверка сравнивала бы с овощами.
		 * Нет водного сада у игрока — молча пропускаем.
		 */
		function harvestHereWater() {
			var before = wgRipeCells();
			if (!before) return Promise.resolve({ harvested: 0 });
			if (!wgHarvestAllReady()) return harvestAllWater();
			try { waterGarden().harvestAll(); }
			catch (e) { return harvestAllWater(); }
			return waitFor(function () { return wgRipeCells() === 0; }, 20000).then(
				function () { return { harvested: before }; },
				function () { return { harvested: 0 }; }
			);
		}

		function roundWaterGarden(итог) {
			var w = waterGarden();
			if (!w || typeof w.open !== 'function' || !hasLocation('watergarden')) {
				return Promise.resolve();
			}
			try { w.open(); } catch (e) { return Promise.resolve(); }

			function закрыть() { try { w.close(); } catch (e) { /* уже закрыт */ } }

			return waitFor(function () { return inWaterGarden() && !!w.grid; }, 25000)
				.then(function () {
					notify.wait('Собираю (водный сад)');
					return harvestHereWater();
				})
				.then(function (r) {
					итог.собрано += (r && r.harvested) || 0;
					var pid = currentSeed();
					if (!pid) return null;
					notify.wait('Сажаю (водный сад)');
					return plantAllWater(pid).then(null, function () { return null; });
				})
				.then(function (r) {
					итог.посажено += (r && r.planted) || 0;
					notify.wait('Поливаю (водный сад)');
					return waterAllWater();
				})
				.then(function (r) {
					итог.полито += (r && r.watered) || 0;
					итог.водный = true;
					закрыть();
				}, function (err) { закрыть(); throw err; });
		}

		/*
		 * ═══ ПТИЧЬЯ ПОЧТА: ПТИЦЫ РАБОТАЮТ ПО ВСЕМ СКВОРЕЧНИКАМ ══════
		 *
		 * Разобрано по объекту `birds` из wurzel_all.js и по живой игре
		 * 2026-09-18 (см. docs/game-api.md, «Птичья почта»). Один заказ —
		 * одна птица: заказ ждёт в слоте, игрок выбирает скворечник, чья
		 * птица тянет груз и выносливость, продукты уходят с полки, птица
		 * летит, потом «Забрать вознаграждение» — и в слоте тут же новый
		 * заказ. Выносливость кормится ягодами с полки; вылеты кончаются,
		 * птица уходит «на пенсию», в пустой скворечник покупают новую.
		 * У игрока это десяток нажатий на каждый заказ, и заказы висят
		 * готовыми часами.
		 *
		 * Что делаем одним нажатием, ровно её же функциями:
		 *   1. забираем готовые награды (`finishJob`);
		 *   2. в опустевший скворечник покупаем ТУ ЖЕ породу — только за сТ
		 *      (решение владельца 2026-09-18; Попугай за Coins — никогда);
		 *   3. ждущие заказы раздаём свободным птицам: тяжёлый заказ —
		 *      самой слабой птице, которая его тянет, чтобы сильная
		 *      оставалась под тяжёлые; не хватает выносливости — сперва
		 *      кормим (`feedBird`), как сделал бы игрок;
		 *   4. чего сделать нельзя — говорим словами: какого продукта не
		 *      хватает, какой скворечник пуст.
		 *
		 * ЧЕГО НЕ ДЕЛАЕМ НИКОГДА: ускорений (300 Coins), отмены заказа
		 * (решение владельца — только сообщать), покупок за Coins и перья,
		 * соревнований (слот `pvp`), бонусов, специализаций.
		 *
		 * Каждый шаг — запрос к серверу, и «сделано» мы считаем только по
		 * ответу: `birds.data` игра заменяет целиком, по нему и ждём.
		 */
		var POST_PASSES = 5;        // кругов «забрать → купить → раздать» за одно нажатие
		var POST_WAIT_MS = 15000;   // сколько ждём ответа сервера на один шаг
		// Окно игры на экране, а данных ещё нет: столько ждём, прежде чем счесть
		// окно отказом. Игра показывает окна и не по нашему поводу — достижение,
		// уровень, акция, — и данные приходят следом; отказ же данных не приносит.
		var DIALOG_GRACE_MS = 3000;

		function post() {
			var b = window.birds;
			if (!b || typeof b.init !== 'function' || typeof b.startJob !== 'function') return null;
			return b;
		}

		function postData() {
			var b = post();
			var d = b && b.data;
			return (d && d.config && d.data) ? d : null;
		}

		function postOpen() {
			var e = document.getElementById('birds');
			if (!e) return false;
			try { return getComputedStyle(e).display !== 'none'; } catch (err) { return false; }
		}

		function num(x) { var n = parseFloat(x); return isNaN(n) ? 0 : n; }

		// Диалог игры посреди цикла — это её отказ (нет продукта, нет денег):
		// сервер ответил ошибкой, `ajax.error` показал её словами, а данные
		// не пришли. Читаем эти слова, чтобы отдать игроку, а не «не ответила».
		function gameDialogText() {
			try {
				var d = document.getElementById('baseDialog');
				if (!d || getComputedStyle(d).display === 'none') return '';
				var t = document.getElementById('baseDialogText');
				return t ? String(t.textContent || '').replace(/\s+/g, ' ').trim() : '';
			} catch (e) { return ''; }
		}

		function postWait(get) {
			var deadline = Date.now() + POST_WAIT_MS, окноС = 0;
			return new Promise(function (resolve, reject) {
				(function tick() {
					var v;
					try { v = get(); } catch (e) { v = null; }
					if (v) return resolve(v);
					var said = gameDialogText();
					if (said) {
						if (!окноС) окноС = Date.now();
						if (Date.now() - окноС >= DIALOG_GRACE_MS) return reject(new Error('Игра ответила: ' + said));
					} else {
						окноС = 0;
					}
					if (Date.now() > deadline) return reject(new Error('игра не ответила вовремя'));
					setTimeout(tick, 200);
				})();
			});
		}

		function openPost() {
			var b = post();
			if (!b) return Promise.reject(new Error('Птичьей почты на странице нет.'));
			if (!hasLocation('birds')) return Promise.reject(new Error('У вас нет птичьей почты.'));
			if (!(postOpen() && postData())) {
				try { goTo('birds'); b.init(); }
				catch (e) { return Promise.reject(new Error('Не получилось открыть птичью почту.')); }
			}
			return postWait(function () { return postOpen() && !!postData(); });
		}

		// Назад в сад — тем же путём, что быстрая навигация игры:
		// `setLocation` сам закрывает почту и возвращает полку.
		function closePost() {
			var n = currentGarden();
			try { if (post()) post().close(); } catch (e) { /* уже закрыта */ }
			goTo(n ? 'garden' + n : 'garden');
		}

		// Слоты заказов, которые у игрока есть и в которых обычные заказы.
		function postSlots(d) {
			var out = [];
			for (var s in d.config.jobslots) {
				var c = d.config.jobslots[s];
				if (c.pvp || c.comingsoon) continue;                       // соревнование — не наше
				if (num(c.level) > num(d.data.level)) continue;            // заперт уровнем
				if (num(s) > 1 && !(d.data.jobslots && d.data.jobslots[s])) continue;   // не куплен
				out.push(s);
			}
			return out;
		}

		function jobOf(d, s) { return (d.data.jobs && d.data.jobs[s]) || null; }
		function jobRunning(j) { return !!j && num(j.startdate) > 0; }
		function jobReady(j) { return jobRunning(j) && num(j.remain) <= 0; }
		function jobPending(j) {
			return !!j && num(j.startdate) <= 0 && !(num(j.remove_remain) > 0) && !(num(j.house) > 0);
		}

		// Скворечник действует: куплен и, если арендован, аренда не вышла.
		function houseActive(d, h) {
			var hd = d.data.houses && d.data.houses[h];
			if (!hd) return false;
			var hc = d.config.houses[h] || {};
			if (num(hc.level) > num(d.data.level)) return false;
			if (hc.rent && num(hd.remain) <= 0) return false;
			return true;
		}

		function birdLoad(d, h) {
			var hd = d.data.houses[h], bc = d.config.birds[hd.bird.type] || {};
			var load = num(bc.load);
			if (hd.special && d.config.special[hd.special]) load += num(d.config.special[hd.special].load);
			return load;
		}

		function shelfMissing(products) {
			var out = [];
			for (var p in products) {
				var have = num(window.regal && regal.getCount(p)), need = num(products[p]);
				if (have < need) {
					out.push({ pid: p, need: need - have,
					           name: (window.data_products && data_products[p] && data_products[p].name) || ('продукт ' + p) });
				}
			}
			return out;
		}

		function feedable(d, h) {
			var b = d.data.houses[h].bird;
			return !!(b && b.feed) && shelfMissing(b.feed).length === 0;
		}

		// Лучшая птица за сТ, доступная по уровню почты: когда породу
		// ушедшей узнать негде (скворечник был пуст ещё до нас).
		function bestMoneyBird(d) {
			var best = null, bestLevel = -1;
			for (var t in d.config.birds) {
				var c = d.config.birds[t];
				if (!(num(c.money) > 0) || num(c.coins) > 0 || num(c.feathers) > 0) continue;
				if (num(c.level) > num(d.data.level)) continue;
				if (num(c.level) > bestLevel) { bestLevel = num(c.level); best = t; }
			}
			return best;
		}

		function missingText(list) {
			var parts = [];
			for (var i = 0; i < list.length; i++) parts.push(list[i].name + ' ×' + list[i].need);
			return parts.join(', ');
		}

		/*
		 * Один круг: забрать → купить → раздать. Возвращает, сколько всего
		 * сделал; ноль — можно останавливаться. Каждое действие ждёт ответа
		 * сервера, а награду ловим на её же `showRewards`: игра зовёт его с
		 * тем, что прислал сервер, — в том числе с «птица ушла на пенсию».
		 */
		function postPass(итог, известно) {
			var b = post(), d = postData();
			if (!b || !d) return Promise.reject(new Error('Почта ещё не загрузилась.'));
			var сделано = 0;
			var slots = postSlots(d);

			function запомнитьПтиц() {
				var dd = postData();
				for (var h in dd.data.houses) {
					if (dd.data.houses[h].bird) известно[h] = dd.data.houses[h].bird.type;
				}
			}
			запомнитьПтиц();

			// ── 1. забрать готовые ────────────────────────────────────
			var i = 0;
			function забрать() {
				for (; i < slots.length; i++) {
					var s = slots[i], dd = postData(), j = jobOf(dd, s);
					if (!jobReady(j)) continue;
					if (!houseActive(dd, j.house)) {
						итог.заметки.push('заказ ' + s + ' готов, но скворечник ' + j.house + ' не действует');
						continue;
					}
					var было = j.id, дом = j.house;
					notify.wait('Почта: забираю заказ ' + s);
					b.currentJobSlot = num(s);
					try { b.finishJob(); }
					catch (e) { return Promise.reject(new Error('Не получилось забрать заказ ' + s + '.')); }
					i++;
					return postWait(function () {
						var x = jobOf(postData(), s);
						return !x || x.id !== было || !jobRunning(x);
					}).then(function () {
						сделано++;
						итог.забрано++;
						var r = итог.последняяНаграда;
						if (r && r.birdgone) итог.ушли.push({ house: дом, type: r.birdgone });
						итог.последняяНаграда = null;
						return sleep(PACE_MS).then(забрать);
					});
				}
				return Promise.resolve();
			}

			// ── 2. купить птицу в пустой скворечник ───────────────────
			function купить() {
				var dd = postData();
				for (var h in dd.data.houses) {
					if (!houseActive(dd, h) || dd.data.houses[h].bird) continue;
					if (итог.пустые[h]) continue;                 // уже разобрались с ним в этот раз
					итог.пустые[h] = true;
					var type = известно[h] || bestMoneyBird(dd);
					var c = type && dd.config.birds[type];
					var name = (window.t_birds_birdnames && t_birds_birdnames[type]) || ('птица ' + type);
					if (!c || !(num(c.money) > 0) || num(c.coins) > 0 || num(c.feathers) > 0) {
						итог.заметки.push('скворечник ' + h + ' пуст: ' + name + ' продаётся не за сТ — купите сами');
						continue;
					}
					if (num(window.player_bar) < num(c.money)) {
						итог.заметки.push('скворечник ' + h + ' пуст: на ' + name + ' не хватает сТ');
						continue;
					}
					notify.wait('Почта: покупаю ' + name + ' в скворечник ' + h);
					try { b.buyBird(num(h), num(type)); }
					catch (e) { return Promise.reject(new Error('Не получилось купить птицу.')); }
					return postWait(function () {
						var x = postData().data.houses[h];
						return !!(x && x.bird);
					}).then(function () {
						сделано++;
						итог.куплено.push(name + ' за ' + moneyText(num(c.money)));
						return sleep(PACE_MS).then(купить);
					});
				}
				return Promise.resolve();
			}

			// ── 3. раздать ждущие заказы ──────────────────────────────
			function раздать() {
				var dd = postData();
				var busy = {}, s, j;
				for (s in dd.data.jobs) {
					j = dd.data.jobs[s];
					if (jobRunning(j) && num(j.house) > 0) busy[j.house] = true;
				}
				// Тяжёлые заказы первыми: им годится меньше птиц.
				var pending = [];
				for (var k = 0; k < slots.length; k++) {
					j = jobOf(dd, slots[k]);
					if (jobPending(j) && !итог.ждут[slots[k]]) pending.push(slots[k]);
				}
				pending.sort(function (a, c) {
					var ja = jobOf(dd, a), jc = jobOf(dd, c);
					var la = num(dd.config.packagesize[ja.size].load), lc = num(dd.config.packagesize[jc.size].load);
					return (lc - la) || (num(jc.endurance) - num(ja.endurance));
				});

				for (var n = 0; n < pending.length; n++) {
					s = pending[n]; j = jobOf(dd, s);
					var needLoad = num(dd.config.packagesize[j.size].load), needEnd = num(j.endurance);
					var missing = shelfMissing(j.products);
					if (missing.length) {
						итог.ждут[s] = 'не хватает ' + missingText(missing);
						continue;
					}
					var best = null;
					for (var h in dd.data.houses) {
						if (busy[h] || !houseActive(dd, h) || !dd.data.houses[h].bird) continue;
						var bird = dd.data.houses[h].bird;
						if (birdLoad(dd, h) < needLoad) continue;
						var сыт = num(bird.endurance) >= needEnd;
						if (!сыт && !(num(bird.endurance_max) >= needEnd && feedable(dd, h))) continue;
						var cand = { house: h, load: birdLoad(dd, h), feed: !сыт, end: num(bird.endurance) };
						if (!best || cand.load < best.load ||
						    (cand.load === best.load && (best.feed && !cand.feed)) ||
						    (cand.load === best.load && cand.feed === best.feed && cand.end > best.end)) best = cand;
					}
					if (!best) {
						итог.ждут[s] = 'нет свободной птицы: нужна сила ' + needLoad + ' и выносливость ' + needEnd;
						continue;
					}
					busy[best.house] = true;
					return (best.feed ? покормить(best.house) : Promise.resolve())
						.then(function () {
							var x = postData().data.houses[best.house].bird;
							if (num(x.endurance) < needEnd) {
								итог.ждут[s] = 'птице в скворечнике ' + best.house + ' не хватило выносливости и после корма';
								return null;
							}
							return отправить(s, best.house);
						})
						.then(function () { return sleep(PACE_MS).then(раздать); });
				}
				return Promise.resolve();
			}

			function покормить(h) {
				var было = num(postData().data.houses[h].bird.endurance);
				notify.wait('Почта: кормлю птицу в скворечнике ' + h);
				try { b.feedBird(num(h)); }
				catch (e) { return Promise.reject(new Error('Не получилось покормить птицу.')); }
				return postWait(function () {
					var x = postData().data.houses[h];
					return !!(x && x.bird && num(x.bird.endurance) > было);
				}).then(function () { сделано++; итог.покормлено++; return sleep(PACE_MS); });
			}

			function отправить(s, h) {
				var dd = postData();
				notify.wait('Почта: отправляю заказ ' + s + ' из скворечника ' + h);
				b.currentJobSlot = num(s);
				b.currentJobHouseSlot = num(h);
				b.currentJobBird = num(dd.data.houses[h].bird.type);
				try { b.startJob(); }
				catch (e) { return Promise.reject(new Error('Не получилось отправить заказ ' + s + '.')); }
				return postWait(function () { return jobRunning(jobOf(postData(), s)); })
					.then(function () { сделано++; итог.отправлено++; });
			}

			return забрать().then(купить).then(раздать).then(function () { return сделано; });
		}

		function postSummary(итог) {
			var parts = ['забрано ' + итог.забрано, 'отправлено ' + итог.отправлено, 'покормлено ' + итог.покормлено];
			var s = 'Почта: ' + parts.join(', ') + '.';
			if (итог.перья > 0) s += ' Перьев +' + итог.перья + '.';
			if (итог.куплено.length) s += ' Куплено: ' + итог.куплено.join(', ') + '.';
			var ждут = [];
			for (var k in итог.ждут) ждут.push('заказ ' + k + ' — ' + итог.ждут[k]);
			if (ждут.length) s += ' Ждёт: ' + ждут.join('; ') + '.';
			if (итог.заметки.length) s += ' ' + итог.заметки.join('. ') + '.';
			return s;
		}

		function newPostTotal() {
			return { забрано: 0, отправлено: 0, покормлено: 0, перья: 0, куплено: [],
			         ушли: [], пустые: {}, ждут: {}, заметки: [], последняяНаграда: null };
		}

		/*
		 * Весь цикл на открытой почте. Награду игра показывает своим окном
		 * на каждый заказ — на время цикла подменяем `showRewards` на
		 * подсчёт, итог скажем одним шариком; после — возвращаем как было.
		 */
		function workPost(итог) {
			var b = post();
			var было = b.showRewards, перьяБыло = num(postData().data.feather);
			var известно = {};
			b.showRewards = function (r) { итог.последняяНаграда = r || null; };

			var pass = 0;
			function круг() {
				if (pass >= POST_PASSES) return Promise.resolve();
				pass++;
				return postPass(итог, известно).then(function (n) { return n ? круг() : null; });
			}

			function вернуть() {
				if (b.showRewards === undefined) return;
				// Свойство экземпляра прятало прототип — снимаем, если было так.
				if (Object.prototype.hasOwnProperty.call(b, 'showRewards') && было === b.constructor.prototype.showRewards) {
					delete b.showRewards;
				} else {
					b.showRewards = было;
				}
				try { итог.перья = Math.max(0, num(postData().data.feather) - перьяБыло); } catch (e) { /* без перьев */ }
			}

			return круг().then(function () { вернуть(); }, function (err) { вернуть(); throw err; });
		}

		function runPost() {
			if (running) return;
			if (!post() || !hasLocation('birds')) {
				notify.error('Птичьей почты у вас нет — кнопке нечего делать.');
				return;
			}
			var итог = newPostTotal();
			busy(true);
			notify.wait('Открываю птичью почту');
			openPost()
				.then(function () { return workPost(итог); })
				.then(function () {
					closePost();
					notify.info(postSummary(итог));
				}, function (err) {
					try { closePost(); } catch (e) { /* вернёмся как выйдет */ }
					notify.error((err && err.message ? err.message : 'Почта не закончена.') +
						' Успел: ' + postSummary(итог));
				})
				.then(otpustit, otpustit);
		}

		// Почта — последняя остановка обхода. Нет её у игрока — молча мимо.
		function roundPost(итог) {
			if (!post() || !hasLocation('birds')) return Promise.resolve();
			var п = newPostTotal();
			итог.почта = п;
			notify.wait('Почта');
			return openPost()
				.then(function () { return workPost(п); })
				.then(function () { closePost(); }, function (err) {
					try { closePost(); } catch (e) { /* вернёмся как выйдет */ }
					п.заметки.push(err && err.message ? err.message : 'почта не закончена');
				});
		}

		/*
		 * ═══ ПАСЕКА: ОТПРАВИТЬ ВЕРНУВШИЕСЯ ПЧЕЛИНЫЕ СЕМЬИ ═══════════
		 *
		 * Разобрано по объекту `bees` из wurzel_all.js и по живой игре
		 * 2026-09-19 (docs/game-api.md, «Пасека»). Улей летит за мёдом
		 * маршрутом («Быстро» 2 ч, «Тщательно» 8 ч, «До последнего цвета»
		 * 24 ч), мёд по возвращении сам ложится в соты. Ручной труд один:
		 * КАЖДЫЙ вернувшийся улей отправлять заново — окно с выбором
		 * маршрута и кнопка «Вылет». У игрока 20 ульев, три раза в сутки.
		 * Кнопка «Отправить все пчелиные семьи» в игре есть — но только
		 * для премиум-аккаунта (`_PREMIUM`); по одному — бесплатно всем.
		 *
		 * Что делаем одним нажатием, её же функциями:
		 *   1. каждый вернувшийся улей отправляем ТЕМ ЖЕ маршрутом, каким
		 *      он летал (по длительности прошлого полёта); улей без истории —
		 *      маршрутом большинства;
		 *   2. соты, заполненные до отказа, разливаем по банкам (`fill`) —
		 *      это же нажатие игрок делает сам, а «гном-пчеловод» игры,
		 *      который делает это автоматически, — платный;
		 *   3. чего нельзя — говорим: не посажены цветы, соты полны, маршрут
		 *      платный.
		 *
		 * НИКОГДА: ускорений (`finishHiveTimer` — Coins), платных маршрутов,
		 * покупки ульев и улучшений, бустеров, пчеловода, смены продукта.
		 */
		function bees() {
			var b = window.bees;
			if (!b || typeof b.init !== 'function' || typeof b.startFlight !== 'function') return null;
			return b;
		}

		function beesData() {
			var b = bees();
			var d = b && b.data;
			return (d && d.config && d.data && d.data.hives) ? d : null;
		}

		function beesOpen() {
			var e = document.getElementById('bees');
			if (!e) return false;
			try { return getComputedStyle(e).display !== 'none'; } catch (err) { return false; }
		}

		function openBees() {
			var b = bees();
			if (!b) return Promise.reject(new Error('Пасеки на странице нет.'));
			if (!hasLocation('bees')) return Promise.reject(new Error('У вас нет пасеки.'));
			if (!(beesOpen() && beesData())) {
				try { goTo('bees'); b.init(); }
				catch (e) { return Promise.reject(new Error('Не получилось открыть пасеку.')); }
			}
			return postWait(function () { return beesOpen() && !!beesData(); });
		}

		function closeBees() {
			var n = currentGarden();
			try { if (bees()) bees().close(); } catch (e) { /* уже закрыта */ }
			goTo(n ? 'garden' + n : 'garden');
		}

		function hiveFlying(h) { return !!(h && h.tour_start && num(h.tour_remain) > 0); }

		// Маршрут улья — по длительности его прошлого полёта.
		function tourByDuration(d, duration) {
			for (var t in d.config.tours) {
				if (num(d.config.tours[t].duration) === num(duration)) return t;
			}
			return null;
		}

		// Маршрут для улья без истории: каким летает большинство; никого — второй.
		function usualTour(d) {
			var votes = {}, best = null;
			for (var k in d.data.hives) {
				var t = tourByDuration(d, d.data.hives[k].tour_duration);
				if (!t) continue;
				votes[t] = (votes[t] || 0) + 1;
				if (!best || votes[t] > votes[best]) best = t;
			}
			return best || (d.config.tours['2'] ? '2' : Object.keys(d.config.tours)[0]);
		}

		function tourPaid(d, t) {
			var c = d.config.tours[t];
			return !c || num(c.money) > 0 || num(c.coins) > 0;
		}

		// Полные соты — те, что игра сама предлагает разлить (`comps`: ≥ 100 %).
		function fullCombs(d) {
			var n = 0;
			for (var pid in d.config.mapping) {
				if (num(d.stock && d.stock[pid]) >= num(d.config.max_capacity)) n++;
			}
			return n;
		}

		function newBeesTotal() {
			return { отправлено: 0, летят: 0, ближайший: 0, разлито: 0, маршрут: '', заметки: [] };
		}

		// Между ульями — не 140 мс, как между клетками: каждый вылет — свой запрос
		// к серверу, а игрок жмёт «Вылет» не чаще раза в секунду.
		var BEES_PACE_MS = 600;

		function workBees(итог) {
			var b = bees(), d = beesData();
			if (!b || !d) return Promise.reject(new Error('Пасека ещё не загрузилась.'));
			var обычный = usualTour(d);
			var keys = [];
			for (var k in d.data.hives) keys.push(k);
			keys.sort(function (a, c) { return num(a) - num(c); });
			var маршруты = {}, неудачи = {}, отказовПодряд = 0;

			/*
			 * ОДИН УЛЕЙ БЕЗ ОТВЕТА НЕ ОСТАНАВЛИВАЕТ ОСТАЛЬНЫХ. Живая игра 2026-09-19:
			 * первое нажатие отправило ульи 1–12, на 13-м ответ сервера не пришёл
			 * за 15 с, и помощник бросил всех — игроку пришлось нажимать снова.
			 * Теперь такой улей пропускается, остальные летят, а пропущенных
			 * пробуем ещё раз в конце; кто и после этого не ответил — в отчёт.
			 */
			function отправлять(список, повтор) {
				var i = 0;
				function шаг() {
					for (; i < список.length; i++) {
						var id = список[i], dd = beesData(), h = dd.data.hives[id];
						if (!h || !h.pid || h.buyable || h.blocked) continue;    // пустой или не куплен
						if (hiveFlying(h)) {
							if (!повтор) {
								итог.летят++;
								if (!итог.ближайший || num(h.tour_remain) < итог.ближайший) итог.ближайший = num(h.tour_remain);
							}
							continue;
						}
						if (повтор) { /* второй заход — только по тем, кто не ответил */ }
						var flower = dd.config.mapping[h.pid] && dd.config.mapping[h.pid].pid;
						if (!(num(dd.garden && dd.garden[flower]) > 0)) {
							if (!повтор) итог.заметки.push('улей ' + id + ': в садах не посажено ' +
								((window.data_products && data_products[flower] && data_products[flower].name) || ('растение ' + flower)));
							continue;
						}
						if (num(dd.stock && dd.stock[h.pid]) >= num(dd.config.new_capacity)) {
							if (!повтор) итог.заметки.push('улей ' + id + ': соты полны, мёд некуда складывать');
							continue;
						}
						var t = tourByDuration(dd, h.tour_duration) || обычный;
						if (tourPaid(dd, t)) {
							if (!повтор) итог.заметки.push('улей ' + id + ': прошлый маршрут платный — не отправляю');
							continue;
						}
						notify.wait('Пасека: отправляю улей ' + id + (повтор ? ' (ещё раз)' : ''));
						try { b.startFlight(num(id), num(t)); }
						catch (e) { неудачи[id] = { why: 'не получилось отправить', снова: false }; i++; continue; }
						i++;
						return postWait(function () { return hiveFlying(beesData().data.hives[id]); })
							.then(function () {
								итог.отправлено++;
								delete неудачи[id];
								отказовПодряд = 0;
								маршруты[t] = (маршруты[t] || 0) + 1;
								return sleep(BEES_PACE_MS).then(шаг);
							}, function (err) {
								// Молчание — запомнить и попробовать ещё раз в конце.
								// Отказ словами — не повторять; три отказа подряд —
								// остановиться с этими словами: сервер что-то говорит.
								try { if (window.basedialog) basedialog.close(); } catch (e) { /* окна нет */ }
								var why = (err && err.message) ? err.message : 'игра не ответила';
								var словами = why.indexOf('Игра ответила') === 0;
								неудачи[id] = { why: why, снова: !словами };
								отказовПодряд = словами ? отказовПодряд + 1 : 0;
								if (отказовПодряд >= 3) return Promise.reject(err);
								return sleep(BEES_PACE_MS * 2).then(шаг);
							});
					}
					return Promise.resolve();
				}
				return шаг();
			}

			function разлить() {
				var dd = beesData(), было = fullCombs(dd);
				if (!было || typeof b.fill !== 'function') return Promise.resolve();
				notify.wait('Пасека: разливаю мёд из полных сот');
				try { b.fill(); }
				catch (e) { return Promise.reject(new Error('Не получилось разлить мёд.')); }
				return postWait(function () { return fullCombs(beesData()) < было; })
					.then(function () { итог.разлито = было - fullCombs(beesData()); },
					      function (err) {
						// Не разлилось (например, банки на полке полны) — не повод не
						// отправлять остальных: закрываем её окно, запоминаем слова, а
						// улей с полными сотами отсеет проверка ниже.
						try { if (window.basedialog) basedialog.close(); } catch (e) { /* окна нет */ }
						итог.заметки.push('мёд не разлит: ' + (err && err.message ? err.message : 'игра не ответила'));
					});
			}

			// Сначала разлить: улей с полными сотами игра не отпустит в полёт.
			return разлить().then(function () { return отправлять(keys, false); }).then(function () {
				var снова = [];
				for (var id in неудачи) if (неудачи[id].снова) снова.push(id);
				if (!снова.length) return null;
				return sleep(BEES_PACE_MS * 3).then(function () { return отправлять(снова, true); });
			}).then(function () {
				for (var id in неудачи) итог.заметки.push('улей ' + id + ': ' + неудачи[id].why);
				var лучший = null;
				for (var t in маршруты) if (!лучший || маршруты[t] > маршруты[лучший]) лучший = t;
				var dd = beesData();
				if (лучший && dd.config.tours[лучший]) итог.маршрут = dd.config.tours[лучший].name;
			});
		}

		function beesSummary(итог) {
			var s = 'Пасека: отправлено ' + итог.отправлено;
			if (итог.маршрут) s += ' («' + итог.маршрут + '»)';
			s += ', в полёте ' + итог.летят;
			if (итог.летят && итог.ближайший) s += ' (ближайший вернётся через ' + formatHours(итог.ближайший) + ')';
			s += '.';
			if (итог.разлито) s += ' Мёд разлит из ' + итог.разлито + ' ' + plural(итог.разлито, 'соты', 'сот', 'сот') + '.';
			if (итог.заметки.length) s += ' ' + итог.заметки.join('; ') + '.';
			return s;
		}

		function formatHours(sec) {
			var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
			if (h > 0) return h + ' ч' + (m ? ' ' + m + ' мин' : '');
			return m + ' мин';
		}

		function runBees() {
			if (running) return;
			if (!bees() || !hasLocation('bees')) {
				notify.error('Пасеки у вас нет — кнопке нечего делать.');
				return;
			}
			var итог = newBeesTotal();
			busy(true);
			notify.wait('Открываю пасеку');
			openBees()
				.then(function () { return workBees(итог); })
				.then(function () {
					closeBees();
					notify.info(beesSummary(итог));
				}, function (err) {
					try { closeBees(); } catch (e) { /* вернёмся как выйдет */ }
					notify.error((err && err.message ? err.message : 'Пасека не закончена.') +
						' Успел: ' + beesSummary(итог));
				})
				.then(otpustit, otpustit);
		}

		function runRound() {
			if (running) return;
			if (inWaterGarden()) {
				notify.error('Начните обход из обычного сада — водный он обойдёт сам.');
				return;
			}
			var list = ownedGardens();
			if (!list || !list.length) {
				notify.error('Пока не знаю, какие у вас сады. Нажмите иконку садов в столбике справа.');
				return;
			}
			var pid = currentSeed();
			var домой = currentGarden();
			var итог = { собрано: 0, посажено: 0, полито: 0, садов: 0 };
			busy(true);

			var i = 0;
			function шаг() {
				if (i >= list.length) return Promise.resolve();
				var n = list[i], где = ' (сад ' + (i + 1) + ' из ' + list.length + ')';
				return goToGarden(n)
					.then(function () {
						notify.wait('Собираю' + где);
						return harvestHere();
					})
					.then(function (r) {
						итог.собрано += (r && r.harvested) || 0;
						if (!pid) return null;
						notify.wait('Сажаю' + где);
						// Семена кончились — это не повод обрывать обход:
						// в следующем саду поливать всё равно надо.
						return plantAll(pid).then(null, function () { return null; });
					})
					.then(function (r) {
						итог.посажено += (r && r.planted) || 0;
						notify.wait('Поливаю' + где);
						return waterAll();
					})
					.then(function (r) {
						итог.полито += (r && r.watered) || 0;
						итог.садов++;
						i++;
						return шаг();
					});
			}

			шаг().then(function () {
				return roundWaterGarden(итог);
			}).then(function () {
				return roundPost(итог);
			}).then(function () {
				// Возвращаем игрока туда, откуда он начал.
				return домой ? goToGarden(домой).then(null, function () { return null; }) : null;
			}).then(function () {
				var s = 'Обход ' + итог.садов + ' ' +
					plural(итог.садов, 'сада', 'садов', 'садов') + ': собрано ' +
					итог.собрано + ', посажено ' + итог.посажено + ', полито ' + итог.полито + '.';
				if (итог.водный) s += ' Водный сад тоже.';
				if (!pid) s += ' Семена не выбраны — не сажал.';
				if (итог.почта) s += ' ' + postSummary(итог.почта);
				notify.info(s);
				if (painted) paint(true);
			}, function (err) {
				notify.error((err && err.message ? err.message : 'Обход не закончен.') +
					' Успел: собрано ' + итог.собрано + ', посажено ' + итог.посажено +
					', полито ' + итог.полито + '.');
			}).then(function () { otpustit(); refreshPlantButton(); },
			        function () { otpustit(); refreshPlantButton(); });
		}

		// ── строка кнопок: живёт в ряду игры, своей рамки не имеет ────
		var bar = document.createElement('div');
		bar.id = 'si-helper';
		// Накладка поверх полосы помощников. Сама ничего не ловит мышью
		// (`pointer-events:none`), ловят только гномы внутри — иначе прозрачный
		// прямоугольник перехватывал бы клики по игре.
		bar.setAttribute('style', [
			'position:fixed', 'z-index:99999', 'pointer-events:none',
			'font:12px/1.2 system-ui,Segoe UI,sans-serif'
		].join(';'));

		function gameButton(text, title, onclick) {
			var b = document.createElement('button');
			b.type = 'button';
			b.textContent = text;
			b.title = title || text;
			b.setAttribute('style', [
				'padding:5px 9px', 'border:1px solid rgba(0,0,0,.5)', 'border-radius:5px',
				'background:rgba(28,38,26,.82)', 'color:#eaf6e6', 'font:inherit',
				'font-weight:600', 'cursor:pointer', 'white-space:nowrap',
				'box-shadow:0 1px 3px rgba(0,0,0,.4)'
			].join(';'));
			b.onmouseenter = function () { if (!b.disabled) b.style.background = 'rgba(48,66,44,.92)'; };
			b.onmouseleave = function () { b.style.background = 'rgba(28,38,26,.82)'; };
			b.onclick = onclick;
			return b;
		}

		/*
		 * ГНОМЫ — картинками самой игры, как у Ивана.
		 *
		 * Он вставлял `kannenzwerg.gif` из папки `pics/verkauf/`. Картинка жива
		 * и сегодня, только лежит не на игровом сервере, а на его хранилище
		 * (`wurzelimperium.wavecdn.net`) — я сперва стучался не туда и решил,
		 * что её больше нет. Путь берём из `_GFX`, как делал он; если игра его
		 * не выложила, подставляем известный адрес хранилища.
		 *
		 * Размеры — родные, из самих файлов: гном с лейкой 25×45, посадочный
		 * автомат 45×45, гном-исследователь 36×45, солнце 56×44.
		 */
		function gfx(path) {
			if (path.indexOf('data:') === 0) return path;   // своя картинка, не с сервера игры
			var base = (typeof window._GFX === 'string' && window._GFX)
				? window._GFX
				: 'https://wurzelimperium.wavecdn.net/';
			if (base.charAt(base.length - 1) !== '/') base += '/';
			return base + path;
		}

		function gnomeButton(key, path, w, h, title, onclick) {
			var b = document.createElement('img');
			b.src = gfx(path);
			b.alt = title;
			b.title = title;
			// Постоянная метка: подписи меняются по ходу игры, и цепляться
			// к ним ни коду, ни проверкам нельзя.
			b.setAttribute('data-si-button', key);
			b.setAttribute('data-si-w', String(w));
			b.setAttribute('data-si-h', String(h));
			b.setAttribute('style', [
				'position:absolute', 'pointer-events:auto',
				'width:' + w + 'px', 'height:' + h + 'px', 'cursor:pointer',
				'display:block', 'border:0', 'background:none',
				'filter:drop-shadow(0 1px 2px rgba(0,0,0,.55))', 'transition:transform .1s'
			].join(';'));
			b.onmouseenter = function () { if (!b.disabled) b.style.transform = 'scale(1.12)'; };
			b.onmouseleave = function () { b.style.transform = ''; };
			// Нажатие всегда доходит до обработчика: он сам скажет шариком,
			// почему нельзя. Молча проглатывать нажатие на притушенного
			// гнома — значит оставить человека в недоумении.
			b.onclick = function () { onclick(); };

			// Картинка игры может однажды пропасть — тогда вместо пустоты
			// показываем обычную кнопку с подписью, а не ничего.
			b.onerror = function () {
				var t = gameButton(title.split(' ')[0], title, onclick);
				if (b.parentNode) b.parentNode.replaceChild(t, b);
			};
			return b;
		}

		/*
		 * ═══ РЫНОК: БЫСТРАЯ ПРОДАЖА ═══════════════════════════════
		 *
		 * Была в карточке пропавшего расширения. Разбор живой игры —
		 * `docs/game-api.md`, раздел «Рынок и торговая палатка».
		 *
		 * Рынок лежит на ТРИ кадра вглубь, и в `wurzel_all.js` его нет вовсе:
		 *   верхнее окно → #stadtframe (город) → #shopframe (палатка).
		 * Мы живём только в верхнем окне и второй точки вставки не заводим —
		 * кадры того же узла, до них достаём через `contentWindow`.
		 *
		 * Чем это НЕ похоже на приём Ивана: у него скрытый кадр сам обходил
		 * рынок по всем овощам с паузами 5–9 секунд (`autoUpdateMarketPrice.js`).
		 * Это работа без игрока — запрещена АС-7 и со стороны сервера
		 * выглядит ботом. Мы берём цену ОДНОГО товара одним запросом и
		 * только тогда, когда на этот товар нажали.
		 *
		 * ВНИМАНИЕ ПРО МАССИВЫ: на странице игры живёт Prototype.js, он
		 * подменяет `Array.prototype.filter` своей версией с другими
		 * аргументами. Поэтому здесь только обычные циклы. Поймано на живой
		 * игре 2026-09-10: `filter` уронил разведку с «Cannot read property
		 * indexOf of undefined».
		 */
		var PUT_PALATKA = '/stadt/marktstand.php';
		var PUT_RYNOK = '/stadt/markt.php';
		var SHAG_CENY = 0.01;   // на столько подрезаем самого дешёвого

		// Внутренний кадр, какую бы страницу он ни показывал.
		function shopWindow() {
			try {
				var sf = document.getElementById('stadtframe');
				if (!sf || !sf.contentWindow) return null;
				var shop = sf.contentWindow.document.getElementById('shopframe');
				if (!shop || !shop.contentWindow || !shop.contentWindow.document) return null;
				return shop.contentWindow;
			} catch (e) { return null; }   // чужой узел — молча выходим
		}

		/*
		 * Палатка, ГОТОВАЯ принять новое предложение.
		 *
		 * Отличать её от просто «страницы палатки» пришлось после живой игры
		 * 2026-09-10: страница комиссии лежит по тому же адресу и тоже
		 * содержит `#preisschild`, но полей ценника на ней НЕТ. Мы принимали
		 * её за палатку, заполнять было нечего, и помощник отвечал
		 * «палатка выглядит иначе, чем мы её знаем». Так случилось бы у
		 * любого, кто подготовил сделку и ушёл, не подтвердив.
		 */
		// Открыт ли город. Кадр с палаткой переживает выход из города, и
		// судить по одному лишь наличию страницы нельзя.
		function gorodOtkryt() {
			try {
				if (window.stadt === true) return true;
				var e = document.getElementById('stadt');
				return !!(e && getComputedStyle(e).display !== 'none');
			} catch (e) { return false; }
		}

		function stallWindow() {
			// Город закрыт — значит палатки на экране нет, сколько бы
			// страниц ни осталось в кадре.
			if (!gorodOtkryt()) return null;
			var w = shopWindow();
			if (!w) return null;
			try {
				if (!w.document.getElementById('preisschild')) return null;
				if (!w.document.getElementById('produkt_anzahl')) return null;
			} catch (e) { return null; }
			return w;
		}

		function waitFor(get, ms) {
			var deadline = Date.now() + (ms || 15000);
			return new Promise(function (resolve, reject) {
				(function tick() {
					var v;
					try { v = get(); } catch (e) { v = null; }
					if (v) return resolve(v);
					if (Date.now() > deadline) return reject(new Error('игра не ответила вовремя'));
					setTimeout(tick, 200);
				})();
			});
		}

		/*
		 * Открыть палатку.
		 *
		 * НЕ «сделать шаг и понадеяться»: город грузится в кадр, и адрес,
		 * записанный в `#shopframe` слишком рано, игра затирает своим, когда
		 * её страница дочитывается. На живой игре 2026-09-10 это выглядело
		 * так: город открыт, кадр на месте, а `src` остался `about:blank`, и
		 * помощник честно ждал палатку, которой никто не грузил. Стенд этого
		 * не поймал — он был добрее игры, его город готов мгновенно.
		 *
		 * Поэтому здесь настойчивый цикл: пока палатки нет, каждый раз
		 * заново доводим состояние до нужного. Шаги идемпотентны, лишнего
		 * запроса не будет — `zeige` и подмена адреса зовутся только если
		 * кадр ещё не там, где надо.
		 */
		function openStall() {
			var already = stallWindow();
			if (already) return Promise.resolve(already);
			if (typeof window.zeigeStadtMain !== 'function') {
				return Promise.reject(new Error('Не нашёл в игре вход в город.'));
			}
			window.zeigeStadtMain(1);

			var deadline = Date.now() + 25000;
			var videliGorod = false;
			var postavili = false;      // адрес палатки задавали МЫ, а не кто-то до нас
			var perezagruzili = false;  // застрявшую страницу обновляем один раз
			return new Promise(function (resolve, reject) {
				(function tick() {
					// Принимаем палатку, ТОЛЬКО если её адрес задали мы сами.
					//
					// Иначе ловится такая гонка: город открылся, его кадр
					// начал перезагружаться, а в старом, ещё не выброшенном
					// документе палатка с прошлого раза на месте. Помощник
					// принимал её за готовую, а через миг документ заменялся
					// пустым — и палатка не открывалась вовсе. Поймано на
					// стенде 2026-09-10, ровно этим и объяснялось «нажал, а
					// ничего не произошло со второго раза».
					if (postavili) {
						var w = stallWindow();
						if (w) return resolve(w);
					}
					if (gorodOtkryt()) videliGorod = true;
					else if (videliGorod) {
						return reject(new Error('Город закрыт — продажу отменил.'));
					}
					if (Date.now() > deadline) {
						return reject(new Error('Палатка не открылась — игра не ответила.'));
					}
					try {
						var sf = document.getElementById('stadtframe');
						var cw = sf && sf.contentWindow;
						var cd = cw && cw.document;
						// Работаем только с ДОЧИТАННОЙ страницей города:
						// в недочитанной кадр ещё перепишут.
						if (cd && cd.readyState === 'complete') {
							var shop = cd.getElementById('shopframe');
							if (shop) {
								var src = shop.getAttribute('src') || '';
								if (src.indexOf('marktstand') === -1) {
									// Сперва даём игре открыть рамку магазина её
									// же способом — иначе кадр останется скрытым.
									if (src === '' || src === 'about:blank') {
										if (typeof window.zeige === 'function') window.zeige('markt');
									}
									shop.setAttribute('src', PUT_PALATKA);
									postavili = true;
								} else if (!postavili) {
									// Адрес палатки уже стоит, но ставили его не
									// мы: это страница с прошлого раза.
									// Перезагружаем через `about:blank` —
									// повторная запись того же адреса обновляет
									// кадр не во всех браузерах.
									shop.setAttribute('src', 'about:blank');
									shop.setAttribute('src', PUT_PALATKA);
									postavili = true;
								} else if (!perezagruzili) {
									// Ставили мы, страница ДОЧИТАНА, а ценника
									// нет — значит палатка застряла на комиссии
									// от прошлой, неподтверждённой сделки.
									// Даём ей ровно одну перезагрузку: уход с
									// той страницы и есть отказ от той сделки.
									var sd = shop.contentWindow && shop.contentWindow.document;
									if (sd && sd.readyState === 'complete') {
										perezagruzili = true;
										shop.setAttribute('src', 'about:blank');
										shop.setAttribute('src', PUT_PALATKA);
									}
								}
							}
						}
					} catch (e) { /* кадр ещё чужой — просто ждём */ }
					setTimeout(tick, 300);
				})();
			});
		}

		// Что лежит на складе: товар и сколько его.
		function stallGoods(w) {
			var out = [], divs = w.document.querySelectorAll('div[id]'), i;
			for (i = 0; i < divs.length; i++) {
				var m = /^p(\d+)$/.exec(divs[i].id);
				if (!m) continue;
				var box = w.document.getElementById('anzahl_' + m[1]);
				if (!box) continue;
				var n = parseInt(box.value, 10);
				if (!(n > 0)) continue;
				out.push({
					pid: parseInt(m[1], 10),
					name: divs[i].getAttribute('title') || ('товар ' + m[1]),
					count: n
				});
			}
			return out;
		}

		// Коридор допустимых цен. Игра публикует его сама; для части товаров
		// вместо объекта лежит просто 1 — тогда коридора нет.
		function corridor(w, pid) {
			var d = w.price_regulation_data;
			if (!d) return null;
			var c = d[pid] || d[String(pid)];
			if (!c || typeof c !== 'object') return null;
			var min = typeof c.min === 'number' ? c.min : null;
			var max = typeof c.max === 'number' ? c.max : null;
			if (min === null && max === null) return null;
			return { min: min, max: max };
		}

		// «0,08 сT» → 0.08; «1.195,68 сT» → 1195.68.
		// Точка у игры — разделитель тысяч, запятая — дробная часть.
		function parsePrice(text) {
			var s = String(text).replace(/[^\d.,]/g, '');
			if (!s) return null;
			s = s.replace(/\./g, '').replace(',', '.');
			var v = parseFloat(s);
			return isFinite(v) ? v : null;
		}

		// Самое дешёвое предложение по товару. null — предложений нет.
		function cheapestOnMarket(pid) {
			return fetch(PUT_RYNOK + '?filter=1&page=1&order=p&v=' + pid,
				{ credentials: 'same-origin' })
				.then(function (r) {
					if (!r.ok) throw new Error('рынок не ответил (' + r.status + ')');
					return r.text();
				})
				.then(function (html) {
					var doc = new DOMParser().parseFromString(html, 'text/html');
					var rows = doc.querySelectorAll('table tr'), i, j;
					if (!rows.length) return null;
					// Столбец цены ищем по заголовку, а не по номеру: порядок
					// столбцов — не то, на что стоит закладываться.
					var col = -1, head = rows[0].cells;
					for (j = 0; j < head.length; j++) {
						if (/цена|preis|price/i.test(head[j].textContent || '')) { col = j; break; }
					}
					if (col === -1) col = 3;
					var best = null;
					for (i = 1; i < rows.length; i++) {
						var cells = rows[i].cells;
						if (!cells || cells.length <= col) continue;
						var v = parsePrice(cells[col].textContent);
						if (v === null || !(v > 0)) continue;
						if (best === null || v < best) best = v;
					}
					return best;
				});
		}

		// Цена, которую предложим. Решение владельца 2026-09-10: на грош
		// дешевле самого дешёвого. Непроданный товар не приносит ничего и
		// занимает склад, поэтому берём скорость, а не цену за единицу.
		function askingPrice(w, pid, best) {
			var c = corridor(w, pid);
			var price;
			if (best === null || best === undefined) {
				// Предложений нет — подрезать некого, просим по верхней границе.
				if (!c || c.max === null) return null;
				price = c.max;
			} else {
				price = best - SHAG_CENY;
			}
			if (c) {
				if (c.min !== null && price < c.min) price = c.min;
				if (c.max !== null && price > c.max) price = c.max;
			}
			if (!(price > 0)) return null;
			return Math.round(price * 100) / 100;
		}

		/*
		 * ПРОДАЖА БЕЗ СВОИХ ПЛАШЕК — как делал Иван.
		 *
		 * Своего окна со списком товаров у нас больше нет. Владелец отверг
		 * его 2026-09-10 («уберем таблицы эти и панельки, уверен у Ивана это
		 * было как-то иначе реализовано») — так же, как до этого отверг
		 * таблицу выгоды. Иван и правда своих окон не рисовал: он вплетался
		 * в интерфейс игры, а не строил рядом свой.
		 *
		 * Поэтому теперь помощник ничего не показывает. Он ЗАПОЛНЯЕТ форму
		 * самой игры: игрок нажимает товар в палатке, как обычно, а помощник
		 * подставляет количество (весь запас) и цену (на грош дешевле самого
		 * дешёвого на рынке, в пределах коридора игры). Дальше игрок жмёт
		 * родные кнопки игры — «предложить на рынке» и «O.K.» на комиссии.
		 *
		 * Говорим тоже её голосом: в палатке есть гном с репликой
		 * (`setSprechText`), в неё и пишем. Своих шариков в чужом окне не
		 * заводим.
		 *
		 * Чего мы намеренно НЕ делаем: не нажимаем за игрока ни «предложить»,
		 * ни «O.K.». Комиссию рынка (10 %) он должен увидеть сам — это его
		 * деньги, и игра о них спрашивает не зря.
		 */
		function priceParts(price) {
			var whole = Math.floor(price);
			var cents = Math.round((price - whole) * 100);
			return { whole: String(whole), cents: (cents < 10 ? '0' : '') + String(cents) };
		}

		function formatPriceRu(price) {
			return price.toFixed(2).replace('.', ',');
		}

		/*
		 * Сообщения о продаже — нашим шариком, а не пузырём игры.
		 *
		 * Сперва я писал в реплику гнома палатки (`setSprechText`) — это
		 * выглядело самым «родным». На живой игре 2026-09-10 оказалось, что
		 * этот пузырь у игры — подсказка ПО НАВЕДЕНИЮ: соседний
		 * `clrSprechText` стирает её, едва мышь уходит, и текст не доживает
		 * до того, как игрок его прочтёт.
		 *
		 * Шарик владелец одобрил ещё для полива, и это не «плашка»: он
		 * недолгий и ничего собой не закрывает.
		 */
		function skazatVPalatke(w, text) {
			try { notify.info(text); return true; } catch (e) { return false; }
		}

		function fillOffer(pid) {
			var w = stallWindow();
			if (!w) return;
			var d = w.document;
			var box = d.getElementById('anzahl_' + pid);
			var zapas = box ? parseInt(box.value, 10) : 0;
			if (!(zapas > 0)) return;

			skazatVPalatke(w, 'Смотрю цену на рынке…');
			cheapestOnMarket(pid).then(function (best) {
				var w2 = stallWindow();
				if (!w2) return;
				var d2 = w2.document;
				var price = askingPrice(w2, pid, best);
				if (price === null) {
					skazatVPalatke(w2, 'Игра не назвала допустимую цену для этого товара.');
					return;
				}
				var a = d2.getElementById('produkt_anzahl');
				var p1 = d2.getElementById('produkt_preis1');
				var p2 = d2.getElementById('produkt_preis2');
				if (!a || !p1 || !p2) return;   // ценник закрыли — не мешаем
				var parts = priceParts(price);
				a.value = String(zapas);
				p1.value = parts.whole;
				p2.value = parts.cents;
				skazatVPalatke(w2, 'Поставил весь запас: ' + zapas + ' шт. по ' +
					formatPriceRu(price) + ' сТ за штуку. Осталось нажать ' +
					'«предложить на рынке».');
			}, function () {
				var w3 = stallWindow();
				if (w3) skazatVPalatke(w3, 'Рынок не ответил — цену не узнал.');
			});
		}

		/*
		 * Подвешиваемся к товарам в палатке.
		 *
		 * Каждый шаг продажи ПЕРЕЗАГРУЖАЕТ страницу палатки, поэтому метку
		 * ставим на сам документ: после перезагрузки она пропадёт вместе с
		 * ним, и мы подвесимся заново. Слушаем всплытие, а не перехват:
		 * сперва пусть отработает игра (её обработчик очищает поля), и
		 * только потом заполняем.
		 */
		function hookStall() {
			var w = stallWindow();
			if (!w) return;
			var d = w.document;
			try {
				if (d.__siPalatka) return;
			} catch (e) { return; }

			var divs = d.querySelectorAll('div[id]'), i, naydeno = 0;
			for (i = 0; i < divs.length; i++) {
				var m = /^p(\d+)$/.exec(divs[i].id);
				if (!m || !d.getElementById('anzahl_' + m[1])) continue;
				(function (tile, pid) {
					tile.addEventListener('click', function () { fillOffer(pid); });
				})(divs[i], parseInt(m[1], 10));
				naydeno++;
			}
			// Метку ставим ТОЛЬКО если было к чему подвешиваться. Иначе
			// страница, ещё не дорисовавшая товары, помечалась как готовая,
			// и обработчиков не появлялось уже никогда: метка есть, а
			// нажатия по товару никто не слушает. Поймано на стенде
			// 2026-09-10 — рынок молчал, хотя гном отработал.
			if (!naydeno) return;
			try { d.__siPalatka = true; } catch (e) { return; }
			skazatVPalatke(w, 'Нажмите товар — подставлю количество и цену сам.');
		}

		function runSelling() {
			// Молча глотать нажатие нельзя: человек жмёт ещё раз и решает,
			// что помощник сломался.
			if (running) { notify.info('Ещё работаю, подождите…'); return; }
			busy(true);
			notify.wait('Открываю палатку…');
			openStall().then(function () {
				notify.hide();
				hookStall();
			}, function (err) {
				notify.error(err && err.message ? err.message : 'Не получилось открыть палатку.');
			}).then(otpustit, otpustit);   // отпускаем в ЛЮБОМ случае, см. `otpustit`
		}

		var btnWater = gnomeButton('water', 'pics/verkauf/kannenzwerg.gif', 25, 45,
			'Полить всё', runWatering);

		/*
		 * ═══ ЖУК ДОЛЖЕН БЫТЬ ОДИН ═══════════════════════════════════
		 *
		 * У игры ЕСТЬ свой автомат посадки — `#wimpareaAutoplant`, она рисует
		 * его фоном в верхней строке (проверено на живой игре 2026-09-11:
		 * `onclick="gardenjs.autoplantOpen('v')"`). Пока мы рисовали такого
		 * же своего ниже, у игрока было ДВА жука — он прислал снимок и
		 * сказал: «должен быть один, у гнома сверху».
		 *
		 * Решение: разметку игры НЕ ТРОГАЕМ (её обработчик, её картинка), а
		 * свою кнопку превращаем в ПРОЗРАЧНУЮ НАКЛАДКУ ровно поверх её жука.
		 * Видимый жук один — её; нажатие ловим мы и сажаем своим способом,
		 * бесплатно. Платный автомат игры работает на ПОКУПАЕМЫХ зарядах, и
		 * тратить их молча нельзя — накладка до него нажатие не пускает.
		 *
		 * Нет у игры своего жука (бывает на других уровнях) — рисуем своего,
		 * как раньше: возможность посадки не должна пропадать.
		 */
		var PROZRACHNAYA = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

		function zhukIgry() {
			var e = document.getElementById('wimpareaAutoplant');
			if (!e) return null;
			var r = e.getBoundingClientRect();
			return (r.width && r.height) ? e : null;
		}

		var btnPlant = gnomeButton('plant', 'pics/verkauf/anpflanzautomat.gif', 45, 45,
			'Посадить выбранные семена', runPlanting);

		// Гном с фонарём: фонарь и есть «подсветить сад». Прежде тут стояло
		// солнце — единственный не-гном в ряду, и оно выбивалось.
		// Рыночная площадь — картинка самой игры из города. Гнома-торговца
		// у игры нет: в `pics/verkauf/` живут только лейка, автомат, жнец и
		// исследователь (проверено запросами 2026-09-10).
		var btnSell = gnomeButton('sell', 'pics/stadt/marktplatz_neu.png', 38, 45,
			'Продать на рынке', runSelling);

		// Жнец с косой — её же картинка. Рисуется ТОЛЬКО когда жнец игры не
		// нанят: иначе в полосе стояло бы два жнеца.
		var btnHarvest = gnomeButton('harvest', 'pics/verkauf/sensenzwerg.gif', 45, 45,
			'Собрать урожай', runHarvesting);

		// Гном-ускоритель: обойти все сады разом.
		var btnRound = gnomeButton('round', 'pics/wassergarten/boosterzwerg_klein.png', 25, 45,
			'Обойти все сады: собрать, посадить, полить', runRound);

		/*
		 * ГНОМ ПОЧТЫ — ЕДИНСТВЕННАЯ НАША КАРТИНКА В РЯДУ.
		 *
		 * У почты в игре нет гнома: её персонаж — летун в 335 px верхом на
		 * птице, в ряд он не встаёт, а иконка почты 40×36 висела среди гномов
		 * табличкой («чтобы не табличка висела, а сидел гном» — владелец,
		 * 2026-09-18). Гнома-почтальона нарисовал владелец 2026-09-19 по
		 * мотивам летуна: лётный шлем, очки, рюкзак с письмами, посох. Он
		 * лежит внутри программы (30×45, PNG, ~3,4 КБ), а не грузится с
		 * сервера игры, как остальные, — это наш рисунок, а не её.
		 */
		var POCHTA_GNOM = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAB4AAAAtCAYAAAC53tuhAAANMklEQVR42q2YaZBc1XXH//ct/V6/3qZ7lp6le2a0MUIjJNBKSUiCwcihiGMSESDYGNvBoMR24gRSODGGYMcpB1OucmKbkosPthM5SZkiyCQxGLFoAbFomZFmn9FM9/T0Mj29vdf9+u335oPLCjJSLLlyvt5b/1+dc0+de84h+C2slM8dPD0+sy83d1roDZs8cy1IAoEsKyCCBFDZjXb1CbrhPnDdrjt+fCkNcrXQ5547cKCwpN67stUOb17Js6BMiWk4cG0PjgfYDoNat1i9YXtF1Vs2eOXbX/ybp5/5dR3+aqCPP/7Xz+bz2c+kFgpKf4yxNXGe2JYF03Rg2TYM20a1Wke5opF8sUryy1pId8S1e2//Xfb6G2+++0Et7kqhX//6V7+9XCruVzXVx/MubVoOKatNmKaNZtOCbVpoqA2YhgnXdUDBSNO0vPPz872mYzw2Oz35pd8KnM8X/qJaKjHXMqlPkrimy8F0GARCQagLnnkQQAHmwbZc2LYDz/P4bGHRqVYrXdMzMw9+UE+4EuiXv/zI/tOn3m/IvBj0iwQtPg8tMgPPKCzLQU3T0dRNqIaLXMlARTOxrFrQDBu2S7nFdIq9c+J49arATz/99IZ6vfKs4xjw88DKrgARaQWlVBrDRR6W6yFT0tGwPJQ0C6ruomFaUBsmCPll7kpqhRTzGf6qwLlcyqtVa55pmvzOdT1YEQVeP3Ee85kKZFlG3fRQqTdBCQUDAWMA9VyIoghe8IGBwTJN1BsNXBWYUkqo5/EcB6xJtuDIu9NIn29g1+5bsXLjBlRqKnJzMxgbn0C5VAIDQUu8G919/aCCjKnpGch+CYmevqsDex5vK8EIwooCjwHlio6dO7finvvvg8EoxkdOomf7FmzavAWnT76L/hWrse2WWzF87gzm5iah63H4pBZE2xNXDmaMDR47/vq/HTjwfeqXFY4CgCBh1cYbMDkxgsmxERiWCdMBQHyItXXgI3tvxeG3TyA3OwzOVrG2uxUkmEDTsC4PfvPoL/6+VBsZHD92nOnZOvmHJx/oDvf3rKdQmcRROLaLklaHphsQiIx8TQXjXKzsWQGRV5DKFZDNLsA1mnD8EpLtccRCSTpboSybS3mXLJkv/+JnT55657VHxyZGg8lEGElJxlKhjGqI0FKJcu1KKwbagBcOncH1mzfhplt2wdA1uEYNcrgN5xeKWBofRX9HO6Kr12DRqKBNotTzFK5j1UZ093SO7dy5Z/2HPB4bPvbwyPA7wVKhbBtmJ1+PBREBI3rB5JosirbuXhQL5xCTZPDLy1g4Owou0gLbspAeeR+e3sDvbRxEcTGPkZOn4e/uoF3bt3GlamMsHI29uGPH7uwlQz184pVSJmd2JuIdglqtc2eXNHR3RKDqNvxtPoR9FNN5DUIghGRvP0TDwvT8WdQ5AW2JJNYODsInEYRaGuAhUsb5mUWlV7fu3vmdDesG/uvX8+cCuG/1hohmp0i11mCCwKHZNHF6PIdwNIjBTooA1aA3LIR61qBvzxBiHIN0+gx8HZ248c7fh2M7eP6HzyFMBdx65z62mC9wqfn5Y5pujF4qcS+Ar9/+0ZMUL7dmUyl/erHJeJ9I+mMcnLoBXVNRVwnKpoE969dh1eAGqHUNa3r6kejpQbVcwszEONq6emA4LgqlMh8Mhdx4Z+fXDMNIAHj4suC77rp/3+Ytg+87DtkieI63fVuSHxhMYGIij5FTCzhSy0Izgc6ebjiejXh7O2S/jFK5gtmJcSxPj8OoqQjG48hmFkDBIZnooaFwuPl/erx//91n5KBz3dmTc5Sj4Fvbw1ACCuJtAXS3ipgrNhBSWuH3K6DUg+s60HWK4lIBciiMobs/gUq5hJOnzyDR1oZSqQSO5zlBEITLgv9k/x+mgxEukUhGufPjIgKiApEjyC4uQ63p0HQTyd5WzM1XcW50FF3d3bBNG67rob2tDX5FgcALgCAi3tWFcrnsUUr5Wq32ZH9//w8vBeYAYKlW6FXrGvfG4QlYho3eFe0wPIrp2TxSmSpKTYqWthAoozh67AiGR0awXCqhXq+DcAQcIcjlcwADDMNANBpljm2S0dHhk7t27Vq4FJgHgD0373giPbtIitkyBlbHEZAFuKYN03IxPl+EoZnoDIvQbQ/ZhQV4DOho70QwFITnURAQmKaJQj4PQoBGo45XDx9marXW+8B99zpvHDl27pIeZ+aKXLWoIxpWIPh9IKIAiwLVJRV2QQXleaTSVYSCMr7wp3+MvvYwRs68h/n0AjRNg6qqkCUZ1WoFmqbh1Vdf5dRy2uvq4Hd70G+8bKhL5SKo50L2SyCMQeA4KJUmQucr6Gx6EB0XZYeBwMUf3b0PD3zyHlx3TRIL0+fw9tvHsVwuw7ZtCKIPR46+gZmJUwiHRL4lyrsCygNf+fO7d18SvHZ9gkVaA2joJggF9LllhMeWcBORMSj70a0aWNfdig3XrYdhmFizdg3u2LsH1KzgtVcO4e2jb+LIsTdxfm4cqfQo2lt4VtNMUlVN4rr6bcGQ9J2Xn/3mtg99Eg8+dAszbQ7pyQIIo0iWLaxoeOgISLAA1FujWP+RrUjuvgn9AwPQlrN4/+QIjr53AjNTkzA0Dw710J6IoL0rhuxsFoWyhXhXDIMDne7m/lVCXOma9cv+j93y8KOTFzyWJEW3TZd2JFrRWFaRKlYxzSjOVOuwwxEM3X07brrhepCFRaQnJjE6Oou33nsHpVoBawZX4NqBNqyKcEgkOtCsNWEzoSH6lEa5arsxNyaEGrJXzORW2Vrj59987KHeC+Ctm4cGcovGHCcKaEm0UT0o4bytY95uokwo/NEocloDnQEZ8vgEnv+Xf8Xo9DR8oghBAKLxGIRYK1KzORQnF7Gu79p7H3zoCwNN3XzJBx6LpRx74dhRjE1MJRbHT49fKCCf/vQj2bvuvdn0STzjCIEpC6B+H+rNJoJ6FXo+j3BnHOm5BWRqZdS1Khr5ZdTrDfAhATUQmJYHowkgKNNTqYkfnJib2Ld9+47PNw1TSxXT9+t61cvX68LGG3YJeOnk/5bM8lI5RjxCrIblgOcFMI8Ld8TQfu11IH0JzA6PIpNbwrmlLLiwjHigC0pIhqM3Ydd1RNck4VV0+BQJuUKtW/BY7ODBg+/sXtueWb1iJbdjz20ZH1EejHT1kYtqdTgc+3xuLnfA8WgHIQR+H3Dz7i343Oe+CL/IoarVIUohmNoSqMehO96BaGsItcwSKo0mAgEFgUgAtWoTHbEgY5S4ALA6EYsGgv7ha7d/dP99n7rv3Q+NMIcOHXkxGAk+KgWCj1Nwx5RQgDYsjU5ODYPnRcT6+5BtLIFTOLQE/YgoPvgBqDULBZ1BAEMgKCMSCyAUDdBwaxgA0BFrkZMREhcyP0tettl74/iZfwaA7Ws74zES3JU+e9b58Vyay/7OAkzbQCY/g2BYRjQQoT5FZM18hctmyqRoUZiVGnwCD5/twCnVeMP5ZTlmhH9JksT11Wrz2Re/8Qnc+ZWDz192TN2SjPTaTWdlrW63m02LjMxMk1J2HrzHEBQIXMsijmFxi+kiyeRrcDwPnGWh0XBRWKyA1htvUtP96XShtvTWWGaqtSXkEMe4LxDpGHrx6Mi3LttXHzqTPdC+ap1PEd1/lDnPFXQq+LQqGGFscYYSmefPSxyt5F1fr0qkuMiBzTsi8akmBE4gnRt3fPaVH/176ld69YYabIQ6HY3reOU3NvStClUkxY9IWxDUo1haLsGxHVqrGXwo1val+bGx/2ztij8liOITgZagpwQg9K/sRbNuopXQ0Ae1rkl29lUM79yfPfXMJ38j2DQ1Ck4GYwEEggFIsoTZkWn4JQlBkeMBIOjzEc91US+WQcIBjJcmobk6OrtVemF18djH/65Qqv7lXJ3fc0WDeWom9614rOuvapkyn5lIedOjU6xhGvw116y569zwuUMAkE5nnggFg181myavqpoXjsvYuW0Ldm7dekFnanYh5nniqRd+fvytKx/aKE1FY2EihhhbLbfj3fdmMDU7NXXRpmCpkOZFjsS6QmzTpmvgGD4ognghaSnzONe1tcs2e5cyRVEUFyZ4PwUEHiCA4lf8FwkQQfKYB8nvQzioYL6oQZCdyq/O5/MaC0c44ZL/8eWsstyoGQ0TPKM4P12A51JIkkQ/eCfUEaBKi+zaluM1DQ/7/uB2FhDp1wB0AUC96Qg13WJXDB4aGtrkkea+zp4Y/fjH7uB4woFSSh2Ci0Rc05I4xgTLdqXJqQw17aa3Z2jrZ94+9ZO/ZWwx6RpmU+AIrhgsit4d23as/9Rn999Db7ttiNu790aEAgGOc72LwubVvRlY7AWeE/57dnKB++4//UiYm8lgdmrhoe8d/MkmQuDygi9yxW/caFYNv7zaI66E//jpSwhJCosFW97nPb960Y6kUjkM4DByJfT3JV+r5BviN576vgdGCO+XavvvHPqBSfE8Xhu+spXihg3rH3Ed9xnLshxBEERRFK3RsXEZ/0/2PwdGvGFzPelbAAAAAElFTkSuQmCC';

		var btnPost = gnomeButton('post', POCHTA_GNOM, 30, 45,
			'Птичья почта: забрать награды, покормить и разослать птиц', runPost);

		// Пасека — гном-пчеловод самой игры (`beekeeper.png`, 400×400 → 45×45):
		// у неё он платный помощник, у нас — кнопка «отправить вернувшихся».
		var btnBees = gnomeButton('bees', 'pics/bees/beekeeper.png', 45, 45,
			'Пасека: отправить вернувшиеся пчелиные семьи, разлить полные соты', runBees);

		var btnPaint = gnomeButton('paint', 'pics/wassergarten/questzwerg_klein.png', 30, 45,
			'Подсветить сад: синий — полить, зелёный — созрело, чёрный — пусто', function () {
			var n = paint(!painted);
			if (n === false) return;
			if (painted) notify.info('Синий — полить, зелёный — созрело, чёрный — пусто.');
			else notify.hide();
		});

		/*
		 * ═══ САДЫ: ПЕРЕКЛЮЧАТЕЛЬ, КАК У ИВАНА ══════════════════════
		 *
		 * У Ивана (`functions/gardenButtons.js`, 5.2) это выглядело так:
		 * иконка на КАЖДЫЙ уже купленный сад, картинками самой игры
		 * (`pics/garten/garten<N>.jpg`, 16×16), клик — `waehleGarten(N)`.
		 * Первой строкой он проверял, нет ли у игры своих таких кнопок,
		 * и тогда не делал ничего. Повторяем и это.
		 *
		 * ВАЖНО ПРО «ПЯТЫЙ САД». Иван НИЧЕГО не открывал: он лишь рисовал
		 * кнопку тому саду, который у игрока уже есть. Купить сад можно
		 * только в самой игре, на карте города, за игровые деньги — со
		 * стороны браузера этого не обойти, и мы не делаем вид, что можем.
		 * Если сад не куплен, кнопки на него не будет и у нас.
		 */
		function currentGarden() {
			try {
				if (window.gardenjs && gardenjs.getCurrentGarden) {
					var n = parseInt(gardenjs.getCurrentGarden(), 10);
					if (n > 0) return n;
				}
			} catch (e) {}
			return null;
		}

		// Список садов, которыми игрок ВЛАДЕЕТ. `null` — «не знаем».
		// Не знаем — значит не выдумываем: пустой ряд честнее выдуманного.
		function ownedGardens() {
			var list = [], i;

			// 1. Быстрая навигация самой игры. Данные в ней есть даже тогда,
			//    когда рисовать их некуда: игра кладёт `quicknavi.data` до
			//    вызова `build()`, а `build()` молча выходит, если на
			//    странице нет блока `#quicknavi`. Это и есть тот самый случай.
			try {
				var q = window.quicknavi;
				if (q && q.data) {
					for (var k in q.data) {
						var m = /^garden(\d+)$/.exec(k);
						if (!m) continue;
						var n = parseInt(q.data[k], 10);
						if (n > 0 && list.indexOf(n) === -1) list.push(n);
					}
				}
			} catch (e) {}
			if (list.length) { list.sort(function (a, b) { return a - b; }); return list; }

			// 2. Карта города, если игрок её открывал: `garden_count` —
			//    сколько садов куплено, `garden_max` — сколько бывает всего.
			try {
				var c = window.citymap;
				if (c && c.data && c.data.garden_count) {
					var cnt = parseInt(c.data.garden_count, 10);
					if (cnt > 0) {
						for (i = 1; i <= cnt; i++) list.push(i);
						return list;
					}
				}
			} catch (e) {}

			return null;
		}

		// Игра уже показывает свои кнопки садов — второй ряд не нужен.
		function gameHasGardenButtons() {
			var own = document.querySelectorAll('#quicknavi .quicknavi-garden');
			return own.length > 0;
		}

		/*
		 * ГДЕ ОН СТОИТ: в правой рамке игры, столбиком — как было у Ивана.
		 *
		 * У игры есть узкая полоса у самого правого края: `#asd`, 20 px в
		 * ширину и 640 в высоту (её собственный `style.css`). Иван вешал
		 * сады именно туда, начиная с `top:250px` — ниже картинки рамки
		 * (`.rahmen-hoch-rechts`, 190 px). Игрок помнит их «сбоку справа»,
		 * и это ровно оно.
		 *
		 * В разметку игры мы НЕ вставляемся: столбик висит поверх страницы
		 * и лишь равняется по `#asd`. Сломать вёрстку игры так невозможно, а
		 * не найдётся полосы — встанем у правого края поля.
		 */
		var gardens = document.createElement('div');
		gardens.id = 'si-helper-gardens';
		// Метка своя, не гномья: сады — навигация, а не помощник.
		gardens.setAttribute('data-si-gardens', '1');
		gardens.setAttribute('style', [
			'position:fixed', 'z-index:99999', 'pointer-events:auto',
			'display:none', 'line-height:0'
		].join(';'));

		var GARDEN_ICON = 16;    // родной размер картинки, как у Ивана
		var GARDEN_PAD = 2;      // поле вокруг: 16 + 2 + 2 = ровно ширина полосы
		var GARDEN_BOX = GARDEN_ICON + GARDEN_PAD * 2;
		var GARDENS_TOP = 250;   // отступ сверху внутри рамки — число Ивана

		function switchGarden(n) {
			try {
				if (typeof window.waehleGarten === 'function') { window.waehleGarten(n); return true; }
				if (window.gardenjs && gardenjs.change) { gardenjs.change(n); return true; }
			} catch (e) {}
			notify.error('Не получилось переключить сад.');
			return false;
		}

		function gardenIcon(n, current) {
			var a = document.createElement('img');
			a.src = gfx('pics/garten/garten' + n + '.jpg');
			a.alt = 'Сад ' + n;
			a.title = n === current ? ('Сад ' + n + ' — вы здесь') : ('Перейти в сад ' + n);
			// Свой признак: в столбике теперь не только сады, и отличать их
			// по «картинка без метки города» стало неверно.
			a.setAttribute('data-si-garden', String(n));
			a.setAttribute('style', [
				'width:' + GARDEN_ICON + 'px', 'height:' + GARDEN_ICON + 'px',
				'padding:' + GARDEN_PAD + 'px', 'box-sizing:content-box',
				'cursor:pointer', 'display:block',
				'border-radius:4px', 'transition:transform .1s',
				n === current ? 'background:rgba(255,255,255,.35)' : 'background:none',
				'filter:drop-shadow(0 1px 2px rgba(0,0,0,.55))'
			].join(';'));
			a.onmouseenter = function () { a.style.transform = 'scale(1.15)'; };
			a.onmouseleave = function () { a.style.transform = ''; };
			a.onclick = function () { if (n !== current) switchGarden(n); };
			return a;
		}

		// Иконка «спросить игру, какие сады есть». Появляется, только если
		// список неоткуда взять. Нажатие — единственное, что шлёт запрос:
		// сама по себе панель на сервер не ходит (АС-7 контракта).
		function askIcon() {
			var a = document.createElement('img');
			a.src = gfx('pics/garten/garten_stadt.jpg');
			a.alt = 'Сады';
			a.title = 'Показать мои сады';
			a.setAttribute('data-si-ask', '1');   // свой признак, как у садов и мест
			a.setAttribute('style', [
				'width:' + GARDEN_ICON + 'px', 'height:' + GARDEN_ICON + 'px',
				'padding:' + GARDEN_PAD + 'px', 'box-sizing:content-box',
				'cursor:pointer', 'display:block',
				'filter:drop-shadow(0 1px 2px rgba(0,0,0,.55))'
			].join(';'));
			a.onclick = function () {
				if (!window.ajax || !ajax.request) {
					notify.error('Игра не отвечает — не могу узнать список садов.');
					return;
				}
				notify.info('Спрашиваю игру про сады…');
				try {
					ajax.request('ajax', { 'do': 'citymap_init' }, function (r) {
						var d = r && r.data;
						var cnt = d ? parseInt(d.garden_count, 10) : 0;
						if (!(cnt > 0)) { notify.error('Игра не сказала, сколько у вас садов.'); return; }
						asked = [];
						for (var i = 1; i <= cnt; i++) asked.push(i);
						notify.info('Садов у вас: ' + cnt + '.');
						buildGardens(true);
					});
				} catch (e) {
					notify.error('Игра не отвечает — не могу узнать список садов.');
				}
			};
			return a;
		}

		/*
		 * ОСТАЛЬНЫЕ КНОПКИ СТОЛБИКА — как у Ивана: рынок и карта города, а с
		 * 3.7.0 ещё грибы и улитки (их владелец просил отдельно; в карточке
		 * пропавшего расширения они тоже были).
		 *
		 * Точки входа и КАРТИНКИ взяты у самой игры, из её быстрой навигации
		 * (`quicknavi.prototype.build` и её же таблица стилей):
		 *   грибы   `megafruit`    → `initMegafruit()`,
		 *                            `pics/pilzzucht/pilzgarten_premiumwahl.jpg`
		 *   улитки  `snailracing`  → `snailracing.init()`,
		 *                            `pics/snailracing/SchnellreiseIcon_02.gif`
		 *   рынок   `city1`        → `zeigeStadtMain(1)`
		 *   карта   `city2`        → `zeigeStadtMain(2)`
		 * Игра перед входом зовёт `setLocation('<ключ>')` — повторяем и это,
		 * иначе она не будет знать, где мы находимся.
		 *
		 * Русские подписи — её собственные, из `location_name`: «Выращивание
		 * грибов» и «улиточные бега».
		 */
		function navIcon(key, path, title, gap, action) {
			var a = document.createElement('img');
			a.src = gfx(path);
			a.alt = title;
			a.title = title;
			a.setAttribute('data-si-nav', key);
			a.setAttribute('style', [
				'width:' + GARDEN_ICON + 'px', 'height:' + GARDEN_ICON + 'px',
				'padding:' + GARDEN_PAD + 'px', 'box-sizing:content-box',
				'cursor:pointer', 'display:block',
				'border-radius:4px', 'transition:transform .1s',
				'filter:drop-shadow(0 1px 2px rgba(0,0,0,.55))',
				gap ? 'margin-top:8px' : 'margin-top:2px'
			].join(';'));
			a.onmouseenter = function () { a.style.transform = 'scale(1.15)'; };
			a.onmouseleave = function () { a.style.transform = ''; };
			a.onclick = action;
			return a;
		}

		function cityIcon(which, title, first) {
			var a = navIcon('city' + which, 'pics/garten/garten_stadt.jpg', title, first, function () {
				try {
					if (typeof window.zeigeStadtMain === 'function') {
						goTo('city' + which);
						window.zeigeStadtMain(which);
						return;
					}
				} catch (e) {}
				notify.error('Не получилось открыть город.');
			});
			a.setAttribute('data-si-city', String(which));
			return a;
		}

		// Игра держит текущее место сама; без этого она считает, что мы всё
		// ещё в саду, и её собственные кнопки начинают врать.
		function goTo(key) {
			try {
				if (typeof window.setLocation === 'function') window.setLocation(key);
			} catch (e) { /* не вышло — не беда, вход всё равно откроется */ }
		}

		/*
		 * Есть ли у игрока эта локация.
		 *
		 * Спрашиваем ТОЛЬКО быструю навигацию игры: в ней перечислено то, что
		 * игроку доступно. Рисовать кнопку на неоткрытое нельзя — нажатие
		 * привело бы в лучшем случае в никуда, а в худшем в платное окно.
		 * Ровно на таком в проекте уже был инцидент с диалогом сноса.
		 * Ноль в значении игра тоже считает «нельзя» (у себя рисует такую
		 * иконку классом `no-entry` и без действия).
		 */
		function hasLocation(key) {
			try {
				var q = window.quicknavi;
				return !!(q && q.data && q.data[key]);
			} catch (e) { return false; }
		}

		var MESTA = [
			{ key: 'birds', title: 'Птичья почта',
			  path: 'pics/birds/Vogelposticon01.gif',
			  go: function () { if (post()) { goTo('birds'); post().init(); return true; } return false; } },
			{ key: 'megafruit', title: 'Выращивание грибов',
			  path: 'pics/pilzzucht/pilzgarten_premiumwahl.jpg',
			  go: function () { if (typeof window.initMegafruit === 'function') { goTo('megafruit'); window.initMegafruit(); return true; } return false; } },
			{ key: 'snailracing', title: 'Улиточные бега',
			  path: 'pics/snailracing/SchnellreiseIcon_02.gif',
			  go: function () { if (window.snailracing && typeof window.snailracing.init === 'function') { goTo('snailracing'); window.snailracing.init(); return true; } return false; } }
		];

		function placeIcons() {
			var out = [], first = true;
			for (var i = 0; i < MESTA.length; i++) {
				(function (m) {
					if (!hasLocation(m.key)) return;
					out.push(navIcon(m.key, m.path, m.title, first, function () {
						if (!m.go()) notify.error('Игра не открывает: ' + m.title + '.');
					}));
					first = false;
				})(MESTA[i]);
			}
			return out;
		}

		var asked = null;    // что ответил сервер на явное нажатие
		var gardensKey = '';

		function buildGardens(force) {
			if (gameHasGardenButtons()) {          // у игры свои есть — молчим
				if (gardens.style.display !== 'none') { gardens.style.display = 'none'; gardensKey = ''; }
				return;
			}
			var list = ownedGardens() || asked;
			var current = currentGarden();
			// В приметку входят и доступные места: игра досылает свою быструю
			// навигацию ответом сервера, и столбик должен пересобраться, когда
			// грибы или улитки в ней появятся.
			var dostupno = [];
			for (var m = 0; m < MESTA.length; m++) {
				if (hasLocation(MESTA[m].key)) dostupno.push(MESTA[m].key);
			}
			var key = (list ? list.join(',') : 'sprosit') + '|' + current + '|' + dostupno.join(',');
			if (!force && key === gardensKey) return;
			gardensKey = key;

			gardens.innerHTML = '';
			if (!list) {
				gardens.appendChild(askIcon());
			} else {
				for (var i = 0; i < list.length; i++) {
					gardens.appendChild(gardenIcon(list[i], current));
				}
			}
			var mesta = placeIcons();
			for (var k = 0; k < mesta.length; k++) gardens.appendChild(mesta[k]);
			gardens.appendChild(cityIcon(1, 'Рынок', true));
			gardens.appendChild(cityIcon(2, 'Карта города', false));

			// сады + доступные места + две кнопки города
			var count = (list ? list.length : 1) + mesta.length + 2;
			gardens.setAttribute('data-si-w', String(GARDEN_BOX));
			gardens.setAttribute('data-si-h', String(count * GARDEN_BOX));
			gardens.style.display = 'block';
			placeGardens();
		}

		/*
		 * Столбик равняется по правой рамке. Ставим его по центру полосы:
		 * иконка с полями ровно 20 px, столько же и полоса, — сойдётся впритык,
		 * как у Ивана. Рамки нет — идём к правому краю игрового поля, а нет и
		 * его — к краю окна: пропасть столбик не должен.
		 */
		function placeGardens() {
			if (gardens.style.display === 'none') return;
			var w = gardens.offsetWidth || GARDEN_BOX;
			var h = gardens.offsetHeight || GARDEN_BOX;
			var left = null, top = null, r;

			var frame = document.getElementById('asd');
			if (frame) {
				r = frame.getBoundingClientRect();
				if (r.width) { left = r.left + (r.width - w) / 2; top = r.top + GARDENS_TOP; }
			}
			if (left === null) {
				var field = document.getElementById('garten') || document.getElementById('garden');
				if (field) {
					r = field.getBoundingClientRect();
					if (r.width) { left = r.right + 4; top = r.top + 40; }
				}
			}
			if (left === null) { left = window.innerWidth - w - 6; top = 120; }

			// Внутрь окна: столбик длинный, а окно бывает низким.
			left = Math.max(2, Math.min(left, window.innerWidth - w - 2));
			top = Math.max(2, Math.min(top, window.innerHeight - h - 2));
			gardens.style.left = Math.round(left) + 'px';
			gardens.style.top = Math.round(top) + 'px';
		}

		bar.appendChild(btnWater);
		bar.appendChild(btnPlant);
		bar.appendChild(btnRound);
		bar.appendChild(btnPaint);
		bar.appendChild(btnSell);

		/*
		 * Своя кнопка сбора появляется и пропадает по ДАННЫМ игры: жнеца
		 * можно нанять и можно лишиться, и решать это надо каждый круг, а
		 * не один раз при запуске.
		 */
		// Кем сбор уже закрыт: в обычном саду — жнецом игры, в водном — его
		// собственной кнопкой «собрать всё».
		function harvestCovered() {
			return inWaterGarden() ? wgHarvestAllReady() : gameHarvestHired();
		}

		function syncHarvestButton() {
			var нужен = !harvestCovered();
			var есть = !!btnHarvest.parentNode;
			if (нужен && !есть) bar.appendChild(btnHarvest);
			else if (!нужен && есть) btnHarvest.parentNode.removeChild(btnHarvest);
		}

		// Гном почты — только тому, у кого почта есть: правило то же, что у
		// столбика мест, источник тот же — быстрая навигация игры.
		function syncPostButton() {
			var нужен = !!post() && hasLocation('birds');
			var есть = !!btnPost.parentNode;
			if (нужен && !есть) bar.appendChild(btnPost);
			else if (!нужен && есть) btnPost.parentNode.removeChild(btnPost);
		}

		function syncBeesButton() {
			var нужен = !!bees() && hasLocation('bees');
			var есть = !!btnBees.parentNode;
			if (нужен && !есть) bar.appendChild(btnBees);
			else if (!нужен && есть) btnBees.parentNode.removeChild(btnBees);
		}
		/*
		 * Убираем следы прежней панели, если код вставили в страницу заново.
		 * Наши элементы живут ОТДЕЛЬНО от `#si-helper` (столбик мест, листок
		 * рынка), и защита «панель уже есть» их не покрывает: получались
		 * два столбика, из которых верхний — мёртвый. Поймано на живой игре
		 * 2026-09-10, той же породы, что и задвоенный листок рынка.
		 */
		(function () {
			var lishnee = document.querySelectorAll('#si-helper-gardens, #si-helper-market');
			for (var i = 0; i < lishnee.length; i++) lishnee[i].remove();
		})();

		(document.body || document.documentElement).appendChild(bar);
		(document.body || document.documentElement).appendChild(gardens);
		buildGardens(true);

		function refreshPlantButton() {
			/*
			 * Своего выбора семян у нас нет — и не должно быть: у Ивана
			 * никаких плашек на экране не висело. Семена выбираются на полке
			 * игры, как обычно, а автомат сажает то, что выбрано.
			 */
			var pid = currentSeed();
			var ok = !!pid;
			btnPlant.disabled = !ok;
			// Прозрачность НЕ трогаем. Раньше жук гасился до 45% и на траве
			// попросту исчезал — владелец так его и не нашёл. Помощник должен
			// быть виден всегда, а причину он скажет шариком при нажатии.
			btnPlant.title = ok
				? ('Засеять свободные клетки: ' + seedName(pid))
				: seedHint();
		}

		function currentSeed() {
			var pid = parseInt(window.selected, 10);
			if (!pid) return 0;
			var d = window.data_products && window.data_products[pid];
			if (!d || !d.plantable) return 0;
			/*
			 * В обычном саду сажают овощи ('v'), в водном — водные растения
			 * ('w'). Именно на этом помощник и спотыкался: полка водного сада
			 * выкладывает 'w', проверка ждала 'v', и жук отвечал «сначала
			 * выберите семена», хотя они были выбраны.
			 * Украшения ('wd') не наше дело: их ставят по одному и по месту.
			 */
			if (d.category !== (inWaterGarden() ? 'w' : 'v')) return 0;
			try {
				if (window.regal && window.regal.getCount(pid) < 1) return 0;
			} catch (e) { }
			return pid;
		}

		// Почему жук отказывается: человеку нужна причина, а не «нельзя».
		function seedHint() {
			var pid = parseInt(window.selected, 10);
			var d = (pid && window.data_products) ? window.data_products[pid] : null;
			if (d && inWaterGarden() && d.category === 'v') {
				return 'Это семена обычного сада. В водном выберите водные растения на полке слева.';
			}
			if (d && !inWaterGarden() && (d.category === 'w' || d.category === 'wd')) {
				return 'Это для водного сада. Здесь выберите овощ на полке слева.';
			}
			if (d && window.regal && window.regal.getCount && window.regal.getCount(pid) < 1) {
				return 'Этих семян не осталось — выберите другие на полке слева.';
			}
			return 'Сначала выберите семена на полке слева.';
		}

		function seedName(pid) {
			var d = window.data_products && window.data_products[pid];
			return (d && d.name) ? d.name : ('#' + pid);
		}

		refreshPlantButton();
		// Полка меняется по ходу игры — переспрашиваем вместе с положением.
		setInterval(refreshPlantButton, 1000);

		// ══ ПОПРОШАЙКИ: ВЫГОДНОСТЬ ПРЯМО В ИХ ЛИСТКЕ ════════════════
		/*
		 * Так это было у CupIvan (`functions/simpList.js`): своего окна нет,
		 * выгодность дописывается в тот самый листок, где игрок и решает —
		 * отдавать товар или нет. Игрок помнит именно проценты.
		 *
		 * КАК УСТРОЕН ЛИСТОК (разобрано по `wimparea` в исходнике игры):
		 *   • данные приходят ответом на `verkaufajax` + `do:getData`;
		 *   • ИГРА ИХ КЭШИРУЕТ (`q[i].sheet`) и второй раз на сервер не
		 *     ходит. Поэтому одного перехвата ajax мало: он сработает только
		 *     на первом показе каждого попрошайки. Какой листок открыт
		 *     сейчас, знает `wimparea.show(id)` — её и слушаем тоже;
		 *   • товары игра рисует в `#wimpVerkaufProducts`, сумму — в
		 *     `#wimpVerkaufSumAmount`, и к сумме прибавляет бонусы.
		 *
		 * С ЧЕМ СРАВНИВАЕМ. С ценой самой игры (`data_products[pid].price`) —
		 * это её собственная оценка товара, по ней она считает и задания
		 * птиц (`Math.ceil(деньги / price)` в её коде). **Цен рынка здесь
		 * нет**: на странице сада их не бывает, а ходить за ними в город
		 * самим — это работа без игрока, чего мы не делаем (АС-7). Блок об
		 * этом честно пишет подсказкой.
		 */
		var WIMP_BOX = 'si-wimp';
		var wimpListTop = null;     // родное положение списка игры

		function escText(s) {
			return String(s).replace(/[&<>"]/g, function (c) {
				return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
			});
		}

		function money(x) {
			try {
				if (typeof window.formatMoney === 'function') return window.formatMoney(x);
			} catch (e) { /* формат игры недоступен — покажем просто число */ }
			return String(Math.round(x * 100) / 100);
		}

		// То же словами для шарика: `formatMoney` игры отдаёт разметку с
		// неразрывным пробелом, а шарик показывает текст как есть.
		function moneyText(x) {
			return String(money(x)).split('&nbsp;').join(' ');
		}

		/*
		 * Деньги игры словами: `formatMoney` собирает их через `number_format`
		 * с разделителями из `t_price` — [1] дробная часть, [3] тысячи.
		 * Разбираем ТЕМИ ЖЕ разделителями, иначе «2.050,03 сТ» прочиталось бы
		 * как 2,05. Не смогли — возвращаем null и честно молчим.
		 */
		function parseMoney(text) {
			if (!text) return null;
			var tp = window.t_price || [];
			var drob = tp[1] || ',', tys = tp[3] || '.';
			var s = String(text).replace(/\u00a0/g, ' ');
			var m = /[\d][\d\s.,\u00a0]*/.exec(s);
			if (!m) return null;
			s = m[0];
			s = s.split(tys).join('').split(' ').join('');
			s = s.split(drob).join('.');
			var v = parseFloat(s);
			return isFinite(v) ? v : null;
		}

		// Имя товара → его номер. Игра рисует в листке имена, а цена лежит по
		// номеру, поэтому держим обратный справочник и пересобираем его,
		// когда игра досылает товары.
		var imenaPid = null, imenaSkolko = -1;

		function pidPoImeni(name) {
			var dp = window.data_products || {};
			var n = 0, k;
			for (k in dp) n++;
			if (!imenaPid || n !== imenaSkolko) {
				imenaPid = {};
				for (k in dp) {
					if (dp[k] && dp[k].name && !(dp[k].name in imenaPid)) imenaPid[dp[k].name] = k;
				}
				imenaSkolko = n;
			}
			return imenaPid[name];
		}

		/*
		 * ══ ВЫГОДНОСТЬ В ЛИСТКЕ ПОПРОШАЙКИ ═════════════════════════
		 *
		 * Так это было у CupIvan (`functions/simpList.js`): своего окна нет,
		 * выгодность дописывается в тот самый листок, где игрок и решает —
		 * отдавать товар или нет.
		 *
		 * ЧИТАЕМ ТО, ЧТО ИГРА УЖЕ НАРИСОВАЛА, А НЕ ОТВЕТ СЕРВЕРА.
		 * Иван перехватывал `verkaufajax` + `do:getData`. На живой игре
		 * 2026-09-11 выяснилось: этот запрос НЕ ВЫЗЫВАЕТСЯ ВОВСЕ — листки
		 * приходят вместе с садом. Расчёт, ждавший ответа, не появлялся у
		 * игрока никогда. В самом листке есть всё нужное: строки вида
		 * «33 x Медуница», цвет строки (`blau` хватает / `rot` нет) и сумма
		 * в `#wimpVerkaufSumAmount` — причём УЖЕ С БОНУСОМ игры, ровно то
		 * число, которое видит человек. Номер товара берём по имени из
		 * `data_products`, цену — оттуда же.
		 *
		 * С ЧЕМ СРАВНИВАЕМ: с ценой самой игры (`data_products[pid].price`) —
		 * её собственной оценкой товара. **Цен рынка здесь нет**: на странице
		 * сада их не бывает, а ходить за ними в город самим — работа без
		 * игрока, чего мы не делаем (АС-7). Блок пишет об этом подсказкой.
		 */
		function hookWimps() {
			// Игра перерисовала список — дорисовываем своё. Наш блок лежит
			// СОСЕДОМ, а не внутри списка, иначе наблюдатель звал бы сам себя.
			var list = document.getElementById('wimpVerkaufProducts');
			if (list && !list.siWatched && typeof window.MutationObserver === 'function') {
				try {
					new window.MutationObserver(function () { drawWimpProfit(); })
						.observe(list, { childList: true });
					list.siWatched = true;
					drawWimpProfit();      // листок мог быть открыт до нас
				} catch (e) { /* без наблюдателя блок просто не появится */ }
			}
		}

		function drawWimpProfit() {
			var host = document.getElementById('wimpVerkauf');
			var list = document.getElementById('wimpVerkaufProducts');

			var old = document.getElementById(WIMP_BOX);
			if (old && old.parentNode) old.parentNode.removeChild(old);
			if (!host || !list || !list.children.length) return;

			var value = 0, known = true, missing = [], i;
			var shelf = window.regal, products = window.data_products || {};

			for (i = 0; i < list.children.length; i++) {
				var stroka = (list.children[i].textContent || '').trim();
				var m = /^(\d+)\s*[x\u0445\u00d7]\s*(.+)$/i.exec(stroka);
				if (!m) continue;
				var amount = parseInt(m[1], 10);
				var name = m[2].trim();
				var pid = pidPoImeni(name);
				var info = pid ? products[pid] : null;

				if (!info || !info.price) { known = false; }
				else { value += amount * info.price; }

				var have = 0;
				try {
					if (pid && shelf && shelf.getCount) have = shelf.getCount(pid) || 0;
				} catch (e) { /* полка молчит — считаем, что нет ничего */ }
				if (have < amount) missing.push({ name: name, n: amount - have });
			}

			// Сумма — ровно та, что видит человек: игра уже прибавила бонусы.
			var sumEl = document.getElementById('wimpVerkaufSumAmount');
			var offered = parseMoney(sumEl ? sumEl.textContent : '');
			var percent = (known && value > 0 && offered !== null)
				? Math.round((offered / value - 1) * 100)
				: null;

			var box = document.createElement('div');
			box.id = WIMP_BOX;
			box.title = 'Сравнение с ценой самой игры. Цены рынка здесь не учтены.';
			box.setAttribute('style', [
				'position:absolute', 'left:0', 'width:320px', 'text-align:center',
				'z-index:3', 'pointer-events:none'
			].join(';'));

			var html = '';
			if (percent === null) {
				html += '<div style="font:11px Verdana;color:#6a6a6a">'
					+ 'Игра не назвала цену — сравнить не с чем.</div>';
			} else {
				var good = percent >= 0;
				html += '<div class="' + (good ? 'blau' : 'rot')
					+ '" data-si-wimp="percent" style="font-size:20px;line-height:1.15">'
					+ (good ? '+' : '') + percent + '%</div>';
				html += '<div style="font:11px Verdana;color:#4a4a4a;margin-top:1px">'
					+ 'дают ' + money(offered) + ' · по цене игры ' + money(value) + '</div>';
			}
			if (missing.length) {
				var mm = [];
				for (i = 0; i < missing.length; i++) {
					mm.push(escText(missing[i].name) + ' ' + missing[i].n);
				}
				html += '<div class="rot" data-si-wimp="missing"'
					+ ' style="font-size:11px;font-family:Verdana;margin-top:1px">'
					+ 'не хватает: ' + mm.join(', ') + '</div>';
			}
			box.innerHTML = html;
			host.appendChild(box);

			/*
			 * СТАВИМ НАД СТРОКОЙ СУММЫ. Если товаров много и список до нас
			 * достаёт — приподнимаем список ровно на нехватку. Иван двигал его
			 * всегда (`top: 60px`), мы — только когда мешает, и всегда от
			 * родного положения, чтобы сдвиг не накапливался.
			 */
			var hostTop = host.getBoundingClientRect().top;
			if (wimpListTop === null) {
				wimpListTop = Math.round(list.getBoundingClientRect().top - hostTop);
			}
			list.style.top = wimpListTop + 'px';

			var sum = document.getElementById('wimpVerkaufSum');
			var sumTop = sum ? (sum.getBoundingClientRect().top - hostTop) : 310;
			var boxTop = Math.round(sumTop - (box.offsetHeight || 44) - 6);
			box.style.top = boxTop + 'px';

			var last = list.lastElementChild;
			if (last) {
				var bottom = last.getBoundingClientRect().bottom - hostTop;
				if (bottom > boxTop - 2) {
					list.style.top = Math.max(10, wimpListTop - Math.ceil(bottom - (boxTop - 2))) + 'px';
				}
			}
		}

		// ── где стоит строка кнопок ──────────────────────────────────
		/*
		 * Равняемся по игровому ряду «посадить / полить / собрать». Строка НЕ
		 * вставляется в разметку игры, а висит поверх и лишь равняется по ней:
		 * сломать вёрстку игры мы так не можем. Не нашёлся ряд — встаём в угол,
		 * чтобы кнопки не пропали совсем.
		 */
		/*
		 * ГДЕ ВСТАТЬ — СЧИТАЕМ, А НЕ ПОДБИРАЕМ.
		 *
		 * Замер живой игры 2026-09-09: в строке гномов занято почти всё —
		 * домик 0–122, сорняк 210–255, зазывала 268–318, гномы игры 370–545,
		 * машина 560–720. Цельного просвета на весь наш ряд (≈136 px) НЕТ,
		 * поэтому ряд целиком уезжал вниз, на плитку дорожки, и выглядел плохо.
		 *
		 * Решение: ставим гномов ПООДИНОЧКЕ. Жука — в просвет сразу за
		 * зазывалой (318–370, ему нужно 45), остальных — в широкий просвет
		 * 122–210. Порядок и предпочтение жука заданы владельцем.
		 */
		// На столько гному можно вылезти из просвета. Три-четыре пикселя на
		// краю соседней картинки глазом не видны, а без этого допуска
		// четвёртому не хватало ровно трёх и он уезжал вниз, на плитку.
		var FIT_SLACK = 6;

		function buttons() {
			return [].slice.call(bar.querySelectorAll('[data-si-button]'));
		}

		function sizeOf(b) {
			return {
				w: parseInt(b.getAttribute('data-si-w'), 10) || b.offsetWidth || 30,
				h: parseInt(b.getAttribute('data-si-h'), 10) || b.offsetHeight || 45
			};
		}

		function occupied(strip, s, bandTop, bandHeight) {
			var busy = [];
			var all = strip.querySelectorAll('*');
			for (var i = 0; i < all.length; i++) {
				var e = all[i];
				if (e === bar || bar.contains(e)) continue;
				var r = e.getBoundingClientRect();
				if (!r.width || !r.height) continue;
				if (r.width >= s.width * 0.9) continue;   // подложка во всю ширину — не помеха
				var top = r.top - s.top, bottom = r.bottom - s.top;
				var overlap = Math.min(bottom, bandTop + bandHeight) - Math.max(top, bandTop);
				if (overlap <= 8) continue;               // едва задевает — не помеха
				busy.push([r.left - s.left, r.right - s.left]);
			}
			busy.sort(function (a, b) { return a[0] - b[0]; });
			return busy;
		}

		function freeGaps(busy, width) {
			var gaps = [], cursor = 0, i;
			for (i = 0; i < busy.length; i++) {
				if (busy[i][0] - cursor > 4) gaps.push([cursor, busy[i][0]]);
				cursor = Math.max(cursor, busy[i][1]);
			}
			if (width - cursor > 4) gaps.push([cursor, width]);
			return gaps;
		}

		function placeInto(gaps, b, prefer) {
			// prefer — левая граница, начиная с которой просвет предпочтительнее
			// (жука владелец просил поставить сразу за зазывалой).
			var need = sizeOf(b).w;
			var order = gaps.slice();
			if (typeof prefer === 'number') {
				order.sort(function (x, y) {
					var px = x[0] >= prefer ? 0 : 1, py = y[0] >= prefer ? 0 : 1;
					return px - py || x[0] - y[0];
				});
			}
			for (var i = 0; i < order.length; i++) {
				var g = order[i];
				if (g[1] - g[0] + FIT_SLACK >= need) {
					var x = g[0];
					// Ставим вплотную: зазоры между гномами съедали место, из-за
					// которого четвёртый не помещался в строку.
					g[0] = x + need;
					return x;
				}
			}
			return null;
		}

		function placeBar() {
			var strip = document.getElementById('wimpareaDiv');
			var items = buttons();
			if (!items.length) return;

			if (strip) {
				var s = strip.getBoundingClientRect();
				if (s.width && s.height) {
					// Накладка ровно поверх полосы: дальше всё считаем в её координатах.
					bar.style.left = Math.round(s.left) + 'px';
					bar.style.top = Math.round(s.top) + 'px';
					bar.style.width = Math.round(s.width) + 'px';
					bar.style.height = Math.round(s.height) + 'px';
					bar.style.right = '';

					var gaps = freeGaps(occupied(strip, s, 0, 45), s.width);

					// Жук — сразу за зазывалой, как просил владелец.
					var barker = document.getElementById('barkerOpener');
					var prefer = null;
					if (barker) {
						var br = barker.getBoundingClientRect();
						if (br.width) prefer = br.right - s.left;
					}

					/*
					 * РАССТАНОВКА ПО ТРЕБОВАНИЮ ВЛАДЕЛЬЦА (2026-09-09):
					 *   жук — наверх, в строку гномов, сразу за зазывалой:
					 *     у него в картинке зелень, и на плитке она чужеродна;
					 *   остальные трое — вниз, на дорожку, где просторно.
					 * Сперва я перенёс наверх всех четверых — это было неверным
					 * прочтением просьбы, владелец поправил.
					 */
					var plant = null, others = [], i;
					for (i = 0; i < items.length; i++) {
						if (items[i].getAttribute('data-si-button') === 'plant') plant = items[i];
						else others.push(items[i]);
					}

					/*
					 * ЕСТЬ ЖУК У ИГРЫ — НАШ СТАНОВИТСЯ НЕВИДИМОЙ НАКЛАДКОЙ
					 * ровно поверх него и в общую расстановку не идёт. Так
					 * жук на экране один, а нажатие всё равно наше.
					 */
					var zhuk = zhukIgry();
					if (plant && zhuk) {
						var zr = zhuk.getBoundingClientRect();
						plant.src = PROZRACHNAYA;
						plant.style.width = Math.round(zr.width) + 'px';
						plant.style.height = Math.round(zr.height) + 'px';
						plant.style.left = Math.round(zr.left - s.left) + 'px';
						plant.style.top = Math.round(zr.top - s.top) + 'px';
						plant.setAttribute('data-si-nakladka', '1');
						plant = null;          // дальше его не расставляем
					} else if (plant && plant.getAttribute('data-si-nakladka')) {
						// Жук игры пропал — возвращаем свою картинку и размер.
						plant.src = gfx('pics/verkauf/anpflanzautomat.gif');
						plant.style.width = '45px';
						plant.style.height = '45px';
						plant.removeAttribute('data-si-nakladka');
					}

					/*
					 * НЕ ПОМЕСТИЛСЯ НАВЕРХУ — ИДЁТ ВНИЗ, А НЕ НА КРЫШУ.
					 *
					 * Прежний запасной путь ставил такого гнома в left:4,
					 * top:0 — это ровно крыша домика (домик занимает 0–122).
					 * Владелец прислал снимок: жук стоял на крыше и читался
					 * как второй, лишний. У кого садовых помощников куплено
					 * больше, блок гномов игры шире, просвет за зазывалой
					 * пропадает — и жук уезжал туда каждый раз.
					 */
					if (plant) {
						var x = placeInto(gaps, plant, prefer);
						if (x === null) { others.unshift(plant); }
						else { plant.style.left = Math.round(x) + 'px'; plant.style.top = '0px'; }
					}

					/*
					 * Трое гномов — на траву НАД тропинкой, правее, вровень с
					 * гномами самой игры, и с такими же просветами между собой.
					 * Так распорядился владелец: слева у домика они жались в
					 * кучу, а вплотную стояли потому, что я убирал зазоры ради
					 * четвёртого — теперь наверху только жук, и место есть.
					 */
					var GNOME_GAP = 20;    // просвет между гномами, как у игры
					var lowTop = Math.max(0, s.height - 50);

					// Ровняемся по левому краю блока гномов игры.
					var anchorX = null;
					var gnomes = document.querySelector('#wimpareaHelper .gnomes');
					if (gnomes) {
						var gr = gnomes.getBoundingClientRect();
						if (gr.width) anchorX = gr.left - s.left;
					}

					var totalW = 0;
					for (i = 0; i < others.length; i++) {
						totalW += sizeOf(others[i]).w + (i ? GNOME_GAP : 0);
					}
					if (anchorX === null) anchorX = Math.max(0, s.width - totalW - 20);
					// Не даём вылезти за правый край полосы.
					anchorX = Math.max(4, Math.min(anchorX, s.width - totalW - 4));

					var cursor = anchorX;
					for (i = 0; i < others.length; i++) {
						others[i].style.left = Math.round(cursor) + 'px';
						others[i].style.top = Math.round(lowTop) + 'px';
						cursor += sizeOf(others[i]).w + GNOME_GAP;
					}

					bar.setAttribute('data-anchored', 'ryad');
					return;
				}
			}

			// Полосы помощников нет — выстраиваем гномов в углу, в строку.
			bar.style.left = '';
			bar.style.right = '8px';
			bar.style.top = '8px';
			bar.style.width = 'auto';
			bar.style.height = 'auto';
			var x2 = 0;
			for (var k = 0; k < items.length; k++) {
				items[k].style.left = x2 + 'px';
				items[k].style.top = '0px';
				x2 += sizeOf(items[k]).w + 6;
			}
			bar.setAttribute('data-anchored', 'ugol');
		}

		/*
		 * ПОМОЩНИКИ ЖИВУТ ТОЛЬКО В САДУ.
		 *
		 * Город и рынок игра показывает НЕ переходом, а накладкой поверх
		 * сада: `#wimpareaDiv` остаётся на месте, и наши гномы, которые по
		 * нему равняются, оказывались поверх рыночной палатки. Владелец
		 * прислал снимок: «помощники перемещаются и на рынок, это
		 * неправильно».
		 *
		 * Судить по `current_location` нельзя: замер живой игры 2026-09-10
		 * показал, что в городе оно так и осталось `garden`. Настоящие
		 * признаки — флаг `stadt` самой игры и видимость её накладок.
		 */
		var NAKLADKI = ['stadt', 'citymap', 'multiframe'];

		/*
		 * НАКЛАДКА СЧИТАЕТСЯ ОТКРЫТОЙ, ТОЛЬКО ЕСЛИ В НЕЙ ЧТО-ТО ЕСТЬ.
		 *
		 * По одному `display` судить нельзя. `#multiframe` в CSS игры не
		 * спрятан вовсе (`z-index:22; position:absolute; 600×400` — и ни
		 * слова про display): прячет его код игры по событиям. Пустой
		 * контейнер с `display:block` по компьютеру «виден», и прежняя
		 * проверка гасила помощника целиком — ни гномов, ни садов.
		 * Первая живая проверка 2026-09-11 («панель с садами пропала,
		 * поливайка и сажалка не работали») укладывается ровно в это.
		 *
		 * Прятаться — украшение, а не защита: показаться лишний раз поверх
		 * палатки неприятно, спрятаться в саду — значит не работать. Поэтому
		 * признак должен быть СИЛЬНЫМ: флаг `stadt` самой игры или накладка
		 * с настоящим содержимым — фреймом с адресом либо видимыми потомками
		 * с площадью.
		 */
		function nakladkaOtkryta(e) {
			if (!e) return false;
			if (getComputedStyle(e).display === 'none') return false;
			var r = e.getBoundingClientRect();
			if (!r.width || !r.height) return false;
			var frames = e.querySelectorAll('iframe');
			for (var i = 0; i < frames.length; i++) {
				var src = frames[i].getAttribute('src') || '';
				if (src && src !== 'about:blank') return true;
			}
			var kids = e.children;
			for (var k = 0; k < kids.length; k++) {
				if (kids[k].tagName === 'IFRAME') continue;
				var kr = kids[k].getBoundingClientRect();
				if (kr.width && kr.height && getComputedStyle(kids[k]).display !== 'none') return true;
			}
			return false;
		}

		// Почему мы не в саду. Пустая строка — в саду.
		function prichinaSkryt() {
			try {
				if (window.stadt === true) return 'город (флаг stadt игры)';
				// Почта рисуется поверх сада и полосы помощников целиком:
				// снимок живой игры 2026-09-18 — наши гномы стояли на её
				// слоте заказов 10. Наш цикл работает там сам, кнопки не нужны.
				if (postOpen()) return 'открыта птичья почта';
				if (beesOpen()) return 'открыта пасека';
				for (var i = 0; i < NAKLADKI.length; i++) {
					if (nakladkaOtkryta(document.getElementById(NAKLADKI[i]))) {
						return 'открыта накладка #' + NAKLADKI[i];
					}
				}
			} catch (e) { /* не смогли выяснить — считаем, что в саду */ }
			return '';
		}

		function vSadu() { return !prichinaSkryt(); }

		function pokazat(vidno) {
			bar.style.display = vidno ? 'block' : 'none';
			gardens.style.display = vidno && gardensKey ? 'block' : 'none';
		}

		/*
		 * САМООТЧЁТ. Единственное, что возвращается с чужой машины, — журнал
		 * программы, а она сама видит лишь страницу снаружи. Поэтому панель
		 * выкладывает своё состояние в `window.SI_HELPER`, программа
		 * переписывает его в журнал при каждой перемене: появился ли
		 * помощник, спрятался ли и почему, сколько садов увидел.
		 */
		window.SI_HELPER = { sostoyanie: 'запускаюсь' };

		function soobshchit(prichina) {
			var s;
			try {
				if (prichina) {
					s = 'спрятан: ' + prichina;
				} else {
					s = 'в саду; гномов ' + bar.querySelectorAll('[data-si-button]').length
						+ ', в столбике ' + gardens.querySelectorAll('img').length
						+ ', сад ' + (currentGarden() || '?')
						+ (inWaterGarden() ? ' (водный)' : '');
				}
				if (sboy) s += '; сбой — ' + sboy;
				window.SI_HELPER.sostoyanie = s;
			} catch (e) { /* отчёт — не повод падать */ }
		}

		function reposition() {
			if (!vSadu()) { pokazat(false); return; }
			pokazat(true);
			placeBar();
			placeGardens();
		}

		/*
		 * КАЖДЫЙ ШАГ ЦИКЛА — ПОД СВОЕЙ ЗАЩИТОЙ.
		 *
		 * `tick()` идёт раз в секунду и держит всё: палатку, признак «в
		 * саду», столбик, расстановку. Исключение в любом шаге прежде
		 * останавливало цикл целиком — молча и навсегда: панель оставалась
		 * в том виде, в каком её застал сбой, а следующая перерисовка не
		 * приходила никогда. На чужой странице шаг может подвести любой
		 * (другая разметка, другое приложение). Теперь сбой одного шага не
		 * трогает остальных, а его причина попадает в самоотчёт — и в журнал.
		 */
		var sboy = '';

		function shag(gde, fn) {
			try { fn(); return true; }
			catch (e) {
				sboy = gde + ': ' + ((e && e.message) ? e.message : String(e));
				return false;
			}
		}

		function tick() {
			sboy = '';
			// Палатку подхватываем ДО проверки «мы в саду»: она как раз
			// открывается поверх сада, и каждый шаг продажи перезагружает
			// её страницу — подвешиваться надо заново.
			shag('палатка', hookStall);
			var prichina = '';
			shag('признак сада', function () { prichina = prichinaSkryt(); });
			if (prichina) {
				shag('спрятать', function () { pokazat(false); });
				soobshchit(prichina);
				return;
			}
			shag('жнец', syncHarvestButton);
			shag('почта', syncPostButton);
			shag('пасека', syncBeesButton);
			shag('попрошайки', hookWimps);
			shag('столбик', function () { buildGardens(false); });
			shag('расстановка', reposition);
			soobshchit('');
		}

		tick();
		window.addEventListener('resize', reposition);
		window.addEventListener('scroll', reposition, true);
		setInterval(tick, 1000);   // игра двигает свои кнопки и досылает данные сама

		notify.info('Помощник готов.');
	}
})();
