/* ============================================================================
 * food-search.js —— 食物/外卖页的轻量交互
 * ----------------------------------------------------------------------------
 * 只做三件事，全部是渐进增强：
 *   1) 食物页：按名称/备注实时过滤卡片（不请求后端）
 *   2) 食物页：在「每 100g」与「每份」之间切换主数字
 *   3) 外卖页：按 0.5 / 1 / 1.5 份换算单卡热量
 * 没有这些脚本时，服务端渲染的卡片仍然全部可见、数字仍然正确。
 * ==========================================================================*/

(function () {
    "use strict";

    /* ---------- 1. 食物页：搜索 ---------- */
    var searchInput = document.getElementById("foodSearch");

    if (searchInput) {
        var cards = Array.prototype.slice.call(document.querySelectorAll(".food-card"));
        var categories = Array.prototype.slice.call(document.querySelectorAll(".category"));
        var meta = document.getElementById("searchMeta");
        var empty = document.getElementById("searchEmpty");
        var total = cards.length;

        var applyFilter = function () {
            var keyword = searchInput.value.trim().toLowerCase();
            var visible = 0;

            cards.forEach(function (card) {
                // 名称 + 备注一起匹配：搜"熟"也能找到熟米饭
                var haystack = ((card.dataset.name || "") + " " + (card.dataset.note || "")).toLowerCase();
                var hit = !keyword || haystack.indexOf(keyword) !== -1;
                card.hidden = !hit;
                if (hit) visible += 1;
            });

            // 分类下没有可见卡片时，把整个分类标题也隐藏，避免出现空标题
            categories.forEach(function (category) {
                category.hidden = !category.querySelector(".food-card:not([hidden])");
            });

            if (meta) {
                meta.textContent = keyword
                    ? "显示 " + visible + " / " + total + " 种食物"
                    : "共 " + total + " 种食物";
            }
            if (empty) empty.hidden = visible !== 0;
        };

        searchInput.addEventListener("input", applyFilter);

        /* ---------- 2. 食物页：口径切换 ---------- */
        var toggleButtons = Array.prototype.slice.call(
            document.querySelectorAll(".unit-toggle button")
        );

        toggleButtons.forEach(function (button) {
            button.addEventListener("click", function () {
                var basis = button.dataset.basis;

                toggleButtons.forEach(function (item) {
                    var active = item === button;
                    item.classList.toggle("is-active", active);
                    item.setAttribute("aria-pressed", String(active));
                });

                cards.forEach(function (card) {
                    var valueEl = card.querySelector(".cal-value");
                    var unitEl = card.querySelector(".cal-unit");
                    if (!valueEl || !unitEl) return;

                    if (basis === "per_serving" && card.dataset.servingCalories) {
                        valueEl.textContent = card.dataset.servingCalories;
                        unitEl.textContent = "大卡/份";
                    } else if (card.dataset.calories) {
                        valueEl.textContent = card.dataset.calories;
                        unitEl.textContent = "大卡/100g";
                    }
                    // 没有 data-calories 时保持原样：
                    // 写 undefined 会让卡片显示“undefined 大卡/100g”
                });
            });
        });
    }

    /* ---------- 3. 外卖页：份量换算 ---------- */
    var portionSelects = Array.prototype.slice.call(
        document.querySelectorAll(".portion-select")
    );

    portionSelects.forEach(function (select) {
        select.addEventListener("change", function () {
            var card = select.closest(".food-card");
            var valueEl = card && card.querySelector(".cal-value");
            if (!valueEl) return;

            var base = Number(select.dataset.baseCalories);
            var factor = Number(select.value);

            // 任意一边不是数字就放弃换算，避免把 "NaN" 写到页面上
            if (!isFinite(base) || !isFinite(factor) || base <= 0 || factor <= 0) return;

            valueEl.textContent = Math.round(base * factor);
        });
    });
})();
