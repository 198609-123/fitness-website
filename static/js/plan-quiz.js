/* ============================================================================
 * plan-quiz.js —— 首页“30 秒选方案”小测
 * ----------------------------------------------------------------------------
 * 纯前端规则推荐，不请求后端、不写 localStorage：
 *   1) 不能接受低热量日 → 碳循环（不用断食）
 *   2) 外食几乎每天     → 5+2（只需管两天）
 *   3) 每周训练 5 次以上 → 碳循环（高碳日配合训练）
 *   4) 其余             → 5+2（规则少、容错高）
 * 推荐结果直接带 ?type= 跳 /calculator，和现有路由完全兼容。
 * ==========================================================================*/

(function () {
    "use strict";

    var quiz = document.getElementById("plan-quiz");
    if (!quiz) return;

    var submit = document.getElementById("quizSubmit");
    var result = document.getElementById("quizResult");
    var pickEl = document.getElementById("quizPick");
    var reasonEl = document.getElementById("quizReason");
    var cta = document.getElementById("quizCta");
    var altEl = document.getElementById("quizAlt");

    // 模板改结构时这几个节点可能缺失。缺任何一个都直接退出：
    // 少了 null 判断，一次改名就会让整段脚本抛错，小测按钮彻底失灵。
    if (!submit || !result || !pickEl || !reasonEl || !cta || !altEl) return;

    var PLANS = {
        "52": {
            name: "5+2 断食法",
            cta: "用 5+2 算我的热量",
            href: "/calculator?type=52",
            alt: "另一个方案：碳循环更适合每周规律训练 3 次以上、愿意称重算宏量的人。"
        },
        carbon: {
            name: "碳循环减脂法",
            cta: "用碳循环算我的热量",
            href: "/calculator?type=carbon",
            alt: "另一个方案：5+2 更适合外食多、不想每天记录的人，但需要能接受两天低热量。"
        }
    };

    function checkedValue(name) {
        var el = quiz.querySelector('input[name="' + name + '"]:checked');
        return el ? el.value : "";
    }

    function recommend(training, outing, fasting) {
        if (fasting === "no") {
            return {
                plan: "carbon",
                reason: training === "high"
                    ? "你训练频率高、又不想断食，碳循环把高碳日放在训练日，比 5+2 更适合你。"
                    : "你不想把某一天压到 500–600 大卡，碳循环不用断食，靠低/中/高碳日的差值制造缺口。"
            };
        }
        if (outing === "daily") {
            return {
                plan: "52",
                reason: "你外食或聚餐多，5+2 只要管两天，其余五天正常吃，比每天算宏量更容易坚持。"
            };
        }
        if (training === "high") {
            return {
                plan: "carbon",
                reason: "你每周训练 5 次以上，碳循环可以把高碳日放在大重量训练日，兼顾训练表现和热量缺口。"
            };
        }
        return {
            plan: "52",
            reason: "你先从 5+2 开始：每周只安排两天低热量，规则少、容错高，适合第一次系统减脂。"
        };
    }

    function render() {
        var outcome = recommend(
            checkedValue("training"),
            checkedValue("outing"),
            checkedValue("fasting")
        );
        var plan = PLANS[outcome.plan];

        var titleEl = cta.querySelector(".btn-title");

        pickEl.textContent = plan.name;
        reasonEl.textContent = outcome.reason;
        cta.href = plan.href;
        if (titleEl) titleEl.textContent = plan.cta;   // 结构变了也不至于中断
        altEl.textContent = plan.alt;

        result.hidden = false;
    }

    submit.addEventListener("click", function () {
        render();
        var reduce = window.matchMedia
            ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
            : false;
        result.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "nearest" });
    });

    // 看过一次结果后，改答案直接更新，避免推荐和选项对不上
    quiz.querySelectorAll('input[type="radio"]').forEach(function (radio) {
        radio.addEventListener("change", function () {
            if (!result.hidden) render();
        });
    });
})();
