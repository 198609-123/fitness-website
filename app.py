# ============================================================================
# app.py —— 减脂网站后端（Flask）
# ----------------------------------------------------------------------------
# 本轮重构做了什么（对应你列的 5 个优先级 + 额外改进）：
#   1) 优先级1：结果 HTML 不再写死在 Python 里，改成“Python 只算数据 → 模板负责画”
#   2) 优先级4：抽出纯计算函数 + 新增 JSON 接口 /api/calculate、/api/ask
#   3) 优先级5：食物/外卖数据抽到 data/*.json，模板用循环渲染
#   4) 额外：异常处理从只拦 ValueError 扩到能拦“缺字段/非法数字”，并给友好提示
#   5) 额外：接入 RAG 检索问答助手——/api/ask 把问题转发给独立的 RAG 服务
#
# 架构说明（很重要）：
#   减脂网站(Flask, 端口5000) 和 RAG 服务(FastAPI, 端口8000) 是两个独立进程。
#   前端 AI 助手 → 调用本文件的 /api/ask → 本文件用 requests 转发给 RAG 服务的
#   /ask 接口。这样做的好处：减脂网站完全不用装 chromadb/fastembed 这些重依赖，
#   两个项目各管各的依赖，这也是“服务拆分 / 微服务”的基本思路。
# ============================================================================

import os
import json
import requests  # 用来把请求转发给 RAG 服务（HTTP 客户端库）
from pathlib import Path

from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# 本文件所在目录（用于定位 data/ 和 templates/）
BASE_DIR = Path(__file__).resolve().parent

# RAG 服务的地址：默认指向本地 8000 端口的 /ask。
# 可用环境变量 RAG_SERVICE_URL 覆盖（比如将来 RAG 服务部署到别的机器）。
RAG_SERVICE_URL = os.getenv("RAG_SERVICE_URL", "http://127.0.0.1:8000/ask")


# ----------------------------------------------------------------------------
# 工具函数：读取 data/ 下的 JSON 数据文件
# ----------------------------------------------------------------------------
def load_json(filename: str):
    """读取 data 目录下的 JSON 文件并返回 Python 对象。

    把所有“会变的数据”（食物热量等）放 JSON，而不是写死在 HTML 里，
    以后加一种食物、改一个热量值，只改 JSON 即可，不用动模板和 Python。
    """
    path = BASE_DIR / "data" / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------------
# 核心计算（纯函数：只吃参数、返回数据字典，不碰 HTML、不发请求）
# 这正是“视图(HTML)与数据(计算)分离”的体现，也是 /api/calculate 能复用的基础。
# ----------------------------------------------------------------------------

def calculate_bmr_tdee(gender, weight, height, age, activity_level):
    """计算基础代谢率(BMR)和每日总消耗(TDEE)。

    BMR 用 Mifflin-St Jeor 公式（目前学界较常用、误差较小的公式）；
    TDEE = BMR × 活动系数（久坐1.2 / 轻度1.375 / 中度1.55 / 高度1.725）。
    """
    if gender == "男":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161

    # 活动系数表：键是 1~4 的整数，对应表单里的活动水平
    activity_factors = {1: 1.2, 2: 1.375, 3: 1.55, 4: 1.725}
    tdee = bmr * activity_factors[activity_level]
    return round(bmr), round(tdee)


def build_plan_52(bmr, tdee, gender):
    """构建“5+2 断食法”计划数据（只算数字，返回字典，不拼 HTML）。"""
    normal_day_min = tdee - 100
    normal_day_max = tdee + 100
    fasting_simple = 600 if gender == "男" else 500      # 新手简化版固定值
    fasting_precise_min = round(bmr * 0.2)               # 精准版按 BMR 的 20%~25%
    fasting_precise_max = round(bmr * 0.25)
    safety_floor = round(bmr * 0.2)                      # 断食日热量绝对下限（安全线）

    return {
        "plan_type": "52",                               # 模板据此分支渲染哪种结果
        "bmr": bmr, "tdee": tdee,
        "normal_day_min": normal_day_min, "normal_day_max": normal_day_max,
        "fasting_simple": fasting_simple,
        "fasting_precise_min": fasting_precise_min,
        "fasting_precise_max": fasting_precise_max,
        "safety_floor": safety_floor,
    }


