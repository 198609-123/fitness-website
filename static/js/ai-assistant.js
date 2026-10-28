/* ============================================================================
 * ai-assistant.js —— 全站 AI 减脂助手的交互逻辑
 * ----------------------------------------------------------------------------
 * 这个文件只负责“前端行为”，不知道后端怎么算的、RAG 服务在哪。
 * 它只做一件事：把用户输入的问题，通过 fetch 发给后端 /api/ask，
 * 拿到 { answer, citations } 后画到聊天面板里。
 *
 * 为什么放前端而不是后端拼 HTML：
 *   聊天是“一次次问答”的交互，用 JS 动态追加消息体验最好，
 *   也契合“视图与数据分离”——后端只给数据(JSON)，前端负责展示。
 * ==========================================================================*/

// 拿到页面上的几个关键元素（这些 id 是在 base.html 里写好的）
const fab = document.getElementById("aiFab");        // 右下角圆形按钮
const panel = document.getElementById("aiPanel");    // 聊天面板
const closeBtn = document.getElementById("aiClose"); // 面板右上角关闭按钮
const messages = document.getElementById("aiMessages"); // 消息列表容器
const input = document.getElementById("aiInput");    // 输入框
const sendBtn = document.getElementById("aiSend");   // 发送按钮

// ---------- 1. 面板开关 ----------
// 点圆形按钮：有 .open 就去掉（隐藏），没有就加上（显示）
fab.addEventListener("click", () => panel.classList.toggle("open"));
// 点关闭按钮：强制隐藏
closeBtn.addEventListener("click", () => panel.classList.remove("open"));

// ---------- 2. 往消息区追加一条消息 ----------
// role: "user" 表示用户说的，"bot" 表示助手回的
function appendMessage(role, text) {
    const div = document.createElement("div");
    div.className = "ai-msg " + role;   // 用 class 区分左右样式（见 style.css）
    div.textContent = text;             // textContent 会自动转义，避免 XSS（不把用户输入当 HTML）
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight; // 自动滚到最底部
}

// 单独追加“引用来源”小字（RAG 的 citations）
function appendCitations(citations) {
    if (!citations || citations.length === 0) return;  // 没有引用就不显示
    const div = document.createElement("div");
    div.className = "ai-msg bot ai-citation";
    // 把每条引用拼成一句“来源：文档名 第X页 (相关度 score)”
    const lines = citations.map(c =>
        `📚 ${c.source_file} 第${c.page}页（相关度 ${c.score.toFixed(3)}）`
    );
    div.textContent = "参考来源：\n" + lines.join("\n");
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
}

// ---------- 3. 发送问题 ----------
async function sendQuestion() {
    const question = input.value.trim();
    if (!question) return;               // 空内容不发送

    // 把用户问题显示到面板，并清空输入框
    appendMessage("user", question);
    input.value = "";

    // 先放一句“正在思考…”的占位，并禁用按钮防止重复点击
    const loading = document.createElement("div");
    loading.className = "ai-msg bot ai-loading";
    loading.textContent = "🤔 思考中…";
    messages.appendChild(loading);
    sendBtn.disabled = true;

    try {
        // fetch 发 POST 请求给后端 /api/ask；Content-Type 必须声明是 JSON
        const resp = await fetch("/api/ask", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question: question }),
        });
        const data = await resp.json();  // 后端返回的就是 { answer, citations }

        // 去掉“思考中”占位，再显示真实答案 + 引用
        loading.remove();
        appendMessage("bot", data.answer || "（没有返回内容）");
        appendCitations(data.citations);

    } catch (err) {
        // 网络层失败（比如后端挂了）：去掉占位，提示错误
        loading.remove();
        appendMessage("bot", "⚠️ 网络异常，请稍后重试。");
    } finally {
        sendBtn.disabled = false;        // 无论成功失败，恢复按钮可用
        input.focus();                   // 把焦点移回输入框，方便连续提问
    }
}

// ---------- 4. 绑定触发事件 ----------
sendBtn.addEventListener("click", sendQuestion);
// 在输入框按回车也能发送（Shift+回车则不发送，留作以后多行输入用）
input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();              // 阻止默认的换行行为
        sendQuestion();
    }
});
