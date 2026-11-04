/* ============================================================================
 * ai-assistant.js —— 全站 AI 减脂助手的交互逻辑
 * ----------------------------------------------------------------------------
 * 这个文件只负责“前端行为”，不知道后端怎么算的、RAG 服务在哪。
 * 它只做一件事：把用户输入的问题，通过 fetch 发给后端 /api/ask，
 * 拿到 { answer, citations, degraded, suggestions } 后画到聊天面板里。
 *
 * 为什么放前端而不是后端拼 HTML：
 *   聊天是“一次次问答”的交互，用 JS 动态追加消息体验最好，
 *   也契合“视图与数据分离”——后端只给数据(JSON)，前端负责展示。
 *
 * 本轮加固（都对应真实会报错的点）：
 *   1) 母版节点缺失时直接退出，不再抛 null.addEventListener 中断整个脚本；
 *   2) 用 isSending 锁住发送，避免回车连按发多份请求；
 *   3) resp.json() 失败、answer 不是字符串、score 是 null，都有兜底；
 *   4) 服务降级时展示“常见问题 + 重试”，而不是把技术细节丢给用户。
 * ==========================================================================*/

(function () {
    "use strict";

    // 拿到页面上的几个关键元素（这些 id 是在 base.html 里写好的）
    var fab = document.getElementById("aiFab");             // 右下角圆形按钮
    var panel = document.getElementById("aiPanel");         // 聊天面板
    var closeBtn = document.getElementById("aiClose");      // 面板右上角关闭按钮
    var messages = document.getElementById("aiMessages");   // 消息列表容器
    var input = document.getElementById("aiInput");         // 输入框
    var sendBtn = document.getElementById("aiSend");        // 发送按钮

    // 少任何一个节点，后面的 addEventListener 都会抛错并让整段脚本失效。
    // 这里一次性判断并退出：助手不工作，但页面其他功能不受影响。
    if (!fab || !panel || !closeBtn || !messages || !input || !sendBtn) return;

    var isSending = false;   // 是否已有请求在飞

    // 本机状态键名要和 local-state.js 保持一致；这里只读不写
    var PLAN_KEY = "leanlab.plan.v1";
    var TODAY_KEY = "leanlab.today.v1";

    var DAY_LABELS = {
        normal: "正常日",
        fasting: "断食日",
        low: "低碳日",
        mid: "中碳日",
        high: "高碳日"
    };

    /* ---------- 0. 小工具 ---------- */

    // 读本机缓存：隐私模式或数据损坏时返回 null，绝不让页面报错
    function readStore(key) {
        try {
            var raw = window.localStorage.getItem(key);
            return raw ? JSON.parse(raw) : null;
        } catch (err) {
            return null;
        }
    }

    function pad(n) {
        return n < 10 ? "0" + n : String(n);
    }

    function todayISO() {
        var d = new Date();
        return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
    }

    function toNumber(value) {
        var n = Number(value);
        return isFinite(n) ? n : 0;
    }

    /* ---------- 1. 本机方案摘要：让助手知道“我是谁、吃多少” ---------- */

    function buildContext() {
        var plan = readStore(PLAN_KEY);
        if (!plan || !plan.days || typeof plan.days !== "object") return "";

        var parts = [];

        if (plan.weight) parts.push(plan.weight + "kg");
        if (plan.targetWeight) parts.push("目标 " + plan.targetWeight + "kg");
        if (plan.label) parts.push("方案 " + plan.label);

        var dayType = plan.defaultDay;
        var today = readStore(TODAY_KEY);

        if (today && today.date === todayISO() && plan.days[today.dayType]) {
            dayType = today.dayType;

            var used = 0;
            if (Array.isArray(today.consumed)) {
                used = today.consumed.reduce(function (sum, item) {
                    return sum + toNumber(item && item.calories);
                }, 0);
            }
            if (used > 0) parts.push("今日已记 " + Math.round(used) + " 大卡");
        }

        var target = plan.days[dayType];
        if (target) {
            parts.push((DAY_LABELS[dayType] || "今日") + " " + target + " 大卡");
        }

        return parts.join("，");
    }

    /* ---------- 2. 面板开关 ---------- */
    // 点圆形按钮：有 .open 就去掉（隐藏），没有就加上（显示）
    // aria-expanded 同步更新，读屏软件才知道面板现在是开还是关
    fab.addEventListener("click", function () {
        var isOpen = panel.classList.toggle("open");
        fab.setAttribute("aria-expanded", String(isOpen));
        if (isOpen) input.focus();
    });

    // 点关闭按钮：强制隐藏，并把焦点还给浮窗按钮（键盘用户不会丢焦点）
    closeBtn.addEventListener("click", function () {
        panel.classList.remove("open");
        fab.setAttribute("aria-expanded", "false");
        fab.focus();
    });

    /* ---------- 3. 往消息区追加内容 ---------- */

    function scrollToBottom() {
        messages.scrollTop = messages.scrollHeight;
    }

    // role: "user" 表示用户说的，"bot" 表示助手回的
    function appendMessage(role, text) {
        var div = document.createElement("div");
        div.className = "ai-msg " + role;   // 用 class 区分左右样式（见 style.css）
        div.textContent = text;             // textContent 会自动转义，避免 XSS
        messages.appendChild(div);
        scrollToBottom();
        return div;
    }

    // 单独追加“引用来源”小字。
    // RAG 返回的字段不可信：score 可能是 null、page 可能是字符串，
    // 直接调 toFixed 会抛 TypeError 并打断后续渲染，所以逐项做类型收敛。
    function appendCitations(citations) {
        if (!Array.isArray(citations) || citations.length === 0) return;

        var lines = citations.map(function (c) {
            if (!c || typeof c !== "object") return "";

            var file = typeof c.source_file === "string" && c.source_file ? c.source_file : "未知文档";
            var page = Number(c.page);
            var pageText = isFinite(page) && page > 0 ? " 第" + page + "页" : "";
            var score = Number(c.score);
            var scoreText = isFinite(score) && score > 0 ? "（相关度 " + score.toFixed(3) + "）" : "";

            return file + pageText + scoreText;
        }).filter(function (line) { return line; });

        if (lines.length === 0) return;

        var div = document.createElement("div");
        div.className = "ai-msg bot ai-citation";
        div.textContent = "参考来源：\n" + lines.join("\n");
        messages.appendChild(div);
        scrollToBottom();
    }

    // 降级时给的“常见问题”快捷入口：点一下就能继续问
    function appendSuggestions(list) {
        var items = Array.isArray(list) ? list.filter(function (q) {
            return typeof q === "string" && q.trim();
        }) : [];
        if (items.length === 0) return;

        var box = document.createElement("div");
        box.className = "ai-suggest";

        items.forEach(function (text) {
            var btn = document.createElement("button");
            btn.type = "button";
            btn.className = "ai-suggest-btn";
            btn.textContent = text;
            btn.addEventListener("click", function () {
                box.remove();                 // 点过就收起来，避免面板越来越长
                // 用户确实“问了这句”，回显一条气泡更符合聊天直觉
                sendQuestion(text);
            });
            box.appendChild(btn);
        });

        messages.appendChild(box);
        scrollToBottom();
    }

    // 失败后的“重试”：只有真发过请求才会出现
    function appendRetry(question) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "ai-retry";
        btn.textContent = "重试这个问题";

        btn.addEventListener("click", function () {
            btn.remove();
            sendQuestion(question, true);
        });

        messages.appendChild(btn);
        scrollToBottom();
    }

    function setSending(state) {
        isSending = state;
        sendBtn.disabled = state;
    }

    /* ---------- 4. 发送问题 ---------- */

    // text 可传字符串（快捷问题/重试）；不传就读输入框
    // skipEcho 为 true 时不插入用户气泡：重试是同一个问题，再显示一遍只会让面板变长
    async function sendQuestion(text, skipEcho) {
        var question = (typeof text === "string" ? text : input.value).trim();
        if (!question || isSending) return;

        if (skipEcho !== true) appendMessage("user", question);
        input.value = "";

        var loading = document.createElement("div");
        loading.className = "ai-msg bot ai-loading";
        loading.textContent = "正在思考…";
        messages.appendChild(loading);
        scrollToBottom();
        setSending(true);

        try {
            var resp = await fetch("/api/ask", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                // context 是本机方案摘要，后端会拼在问题前面；没有方案时为空串
                body: JSON.stringify({ question: question, context: buildContext() })
            });

            // 后端异常时可能返回非 JSON（例如反代返回 HTML 错误页），
            // 直接 resp.json() 会抛错，所以单独兜住解析这一步。
            var data = null;
            try {
                data = await resp.json();
            } catch (parseErr) {
                data = null;
            }

            loading.remove();

            if (!data || typeof data.answer !== "string" || !data.answer.trim()) {
                appendMessage("bot", resp.ok
                    ? "助手这次没返回内容，换个说法再问一次。"
                    : "AI 助手暂时不可用，请稍后重试。");
                appendRetry(question);
                return;
            }

            appendMessage("bot", data.answer);
            appendCitations(data.citations);

            // degraded 由后端标记：服务没启动/超时/返回异常时都是 true
            if (data.degraded) {
                appendSuggestions(data.suggestions);
                appendRetry(question);
            }

        } catch (err) {
            // 走到这里说明 fetch 本身失败（断网、后端进程没起来）
            loading.remove();
            appendMessage("bot", "网络异常，没能连上服务器。");
            appendSuggestions(["减脂每天该吃多少蛋白质？", "连续两周体重不动怎么办？"]);
            appendRetry(question);
        } finally {
            setSending(false);
            input.focus();
        }
    }

    /* ---------- 5. 绑定触发事件 ---------- */
    sendBtn.addEventListener("click", function () { sendQuestion(); });

    // 在输入框按回车也能发送（Shift+回车则不发送，留作以后多行输入用）
    input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();              // 阻止默认的换行行为
            sendQuestion();
        }
    });
})();