def build_plan_carbon(bmr, tdee, weight, cycle_mode, protein_level):
    """构建“碳循环减脂法”计划数据（只算数字，返回字典）。"""
    # —— 蛋白质（全程固定）：体重 × 蛋白质标准(g/kg) × 4大卡/g ——
    protein_g = round(weight * protein_level)
    protein_kcal = protein_g * 4

    # —— 三种日的脂肪(g)与碳水(g)，以及它们折算的大卡 ——
    # 低碳日脂肪多、碳水少；高碳日反之。1g 脂肪=9大卡，1g 碳水=4大卡。
    fat_low_g,  fat_low_kcal  = round(weight * 1.2), round(weight * 1.2 * 9)
    fat_mid_g,  fat_mid_kcal  = round(weight * 1.0), round(weight * 1.0 * 9)
    fat_high_g, fat_high_kcal = round(weight * 0.8), round(weight * 0.8 * 9)
    carb_low_g,  carb_low_kcal  = round(weight * 0.8), round(weight * 0.8 * 4)
    carb_mid_g,  carb_mid_kcal  = round(weight * 2.5), round(weight * 2.5 * 4)
    carb_high_g, carb_high_kcal = round(weight * 5.0), round(weight * 5.0 * 4)

    low_day_total  = protein_kcal + fat_low_kcal  + carb_low_kcal
    mid_day_total  = protein_kcal + fat_mid_kcal  + carb_mid_kcal
    high_day_total = protein_kcal + fat_high_kcal + carb_high_kcal

    # —— 每周循环：模式1 是 5 天循环(3低+1中+1高)，模式2 是 7 天循环 ——
    if cycle_mode == 1:
        weekly_total = 3 * low_day_total + 1 * mid_day_total + 1 * high_day_total
        weekly_days = 5
        cycle_name = "3+1+1 5天循环（新手首选）"
    else:
        weekly_total = 4 * low_day_total + 2 * mid_day_total + 1 * high_day_total
        weekly_days = 7
        cycle_name = "经典7天循环（有基础者）"

    avg_daily = round(weekly_total / weekly_days)
    weekly_deficit = tdee * weekly_days - weekly_total     # 每周热量缺口
    weekly_fat_loss = round(weekly_deficit / 7700, 2)      # 7700大卡≈1kg脂肪

    return {
        "plan_type": "carbon",
        "bmr": bmr, "tdee": tdee,
        "protein_g": protein_g, "protein_kcal": protein_kcal,
        "protein_level": protein_level,
        # 把每天的数据再包一层字典，模板用 result_data.low_day.total 这样取，结构更清晰
        "low_day":  {"carbon_g": carb_low_g,  "fat_g": fat_low_g,  "total": low_day_total},
        "mid_day":  {"carbon_g": carb_mid_g,  "fat_g": fat_mid_g,  "total": mid_day_total},
        "high_day": {"carbon_g": carb_high_g, "fat_g": fat_high_g, "total": high_day_total},
        "cycle_name": cycle_name,
        "weekly_total": weekly_total, "avg_daily": avg_daily,
        "weekly_deficit": weekly_deficit, "weekly_fat_loss": weekly_fat_loss,
    }


# ----------------------------------------------------------------------------
# 路由（URL → 处理函数）
# ----------------------------------------------------------------------------

@app.route("/")
def home():
    # 首页：直接渲染继承母版后的 home.html
    return render_template("home.html")


@app.route("/food-calories")
def food_calories():
    # 食物页：把 JSON 数据整体传给模板，模板用 {% for %} 循环渲染
    return render_template("food_calories.html", data=load_json("foods.json"))


@app.route("/takeout-calories")
def takeout_calories():
    return render_template("takeout_calories.html", data=load_json("takeout.json"))


