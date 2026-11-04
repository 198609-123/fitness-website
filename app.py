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

import math
import os
import json
import logging
import requests  # 用来把请求转发给 RAG 服务（HTTP 客户端库）
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# 技术细节写日志、不写页面：排查时看控制台，用户只会看到“助手暂时休息”
logger = logging.getLogger(__name__)

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


def calculate_bmi(weight, height):
    """BMI = 体重(kg) / 身高(m)^2。

    除了除零，还要拦住 inf / nan：表单里输入 1e309 会被 float() 解析成 inf，
    而 round(inf, 1) 仍然返回 inf，页面上就会出现“BMI inf”这种脏数据。
    """
    if not height or height <= 0:
        return None
    if not math.isfinite(weight) or not math.isfinite(height):
        return None
    return round(weight / ((height / 100) ** 2), 1)


class InputRangeError(ValueError):
    """输入超出可计算范围（含 inf / nan）。

    单独定义是为了给用户一句具体的提示，
    而不是和“填了字母”一起落到那句很笼统的“请输入有效的数字”。
    """


def build_goal(weight, height, target_weight, target_date_str):
    """生成“目标—时间”路线图（纯函数，不碰模板）。

    安全速度按每周 0.5%–1% 体重估算；用户填的日期如果要求更快，
    返回 too_fast 并给出“最早安全日期”，不直接拒绝整个方案。
    返回 (goal_dict, message)：message 用于目标不合法时的提示。
    """
    today = date.today()

    if target_weight is None:
        return None, None
    if target_weight <= 0:
        return None, "目标体重需要大于 0，本次未生成路线图。"
    if target_weight >= weight:
        return None, "目标体重需要低于当前体重，本次未生成路线图。"

    total_loss = round(weight - target_weight, 1)
    if total_loss < 0.3:
        return None, "目标与当前体重接近，暂不需要减脂路线。"

    target_bmi = calculate_bmi(target_weight, height)
    if target_bmi is not None and target_bmi < 18.5:
        return None, f"目标 BMI 为 {target_bmi}，低于 18.5，不建议设定这个目标。"

    weekly_min = round(weight * 0.005, 2)
    weekly_max = round(weight * 0.01, 2)
    preferred_rate = weight * 0.0075

    weeks_preferred = max(1, math.ceil(total_loss / preferred_rate))
    weeks_min = max(1, math.ceil(total_loss / weekly_max))
    weeks_max = max(1, math.ceil(total_loss / weekly_min))
    projected_date = today + timedelta(weeks=weeks_preferred)

    target_date = None
    date_error = None
    if target_date_str:
        try:
            target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
            if target_date <= today:
                date_error = "目标日期需要晚于今天，已按建议速度估算。"
                target_date = None
        except ValueError:
            date_error = "目标日期格式无效，已按建议速度估算。"

    status = "projected"
    required_weekly = round(total_loss / weeks_preferred, 2)
    suggested_date = projected_date

    if target_date:
        days = (target_date - today).days
        weeks = max(days / 7, 0.5)
        required_weekly = round(total_loss / weeks, 2)
        if required_weekly > weekly_max:
            status = "too_fast"
            # 最早安全日期：按上限速度需要的最少周数
            suggested_date = today + timedelta(weeks=weeks_min)
        elif required_weekly < weekly_min:
            status = "too_slow"
            suggested_date = projected_date
        else:
            status = "ok"
            suggested_date = target_date

    return {
        "target_weight": round(target_weight, 1),
        "target_bmi": target_bmi,
        "total_loss": total_loss,
        "weekly_min": weekly_min,
        "weekly_max": weekly_max,
        "weeks_min": weeks_min,
        "weeks_max": weeks_max,
        "projected_weeks": weeks_preferred,
        "projected_date": projected_date.isoformat(),
        "target_date": target_date.isoformat() if target_date else None,
        "required_weekly": required_weekly,
        "required_daily_deficit": round(required_weekly * 7700 / 7),
        "status": status,
        "suggested_date": suggested_date.isoformat(),
        "date_error": date_error,
    }, None


