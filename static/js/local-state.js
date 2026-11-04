/* ============================================================================
 * local-state.js —— 本机状态：今日预算 + 今日概览 + 体重打卡
 * ----------------------------------------------------------------------------
 * 设计原则：
 *   1) 不引入账号、不发请求，全部存 localStorage；换设备/清缓存就没了。
 *   2) 三个页面共用一个模块：
 *      - 计算器页：把结果写成 plan（#plan-data 存在时）
 *      - 食物/外卖页：渲染今日预算（#budgetBar），并给每张卡加“加入今天”
 *      - 首页：有计划时切换成今日概览（#homeDash），处理体重打卡与 7 日趋势
 *   3) 任何 localStorage 读写都包 try/catch：隐私模式下失败也不影响页面主体。
 * ==========================================================================*/

(function () {
    "use strict";

    var KEYS = {
        plan: "leanlab.plan.v1",
        today: "leanlab.today.v1",
        log: "leanlab.log.v1"
    };

    var DAY_LABELS = {
        normal: "正常日",
        fasting: "断食日",
        low: "低碳日",
        mid: "中碳日",
        high: "高碳日"
    };

    /* ---------- 通用工具 ---------- */
    function read(key, fallback) {
        try {
            var raw = window.localStorage.getItem(key);
            return raw ? JSON.parse(raw) : fallback;
        } catch (err) {
            return fallback;
        }
    }

    function write(key, value) {
        try {
            window.localStorage.setItem(key, JSON.stringify(value));
            return true;
        } catch (err) {
            return false;
        }
    }

    function remove(key) {
        try {
            window.localStorage.removeItem(key);
        } catch (err) {
            /* 忽略：清不掉也不阻塞页面 */
        }
    }

    function pad(n) {
        return n < 10 ? "0" + n : String(n);
    }

    function isoDate(date) {
        var d = date || new Date();
        return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
    }

    function todayISO() {
        return isoDate(new Date());
    }

    function formatNumber(value) {
        return Math.round(Number(value) || 0).toLocaleString("zh-CN");
    }

    function toNumber(value) {
        var n = Number(value);
        return isFinite(n) ? n : 0;
    }

    function sumCalories(entries) {
        return (entries || []).reduce(function (total, item) {
            return total + toNumber(item.calories);
        }, 0);
    }

    function dateLabel() {
        var d = new Date();
        var weeks = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
        return (d.getMonth() + 1) + " 月 " + d.getDate() + " 日 · " + weeks[d.getDay()];
    }

    /* ---------- 计划：把模板里的 result_data 转成轻量结构 ---------- */
    function buildPlan(result, weight, targetWeight) {
        if (!result || !result.plan_type) return null;

        if (result.plan_type === "52") {
            return {
                version: 1,
                planType: "52",
                label: "5+2 断食法",
                weight: toNumber(weight) || null,
                targetWeight: toNumber(targetWeight) || null,
                defaultDay: "normal",
                days: {
                    // 正常日给 TDEE 上下浮动 100 的中间值，避免用户总按上限吃
                    normal: Math.round((toNumber(result.normal_day_min) + toNumber(result.normal_day_max)) / 2),
                    fasting: toNumber(result.fasting_simple)
                },
                savedAt: todayISO()
            };
        }

        if (result.plan_type === "carbon") {
            return {
                version: 1,
                planType: "carbon",
                label: "碳循环减脂法",
                weight: toNumber(weight) || null,
                targetWeight: toNumber(targetWeight) || null,
                defaultDay: "mid",
                days: {
                    low: toNumber(result.low_day && result.low_day.total),
                    mid: toNumber(result.mid_day && result.mid_day.total),
                    high: toNumber(result.high_day && result.high_day.total)
                },
                savedAt: todayISO()
            };
        }

        return null;
    }

    /* ---------- 计划结构校验 ---------- */
    // localStorage 里的内容可能是旧版本、被手改、或写了一半的脏数据。
    // 不校验就直接读 plan.days[xxx] 会抛 TypeError，把整个模块打断，
    // 连带预算条和今日概览一起失效。
    function isValidPlan(plan) {
        if (!plan || typeof plan !== "object") return false;
        if (!plan.days || typeof plan.days !== "object") return false;

        var keys = Object.keys(plan.days);
        if (keys.length === 0) return false;

        // 至少要有一个能用的数字，否则预算会显示成 0 / 0
        return keys.some(function (key) {
            return isFinite(Number(plan.days[key]));
        });
    }

    /* ---------- 今日状态：跨日自动清零 ---------- */
    function getToday(plan) {
        var state = read(KEYS.today, null);
        var date = todayISO();

        if (!state || state.date !== date) {
            state = { date: date, dayType: plan.defaultDay, consumed: [] };
            write(KEYS.today, state);
        }
        if (!state.consumed) state.consumed = [];

        // defaultDay 可能不在 days 里（旧数据），退回到第一个可用日型，
        // 否则 target 会变成 undefined，页面上就是 NaN
        var fallbackDay = plan.days[plan.defaultDay] ? plan.defaultDay : Object.keys(plan.days)[0];
        if (!plan.days[state.dayType]) state.dayType = fallbackDay;

        return state;
    }

    /* ---------- 计算器页：保存方案 + 提示 ---------- */
    var planDataEl = document.getElementById("plan-data");
    var savedHint = document.getElementById("planSaved");

    if (planDataEl) {
        var resultData = null;
        try {
            resultData = JSON.parse(planDataEl.textContent);
        } catch (err) {
            resultData = null;
        }

        var planToSave = buildPlan(resultData, planDataEl.dataset.weight, planDataEl.dataset.targetWeight);
        if (planToSave) {
            var saved = write(KEYS.plan, planToSave);
            if (savedHint) {
                savedHint.hidden = false;
                if (saved) {
                    savedHint.innerHTML = '已保存到本机 · <a href="/food-calories">去食物页按预算扣减</a>';
                } else {
                    savedHint.textContent = "当前浏览器不支持本地保存，预算与打卡功能不可用。";
                }
            }
        }
    }

    var storedPlan = read(KEYS.plan, null);
    var plan = isValidPlan(storedPlan) ? storedPlan : null;

    /* ---------- 食物 / 外卖页：今日预算 ---------- */
    var budgetBar = document.getElementById("budgetBar");
    var budgetEmpty = document.getElementById("budgetEmpty");

    function updateBudget(planData, state) {
        var target = toNumber(planData.days[state.dayType] || planData.days[planData.defaultDay]);
        var used = sumCalories(state.consumed);
        var remaining = target - used;

        var remainingEl = document.getElementById("budgetRemaining");
        var targetEl = document.getElementById("budgetTarget");
        var usedEl = document.getElementById("budgetUsed");
        var fillEl = document.getElementById("budgetFill");
        var planLabelEl = document.getElementById("budgetPlanLabel");
        if (!remainingEl || !targetEl || !usedEl || !fillEl) return;

        remainingEl.textContent = formatNumber(remaining);
        targetEl.textContent = formatNumber(target);
        usedEl.textContent = formatNumber(used);
        if (planLabelEl) planLabelEl.textContent = planData.label;

        var pct = target > 0 ? Math.min(100, (used / target) * 100) : 0;
        fillEl.style.width = pct + "%";

        var over = remaining < 0;
        budgetBar.classList.toggle("is-over", over);
        remainingEl.classList.toggle("is-negative", over);
    }

    function buildBudgetBar(planData, state) {
        if (!budgetBar) return;

        budgetBar.innerHTML = [
            '<div class="budget-top">',
            '  <div class="budget-main">',
            '    <span class="budget-kicker">今日剩余</span>',
            '    <strong class="budget-remaining" id="budgetRemaining">0</strong>',
            '    <span class="budget-target">/ <span id="budgetTarget">0</span> 大卡</span>',
            '  </div>',
            '  <div class="budget-actions">',
            '    <select id="budgetDay" aria-label="今天的热量类型"></select>',
            '    <button type="button" id="budgetReset">重置</button>',
            '  </div>',
            '</div>',
            '<div class="budget-track"><span class="budget-fill" id="budgetFill"></span></div>',
            '<p class="budget-note">已记录 <span id="budgetUsed">0</span> 大卡 · <span id="budgetPlanLabel"></span> · 仅存本机，跨日自动清零</p>'
        ].join("");

        var daySelect = document.getElementById("budgetDay");
        daySelect.innerHTML = Object.keys(planData.days).map(function (key) {
            return '<option value="' + key + '">' + (DAY_LABELS[key] || key) + "</option>";
        }).join("");
        daySelect.value = state.dayType;

        daySelect.addEventListener("change", function () {
            state.dayType = daySelect.value;
            write(KEYS.today, state);
            updateBudget(planData, state);
        });

        document.getElementById("budgetReset").addEventListener("click", function () {
            state.consumed = [];
            write(KEYS.today, state);
            updateBudget(planData, state);
        });

        updateBudget(planData, state);
    }

    function addTodayButtons(planData, state) {
        var cards = Array.prototype.slice.call(document.querySelectorAll(".food-card"));
        cards.forEach(function (card) {
            if (card.querySelector(".add-today")) return;
            var info = card.querySelector(".food-info");
            if (!info) return;

            var button = document.createElement("button");
            button.type = "button";
            button.className = "add-today";
            button.textContent = "加入今天";

            button.addEventListener("click", function () {
                var valueEl = card.querySelector(".cal-value");
                var calories = valueEl ? toNumber(valueEl.textContent.replace(/,/g, "")) : 0;
                if (!calories) return;

                state.consumed.push({
                    name: card.dataset.name || "食物",
                    calories: calories,
                    at: Date.now()
                });
                write(KEYS.today, state);
                updateBudget(planData, state);

                button.textContent = "已加入 +" + formatNumber(calories);
                button.classList.add("is-added");
                window.setTimeout(function () {
                    button.textContent = "加入今天";
                    button.classList.remove("is-added");
                }, 1200);
            });

            info.appendChild(button);
        });
    }

    if (budgetBar) {
        if (!plan) {
            budgetBar.hidden = true;
            if (budgetEmpty) budgetEmpty.hidden = false;
        } else {
            budgetBar.hidden = false;
            if (budgetEmpty) budgetEmpty.hidden = true;
            var budgetState = getToday(plan);
            buildBudgetBar(plan, budgetState);
            addTodayButtons(plan, budgetState);
        }
    }

    /* ---------- 首页：今日概览 + 打卡 ---------- */
    var dashboard = document.getElementById("homeDash");
    var hero = document.getElementById("homeHero");

    function computeStreak(entries) {
        var seen = {};
        (entries || []).forEach(function (item) {
            if (item.date) seen[item.date] = true;
        });

        var cursor = new Date();
        if (!seen[isoDate(cursor)]) {
            cursor.setDate(cursor.getDate() - 1);
            if (!seen[isoDate(cursor)]) return 0;
        }

        var count = 0;
        while (seen[isoDate(cursor)]) {
            count += 1;
            cursor.setDate(cursor.getDate() - 1);
        }
        return count;
    }

    function latestEntry(entries) {
        var sorted = (entries || []).slice().sort(function (a, b) {
            return String(a.date).localeCompare(String(b.date));
        });
        return sorted.length ? sorted[sorted.length - 1] : null;
    }

    function renderChart(entries) {
        var chart = document.getElementById("dashChart");
        var hint = document.getElementById("dashTrendHint");
        if (!chart || !hint) return;

        chart.innerHTML = "";
        var recent = (entries || []).slice().sort(function (a, b) {
            return String(a.date).localeCompare(String(b.date));
        }).slice(-7);

        if (recent.length < 2) {
            hint.textContent = recent.length === 1 ? "再记 1 次就能看到趋势" : "记录 2 次后显示趋势";
            var empty = document.createElement("p");
            empty.className = "dash-chart-empty";
            empty.textContent = "还没有足够的体重记录";
            chart.appendChild(empty);
            return;
        }

        hint.textContent = "最近 " + recent.length + " 次";
        var width = 280;
        var height = 52;
        var pad = 5;
        var values = recent.map(function (item) { return toNumber(item.weight); });
        var min = Math.min.apply(null, values);
        var max = Math.max.apply(null, values);
        if (max - min < 1) {
            max += 0.5;
            min -= 0.5;
        }

        var points = values.map(function (value, index) {
            var x = pad + (width - pad * 2) * (index / (values.length - 1));
            var y = pad + (height - pad * 2) * (1 - (value - min) / (max - min));
            return x.toFixed(1) + "," + y.toFixed(1);
        });

        var ns = "http://www.w3.org/2000/svg";
        var svg = document.createElementNS(ns, "svg");
        svg.setAttribute("viewBox", "0 0 " + width + " " + height);
        svg.setAttribute("preserveAspectRatio", "none");
        svg.setAttribute("aria-hidden", "true");

        var line = document.createElementNS(ns, "polyline");
        line.setAttribute("points", points.join(" "));
        line.setAttribute("fill", "none");
        line.setAttribute("stroke", "#10B981");
        line.setAttribute("stroke-width", "2");
        line.setAttribute("stroke-linecap", "round");
        line.setAttribute("stroke-linejoin", "round");
        line.setAttribute("vector-effect", "non-scaling-stroke");
        svg.appendChild(line);

        var last = points[points.length - 1].split(",");
        var dot = document.createElementNS(ns, "circle");
        dot.setAttribute("cx", last[0]);
        dot.setAttribute("cy", last[1]);
        dot.setAttribute("r", "3.2");
        dot.setAttribute("fill", "#10B981");
        svg.appendChild(dot);

        chart.appendChild(svg);
    }

    function renderDashboard(planData) {
        if (!dashboard) return;

        if (!planData) {
            dashboard.hidden = true;
            if (hero) hero.hidden = false;
            return;
        }

        dashboard.hidden = false;
        if (hero) hero.hidden = true;

        var state = getToday(planData);
        var log = read(KEYS.log, null) || {
            startWeight: planData.weight || null,
            startDate: todayISO(),
            entries: []
        };
        if (!log.entries) log.entries = [];
        if (!log.startWeight && planData.weight) log.startWeight = planData.weight;

        var target = toNumber(planData.days[state.dayType] || planData.days[planData.defaultDay]);
        var used = sumCalories(state.consumed);
        var latest = latestEntry(log.entries);
        var delta = latest && log.startWeight ? toNumber(latest.weight) - toNumber(log.startWeight) : 0;

        var dateEl = document.getElementById("dashDate");
        var targetEl = document.getElementById("dashTarget");
        var usedEl = document.getElementById("dashUsed");
        var streakEl = document.getElementById("dashStreak");
        var deltaEl = document.getElementById("dashDelta");
        var deltaLabel = document.getElementById("dashDeltaLabel");
        var planLink = document.getElementById("dashPlanLink");
        var weightInput = document.getElementById("checkinWeight");
        var milestone = document.getElementById("dashMilestone");

        if (dateEl) dateEl.textContent = dateLabel();
        if (targetEl) targetEl.textContent = formatNumber(target);
        if (usedEl) usedEl.textContent = formatNumber(used);
        if (streakEl) streakEl.textContent = String(computeStreak(log.entries));
        if (deltaEl) {
            // 有目标体重时优先显示“距目标”，否则退回“较起始”
            var currentWeight = latest ? toNumber(latest.weight) : (planData.weight || null);
            var remaining = planData.targetWeight && currentWeight
                ? currentWeight - planData.targetWeight
                : null;

            if (remaining !== null) {
                deltaEl.textContent = remaining > 0.05 ? remaining.toFixed(1) : "0";
                deltaEl.classList.toggle("is-loss", remaining <= 0.05);
                deltaEl.classList.toggle("is-gain", false);
                if (deltaLabel) deltaLabel.textContent = "距目标";
            } else {
                deltaEl.textContent = (delta > 0 ? "+" : "") + (Math.abs(delta) < 0.05 ? "0" : delta.toFixed(1));
                deltaEl.classList.toggle("is-loss", delta <= -0.05);
                deltaEl.classList.toggle("is-gain", delta >= 0.05);
                if (deltaLabel) deltaLabel.textContent = "较起始";
            }
        }
        if (planLink) {
            planLink.href = planData.planType === "carbon" ? "/calculator?type=carbon" : "/calculator?type=52";
        }
        if (weightInput) {
            weightInput.placeholder = (latest ? toNumber(latest.weight) : (planData.weight || 75)).toFixed(1);
        }
        if (milestone) milestone.hidden = true;

        renderChart(log.entries);
    }

    if (dashboard) {
        renderDashboard(plan);

        var checkinForm = document.getElementById("checkinForm");
        if (checkinForm) {
            checkinForm.addEventListener("submit", function (event) {
                event.preventDefault();

                var input = document.getElementById("checkinWeight");
                var value = Number(input.value);
                if (!isFinite(value) || value < 30 || value > 200) {
                    if (input.reportValidity) input.reportValidity();
                    return;
                }

                var log = read(KEYS.log, null) || {
                    startWeight: plan && plan.weight ? plan.weight : value,
                    startDate: todayISO(),
                    entries: []
                };
                if (!log.entries) log.entries = [];

                var entry = { date: todayISO(), weight: Math.round(value * 10) / 10 };
                var replaced = false;
                for (var i = 0; i < log.entries.length; i += 1) {
                    if (log.entries[i].date === entry.date) {
                        log.entries[i] = entry;
                        replaced = true;
                        break;
                    }
                }
                if (!replaced) log.entries.push(entry);
                if (!log.startWeight) log.startWeight = entry.weight;
                if (!log.startDate) log.startDate = entry.date;

                write(KEYS.log, log);
                input.value = "";
                renderDashboard(plan);

                var milestone = document.getElementById("dashMilestone");
                if (!milestone) return;
                var delta = entry.weight - toNumber(log.startWeight);
                var streak = computeStreak(log.entries);
                var message = "已记录，明天同一时间再来。";

                if (log.entries.length === 1) {
                    message = "第一次记录，坚持 7 天就能看到趋势。";
                } else if (delta <= -1) {
                    message = "已减 " + Math.abs(delta).toFixed(1) + " kg，保持住。";
                } else if (streak >= 7) {
                    message = "连续记录 " + streak + " 天，节奏很稳。";
                }

                milestone.hidden = false;
                milestone.textContent = message;
            });
        }

        // 两个入口共用同一套清除逻辑：顶部的“不是我的数据？”和底部的“清除数据”。
        // 共用设备时，第二个人看到的第一个问题就是“这是谁的数据”，所以顶部那个更显眼。
        function clearAll() {
            if (!window.confirm("会清除这台设备上的方案、今日预算和体重记录，确定吗？")) return;
            remove(KEYS.plan);
            remove(KEYS.today);
            remove(KEYS.log);
            window.location.reload();
        }

        ["dashClearTop", "dashClear"].forEach(function (id) {
            var button = document.getElementById(id);
            if (button) button.addEventListener("click", clearAll);
        });
    }
})();