@app.route("/calculator", methods=["GET", "POST"])
def calculator():
    # 从 URL 参数拿默认计算器类型（首页点不同按钮会带不同的 type）
    default_type = request.args.get("type", "52")
    result_data = None   # 计算结果数据（字典），没有则 None
    error_msg = None     # 出错时的提示，没有则 None

    if request.method == "POST":
        try:
            # 从表单取字段并转型；任何转型失败或字段缺失都会抛异常
            gender = request.form["gender"]
            weight = float(request.form["weight"])
            height = float(request.form["height"])
            age = int(request.form["age"])
            activity_level = int(request.form["activity_level"])
            calculator_type = request.form["calculator_type"]

            bmr, tdee = calculate_bmr_tdee(gender, weight, height, age, activity_level)

            if calculator_type == "52":
                result_data = build_plan_52(bmr, tdee, gender)
            else:
                cycle_mode = int(request.form["cycle_mode"])
                protein_level = float(request.form["protein_level"])
                result_data = build_plan_carbon(bmr, tdee, weight, cycle_mode, protein_level)

        # 相比原来只拦 ValueError，这里多拦 KeyError（字段缺失也会触发），
        # 并给一句人话提示，而不是把崩溃栈甩给用户。
        except (ValueError, KeyError):
            error_msg = "请输入有效的数字，并完整填写所有字段。"
        except Exception:
            error_msg = "计算出错了，请稍后重试。"

    # 把数据（和错误提示、默认类型）一起交给模板；模板负责画卡片
    return render_template(
        "index.html",
        result_data=result_data,
        error_msg=error_msg,
        default_type=default_type,
    )


# ----------------------------------------------------------------------------
# JSON 接口层（优先级4）：让前端 JS / 移动端 / AI 助手都能复用计算与问答
# ----------------------------------------------------------------------------

@app.route("/api/calculate", methods=["POST"])
def api_calculate():
    """供前端 JS 调用的计算接口，返回 JSON 而不是 HTML 页面。"""
    data = request.get_json(silent=True) or {}   # silent=True：非 JSON 也不报错，返回 None
    try:
        gender = data["gender"]
        weight = float(data["weight"])
        height = float(data["height"])
        age = int(data["age"])
        activity_level = int(data["activity_level"])
        calculator_type = data["calculator_type"]

        bmr, tdee = calculate_bmr_tdee(gender, weight, height, age, activity_level)
        if calculator_type == "52":
            plan = build_plan_52(bmr, tdee, gender)
        else:
            plan = build_plan_carbon(
                bmr, tdee, weight,
                int(data["cycle_mode"]), float(data["protein_level"]),
            )
        return jsonify({"ok": True, "data": plan})
    except (ValueError, KeyError):
        return jsonify({"ok": False, "error": "参数无效，请检查输入。"}), 400
    except Exception:
        return jsonify({"ok": False, "error": "服务器内部错误。"}), 500


@app.route("/api/ask", methods=["POST"])
def api_ask():
    """AI 助手接口：把用户问题转发给独立的 RAG 检索问答服务。

    为什么不在本文件里直接做检索：
      RAG 服务(FastAPI)已经封装好“向量检索 + 拼 prompt + 调大模型”的完整链路，
      并自带 Chroma 向量库和 DeepSeek 调用。减脂网站直接复用它，避免重复造轮子、
      也避免把 chromadb/fastembed 这些重依赖引到这个轻量站点里。
    """
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()

    # 入参校验：问题太短直接回绝，不浪费一次大模型调用
    if len(question) < 2:
        return jsonify({"answer": "请输入至少 2 个字的问题。", "citations": []}), 400

    try:
        # 用 requests 把问题 POST 给 RAG 服务的 /ask，并把它的 JSON 原样回传给前端
        resp = requests.post(
            RAG_SERVICE_URL,
            json={"question": question, "top_k": 5},
            timeout=30,   # 必须设超时：RAG 服务卡住时不能让连接一直挂着
        )
        resp.raise_for_status()               # 非 2xx 状态码也抛异常（走下面兜底）
        return jsonify(resp.json())           # RAG 服务返回 {answer, citations}

    except requests.exceptions.ConnectionError:
        # RAG 服务根本没启动（最常见情况）：给明确指引而不是泛泛的“不可用”
        return jsonify({
            "answer": "AI 助手服务未启动，请先运行 RAG 服务（项目目录）：python run.py",
            "citations": [],
        }), 503
    except requests.exceptions.Timeout:
        return jsonify({"answer": "AI 助手响应超时，请稍后重试。", "citations": []}), 504
    except Exception:
        return jsonify({"answer": "AI 助手暂时不可用，请稍后重试。", "citations": []}), 503


if __name__ == "__main__":
    # debug=True 方便开发时改代码自动重启；正式部署要关掉
    app.run(debug=True, port=5000)