def build_plan_52(bmr, tdee, gender):
    """构建“5+2 断食法”计划数据（只算数字，返回字典，不拼 HTML）。

    断食日按 TDEE 的 25% 估算，并夹在 500–600（女）/ 600–700（男）大卡之间。
    为什么不再用 BMR×20%：
      BMR 只是“躺着不动”的消耗，用它乘 20% 会得到 260–350 大卡这种
      低于 5:2 通行下限的数字；把同一个值再写成“安全线”，等于让用户
      贴着悬崖走。现在 safety_floor 直接取区间下限，推荐值不会低于它。
    """
    normal_day_min = tdee - 100
    normal_day_max = tdee + 100

    floor = 600 if gender == "男" else 500          # 断食日下限，也是安全线
    cap = 700 if gender == "男" else 600            # 断食日上限，避免大体重男性算得过高
    target = round(tdee * 0.25)                     # 推荐的断食日热量

    fasting_simple = floor                          # 新手简化版：直接用下限
    fasting_precise_min = floor
    fasting_precise_max = max(floor, min(cap, target))
    safety_floor = floor

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


@app.route("/method")
def method():
    # 方法与数据页：把公式、误差、适用边界集中在一处，方便被引用和核对
    return render_template("method.html")


@app.route("/calculator", methods=["GET", "POST"])
def calculator():
    # 从 URL 参数拿默认计算器类型（首页点不同按钮会带不同的 type）
    default_type = request.args.get("type", "52")
    result_data = None       # 计算结果数据（字典），没有则 None
    error_msg = None         # 输入错误提示
    screening_reasons = []   # 准入筛查命中项：有值就不出方案
    bmi = None
    goal_data = None         # 目标路线图（选填）
    goal_msg = None          # 目标不合法时的提示，不阻断主方案

    if request.method == "POST":
        try:
            # 从表单取字段并转型；任何转型失败或字段缺失都会抛异常
            gender = request.form["gender"]
            weight = float(request.form["weight"])
            height = float(request.form["height"])
            age = int(request.form["age"])
            activity_level = int(request.form["activity_level"])
            calculator_type = request.form["calculator_type"]

            # 0 / 负数会让 BMI、BMR 失去意义；inf / nan 还会让 round() 抛 OverflowError。
            # 这里给足正常人体区间的余量，只拦明显不可能的数，避免误伤真实用户。
            if not (25 <= weight <= 350) or not (100 <= height <= 250) or not (10 <= age <= 120):
                raise InputRangeError("体重、身高或年龄超出可计算范围，请检查输入。")

            bmi = calculate_bmi(weight, height)

            # 准入筛查：命中任意一项，都不计算热量、不给目标
            if age < 18:
                screening_reasons.append("未满 18 岁")
            if request.form.get("pregnant"):
                screening_reasons.append("孕期或哺乳期")
            if request.form.get("eating_disorder"):
                screening_reasons.append("有进食障碍史")
            if request.form.get("chronic"):
                screening_reasons.append("有糖尿病、低血糖或其他需控制饮食的慢性病")
            if bmi is not None and bmi < 18.5:
                screening_reasons.append(f"BMI 为 {bmi}，低于 18.5")

            # 目标（选填）：先校验目标 BMI，再算路线图
            target_weight_raw = request.form.get("target_weight", "").strip()
            target_date_raw = request.form.get("target_date", "").strip()
            if target_weight_raw:
                try:
                    target_weight = float(target_weight_raw)
                except ValueError:
                    target_weight = None
                    goal_msg = "目标体重需要填写数字，本次未生成路线图。"

                if target_weight is not None and not math.isfinite(target_weight):
                    target_weight = None
                    goal_msg = "目标体重不是有效数字，本次未生成路线图。"

                if target_weight is not None:
                    target_bmi = calculate_bmi(target_weight, height)
                    if target_bmi is not None and target_bmi < 18.5:
                        screening_reasons.append(f"目标 BMI 为 {target_bmi}，低于 18.5")
                    else:
                        goal_data, goal_msg = build_goal(weight, height, target_weight, target_date_raw)

            if screening_reasons:
                # 被筛查拦下时不展示任何“目标/热量”数字，避免用户误用
                goal_data = None
            else:
                bmr, tdee = calculate_bmr_tdee(gender, weight, height, age, activity_level)

                if calculator_type == "52":
                    result_data = build_plan_52(bmr, tdee, gender)
                else:
                    cycle_mode = int(request.form["cycle_mode"])
                    protein_level = float(request.form["protein_level"])
                    result_data = build_plan_carbon(bmr, tdee, weight, cycle_mode, protein_level)

        # 相比原来只拦 ValueError，这里多拦 KeyError（字段缺失也会触发），
        # 并给一句人话提示，而不是把崩溃栈甩给用户。
        except InputRangeError as exc:
            error_msg = str(exc)
        except (ValueError, KeyError):
            error_msg = "请输入有效的数字，并完整填写所有字段。"
        except Exception:
            error_msg = "计算出错了，请稍后重试。"

    # 把数据（和错误提示、默认类型）一起交给模板；模板负责画卡片。
    # form_data 用于把用户刚填的值回填到表单，避免“算完一次、6 个字段全清空”。
    return render_template(
        "index.html",
        result_data=result_data,
        error_msg=error_msg,
        default_type=default_type,
        form_data=request.form if request.method == "POST" else {},
        gender=request.form.get("gender"),
        bmi=bmi,
        screening_reasons=screening_reasons,
        goal_data=goal_data,
        goal_msg=goal_msg,
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

        if not (25 <= weight <= 350) or not (100 <= height <= 250) or not (10 <= age <= 120):
            return jsonify({"ok": False, "error": "体重、身高或年龄超出可计算范围。"}), 400

        bmi = calculate_bmi(weight, height)

        # 和网页端保持同一套准入规则，避免绕过表单直接调接口
        if age < 18:
            return jsonify({"ok": False, "error": "未满 18 岁不建议使用减脂计算。"}), 400
        if bmi is not None and bmi < 18.5:
            return jsonify({"ok": False, "error": f"BMI 为 {bmi}，低于 18.5，不建议减脂。"}), 400
        if any(data.get(key) for key in ("pregnant", "eating_disorder", "chronic")):
            return jsonify({"ok": False, "error": "孕期/哺乳期、进食障碍史或需控制饮食的慢性病，请先咨询医生。"}), 400

        goal = None
        goal_error = None
        target_weight = data.get("target_weight")
        if target_weight not in (None, ""):
            target_weight = float(target_weight)
            target_bmi = calculate_bmi(target_weight, height)
            if target_bmi is not None and target_bmi < 18.5:
                return jsonify({"ok": False, "error": f"目标 BMI 为 {target_bmi}，低于 18.5。"}), 400
            goal, goal_error = build_goal(
                weight, height, target_weight, str(data.get("target_date") or "").strip()
            )

        bmr, tdee = calculate_bmr_tdee(gender, weight, height, age, activity_level)
        if calculator_type == "52":
            plan = build_plan_52(bmr, tdee, gender)
        else:
            plan = build_plan_carbon(
                bmr, tdee, weight,
                int(data["cycle_mode"]), float(data["protein_level"]),
            )

        payload = dict(plan)
        payload["bmi"] = bmi
        if goal:
            payload["goal"] = goal
        if goal_error:
            payload["goal_error"] = goal_error
        return jsonify({"ok": True, "data": payload})
    except (ValueError, KeyError):
        return jsonify({"ok": False, "error": "参数无效，请检查输入。"}), 400
    except Exception:
        return jsonify({"ok": False, "error": "服务器内部错误。"}), 500


# AI 助手兜底话术：RAG 服务不可用时，用户看到的应该是“人话 + 能做的事”，
# 而不是“请先运行 python run.py”这种只有开发者才懂的运维指令。
ASSISTANT_FALLBACK_QUESTIONS = [
    "减脂每天该吃多少蛋白质？",
    "断食日 500 大卡可以怎么吃？",
    "连续两周体重不动怎么办？",
]

# RAG 服务的 AskRequest 只接受一个 question 字段（max_length=500），
# 所以本机方案摘要只能拼进问题里，并且必须整体控制在 500 字以内，
# 否则会触发 422（参数校验失败），用户会看到“助手出错”而不是答案。
RAG_QUESTION_MAX = 500
RAG_CONTEXT_MAX = 160


def _rag_request_options():
    """对回环地址关闭系统代理。

    装了 Clash / 公司 VPN 之类的工具后，环境变量 HTTP_PROXY 会被 requests 自动读取，
    连 127.0.0.1:8000 的请求也会被塞进代理，现象是“RAG 明明启动着，
    助手却一直报 502 / 连接失败”。这里只对回环地址绕过代理，
    远程地址（RAG_SERVICE_URL 指向别的机器时）仍然走系统代理。
    """
    host = (urlparse(RAG_SERVICE_URL).hostname or "").lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return {"proxies": {"http": None, "https": None}}
    return {}


def _degraded_answer(message, status=503):
    """统一的“降级”响应：前端据此展示常见问题与重试按钮。"""
    return jsonify({
        "answer": message,
        "citations": [],
        "degraded": True,
        "suggestions": ASSISTANT_FALLBACK_QUESTIONS,
    }), status


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_rag_question(question, context):
    """把本机方案摘要并进问题，并保证总长度不超过 RAG 的上限。"""
    question = (question or "").strip()[:RAG_QUESTION_MAX]
    context = (context or "").strip()
    if not context:
        return question

    prefix = f"（我的情况：{context[:RAG_CONTEXT_MAX]}）\n"
    room = RAG_QUESTION_MAX - len(prefix)
    if room < 10:                      # 前缀太长就直接放弃上下文，保问题本身
        return question
    return prefix + question[:room]


def _normalize_rag_payload(payload):
    """收敛 RAG 返回的结构。

    RAG 服务是独立进程，字段缺失或类型异常时不能让前端崩：
    前端会直接调用 score.toFixed()，一个 null 就会把整段渲染打断。
    这种脏数据一律按“没拿到有效答案”处理，交给上层走降级。
    """
    if not isinstance(payload, dict):
        return None

    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return None

    citations = []
    raw = payload.get("citations")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            citations.append({
                "source_file": str(item.get("source_file") or "未知文档"),
                "page": _to_int(item.get("page")),
                "score": _to_float(item.get("score")),
            })

    return {"answer": answer.strip(), "citations": citations}


@app.route("/api/ask", methods=["POST"])
def api_ask():
    """AI 助手接口：把用户问题转发给独立的 RAG 检索问答服务。

    为什么不在本文件里直接做检索：
      RAG 服务(FastAPI)已经封装好“向量检索 + 拼 prompt + 调大模型”的完整链路，
      并自带 Chroma 向量库和 DeepSeek 调用。减脂网站直接复用它，避免重复造轮子、
      也避免把 chromadb/fastembed 这些重依赖引到这个轻量站点里。

    降级原则：
      技术细节（服务没启动、超时、状态码）只写日志；
      用户只看到“助手暂时休息 + 可以先问什么”，技术故障不等于这家网站不可信。
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}

    question = data.get("question")
    question = question.strip() if isinstance(question, str) else ""

    context = data.get("context")
    context = context.strip() if isinstance(context, str) else ""

    # 入参校验：问题太短直接回绝，不浪费一次大模型调用
    if len(question) < 2:
        return jsonify({
            "answer": "请输入至少 2 个字的问题。",
            "citations": [],
            "degraded": False,
            "suggestions": [],
        }), 400

    try:
        resp = requests.post(
            RAG_SERVICE_URL,
            json={"question": _build_rag_question(question, context), "top_k": 5},
            timeout=30,   # 必须设超时：RAG 服务卡住时不能让连接一直挂着
            **_rag_request_options(),   # 本地服务绕过系统代理，否则会被代理吞掉
        )

        # 5xx 是服务端问题，先按“服务不可用”处理，不要当成问题本身有错
        if resp.status_code >= 500:
            logger.warning("RAG 服务返回 %s：%s", resp.status_code, resp.text[:200])
            return _degraded_answer("AI 助手暂时休息，你仍然可以先看下面这些常见问题。")

        if resp.status_code >= 400:
            logger.warning("RAG 服务拒绝了请求 %s：%s", resp.status_code, resp.text[:200])
            return _degraded_answer("这个问题超出了助手能回答的范围，换一个说法试试。")

        try:
            payload = resp.json()
        except ValueError:
            logger.warning("RAG 返回了非 JSON 内容：%s", resp.text[:200])
            payload = None

        normalized = _normalize_rag_payload(payload)
        if normalized:
            normalized["degraded"] = False
            normalized["suggestions"] = []
            return jsonify(normalized)

        logger.warning("RAG 返回结构不符合预期：%r", payload)
        return _degraded_answer("助手这次没能组织好答案，换个说法再问一次试试。")

    except requests.exceptions.ConnectionError as exc:
        # RAG 服务根本没启动（最常见情况）：细节进日志，用户只看到可执行的替代方案
        logger.warning("RAG 服务连接失败：%s", exc)
        return _degraded_answer("AI 助手暂时休息，你仍然可以先看下面这些常见问题。")

    except requests.exceptions.Timeout as exc:
        logger.warning("RAG 服务超时：%s", exc)
        return _degraded_answer("AI 助手响应有点慢，稍等几秒再试一次。", status=504)

    except Exception as exc:      # noqa: BLE001 —— 兜底必须拦住一切，不能把栈甩给前端
        logger.exception("AI 助手未知错误：%s", exc)
        return _degraded_answer("AI 助手暂时不可用，请稍后重试。")


if __name__ == "__main__":
    # debug=True 方便开发时改代码自动重启；正式部署要关掉
    app.run(debug=True, port=5000)
