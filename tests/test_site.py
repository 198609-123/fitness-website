# ============================================================================
# test_site.py —— 减脂网站的最小回归测试
# ----------------------------------------------------------------------------
# 为什么用 unittest 而不是 pytest：
#   这个项目目前的依赖只有 Flask 和 requests，为了跑测试再装一个 pytest
#   并不划算。unittest 是标准库，任何一台装了 Python 的机器都能跑。
#
# 运行方式（在项目根目录）：
#   .venv\Scripts\python.exe -m unittest discover -s tests -v
#
# 覆盖范围（只测“改坏了会出事”的部分，不追求行覆盖率）：
#   1. 计算：BMR/TDEE、5+2 断食日区间、碳循环三档
#   2. 目标路线图：过激 / 合理 / 过松 / 非法目标
#   3. 准入筛查：未成年、低 BMI、孕哺 → 不出方案
#   4. 路由与表单回显：5 条页面 + 两条 POST
#   5. 接口：/api/calculate 数值与 400，/api/ask 降级不外泄技术细节
#   6. 数据：条数、schema、份量换算误差
# ============================================================================

import json
import logging
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

# 让 `python -m unittest discover -s tests` 能 import 到项目根目录的 app.py
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

import app as site  # noqa: E402

# /api/ask 的降级用例会故意触发 warning 日志，
# 测试输出里不需要这些“预期内的报错”，否则真正的失败容易被淹没。
_app_logger = logging.getLogger("app")
_app_logger.addHandler(logging.NullHandler())
_app_logger.propagate = False


MALE = dict(calculator_type="52", gender="男", weight="75", height="178", age="25", activity_level="3")
FEMALE = dict(calculator_type="52", gender="女", weight="60", height="165", age="28", activity_level="2")


class SiteTestCase(unittest.TestCase):
    """所有用例共用的 Flask 测试客户端。"""

    @classmethod
    def setUpClass(cls):
        site.app.config.update(TESTING=True)
        cls.client = site.app.test_client()

    def post_calc(self, **overrides):
        """提交计算表单，返回 (状态码, HTML)。"""
        data = dict(MALE)
        data.update(overrides)
        resp = self.client.post("/calculator", data=data)
        return resp.status_code, resp.get_data(as_text=True)


class TestCalculations(SiteTestCase):
    def test_bmr_and_tdee(self):
        bmr, tdee = site.calculate_bmr_tdee("男", 75, 178, 25, 3)
        self.assertEqual((bmr, tdee), (1742, 2701))

        bmr_f, tdee_f = site.calculate_bmr_tdee("女", 60, 165, 28, 2)
        self.assertEqual((bmr_f, tdee_f), (1330, 1829))

    def test_plan_52_respects_floor(self):
        """断食日必须夹在安全区间内，且安全线就是区间下限（P0-7 的回归点）。"""
        male = site.build_plan_52(*site.calculate_bmr_tdee("男", 75, 178, 25, 3), "男")
        self.assertEqual((male["fasting_precise_min"], male["fasting_precise_max"]), (600, 675))
        self.assertEqual(male["safety_floor"], 600)

        female = site.build_plan_52(*site.calculate_bmr_tdee("女", 60, 165, 28, 2), "女")
        self.assertEqual((female["fasting_precise_min"], female["fasting_precise_max"]), (500, 500))
        self.assertEqual(female["safety_floor"], 500)

        for plan in (male, female):
            self.assertGreaterEqual(plan["fasting_precise_min"], plan["safety_floor"])
            self.assertLessEqual(plan["fasting_precise_min"], plan["fasting_precise_max"])

    def test_plan_52_small_person_still_floored(self):
        """体重很轻的人按 TDEE×25% 算会低于 500，必须被下限托住。"""
        plan = site.build_plan_52(*site.calculate_bmr_tdee("女", 45, 155, 30, 1), "女")
        self.assertEqual(plan["fasting_precise_min"], 500)
        self.assertEqual(plan["safety_floor"], 500)

    def test_plan_carbon(self):
        plan = site.build_plan_carbon(*site.calculate_bmr_tdee("男", 75, 178, 25, 3), 75, 1, 2.0)
        totals = (plan["low_day"]["total"], plan["mid_day"]["total"], plan["high_day"]["total"])
        self.assertEqual(totals, (1650, 2025, 2640))
        self.assertEqual(plan["weekly_fat_loss"], 0.51)

    def test_bmi_handles_invalid_numbers(self):
        self.assertEqual(site.calculate_bmi(75, 178), 23.7)
        self.assertIsNone(site.calculate_bmi(75, 0))
        self.assertIsNone(site.calculate_bmi(float("inf"), 178))
        self.assertIsNone(site.calculate_bmi(75, float("nan")))


class TestGoal(SiteTestCase):
    def test_no_target_returns_empty(self):
        self.assertEqual(site.build_goal(75, 178, None, ""), (None, None))

    def test_projected_without_date(self):
        goal, message = site.build_goal(75, 178, 68, "")
        self.assertIsNone(message)
        self.assertEqual(goal["total_loss"], 7.0)
        self.assertEqual(goal["status"], "projected")
        self.assertTrue(goal["projected_date"] > date.today().isoformat())

    def test_too_fast_date(self):
        target = (date.today() + timedelta(weeks=6)).isoformat()
        goal, _ = site.build_goal(75, 178, 68, target)
        self.assertEqual(goal["status"], "too_fast")
        self.assertGreater(goal["required_weekly"], goal["weekly_max"])
        self.assertGreater(goal["suggested_date"], target)

    def test_ok_date(self):
        target = (date.today() + timedelta(weeks=14)).isoformat()
        goal, _ = site.build_goal(75, 178, 68, target)
        self.assertEqual(goal["status"], "ok")

    def test_too_slow_date(self):
        target = (date.today() + timedelta(weeks=24)).isoformat()
        goal, _ = site.build_goal(75, 178, 68, target)
        self.assertEqual(goal["status"], "too_slow")

    def test_invalid_targets(self):
        goal, message = site.build_goal(75, 178, 80, "")       # 目标比现在重
        self.assertIsNone(goal)
        self.assertIn("低于当前体重", message)

        goal, message = site.build_goal(75, 178, 50, "")       # 目标 BMI < 18.5
        self.assertIsNone(goal)
        self.assertIn("18.5", message)


class TestScreening(SiteTestCase):
    def test_minor_gets_no_plan(self):
        _, html = self.post_calc(age="17")
        self.assertIn("暂不建议使用减脂方案", html)
        self.assertIn("未满 18 岁", html)
        self.assertNotIn('id="plan-data"', html)      # 不给方案，也不写入本机计划
        self.assertNotIn("基础代谢率", html)

    def test_low_bmi_gets_no_plan(self):
        _, html = self.post_calc(weight="54")         # BMI 17.0
        self.assertIn("暂不建议使用减脂方案", html)
        self.assertIn("BMI", html)

    def test_pregnant_gets_no_plan(self):
        _, html = self.post_calc(pregnant="1")
        self.assertIn("孕期或哺乳期", html)
        self.assertNotIn("基础代谢率", html)

    def test_normal_user_gets_plan(self):
        _, html = self.post_calc()
        self.assertIn("基础代谢率", html)
        self.assertNotIn("暂不建议使用减脂方案", html)
        self.assertIn('id="plan-data"', html)


class TestPages(SiteTestCase):
    def test_routes_return_200(self):
        for url in ("/", "/food-calories", "/takeout-calories", "/calculator", "/method"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_form_values_are_repopulated(self):
        _, html = self.post_calc(weight="75")
        self.assertIn('value="75"', html)
        self.assertIn('value="178"', html)
        self.assertIn('value="3" selected', html)
        self.assertIn('id="result"', html)

    def test_fasting_copy_by_gender(self):
        _, male = self.post_calc()
        self.assertNotIn("月经", male)                 # 男性不该出现妇科表述
        self.assertIn("600 - 675", male)

        _, female = self.post_calc(**FEMALE)
        self.assertIn("已取安全下限", female)          # 500 与 500 相同，渲染成单值
        self.assertNotIn("266", female)

    def test_food_and_takeout_counts(self):
        food = self.client.get("/food-calories").get_data(as_text=True)
        self.assertEqual(food.count('class="food-card"'), 23)

        takeout = self.client.get("/takeout-calories").get_data(as_text=True)
        self.assertEqual(takeout.count('class="food-card"'), 14)
        self.assertEqual(takeout.count("portion-select"), 14)

    def test_home_tells_users_data_is_local(self):
        """共用设备上，第二个打开的人必须一眼看到“仅存本机”和清除入口。"""
        home = self.client.get("/").get_data(as_text=True)
        self.assertIn("仅存本机", home)
        self.assertIn('id="dashClearTop"', home)
        self.assertIn("不是我的数据", home)

    def test_variants_render_on_cards(self):
        """多种可能对应的食物，卡片里必须能看到“其他情况”，而不是只留一个值。"""
        food = self.client.get("/food-calories").get_data(as_text=True)
        self.assertEqual(food.count('class="food-variants"'), 5)
        for label in ("红心", "白心", "紫薯", "全脂", "脱脂", "蛋清", "蛋黄", "炒花生"):
            self.assertIn(label, food)

        takeout = self.client.get("/takeout-calories").get_data(as_text=True)
        self.assertIn("其他份量", takeout)
        self.assertIn("小份", takeout)
        self.assertIn("大份", takeout)


class TestApiCalculate(SiteTestCase):
    def test_plan_52_keys(self):
        resp = self.client.post("/api/calculate", json=MALE)
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload["ok"])
        data = payload["data"]
        for key in (
            "plan_type", "bmr", "tdee", "normal_day_min", "normal_day_max",
            "fasting_simple", "fasting_precise_min", "fasting_precise_max", "safety_floor",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["fasting_precise_min"], 600)

    def test_invalid_inputs(self):
        for payload in (
            dict(MALE, weight=0),
            dict(MALE, weight=999),
            dict(MALE, age=17),
            dict(MALE, weight=54),        # BMI 17.0
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/api/calculate", json=payload).status_code, 400)

    def test_goal_is_attached_when_provided(self):
        target = (date.today() + timedelta(weeks=6)).isoformat()
        resp = self.client.post("/api/calculate", json=dict(MALE, target_weight=68, target_date=target))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["data"]["goal"]["status"], "too_fast")


class TestApiAsk(SiteTestCase):
    # 所有降级分支共用同一套断言：
    # 状态码、degraded 标记、常见问题条数，以及“不许出现技术词”。
    # 只测其中一条分支是不够的——文案是分支里各写各的，漏一条就等于漏一类事故。
    LEAKS = ("python", "run.py", "RAG", "Traceback", "8000", "127.0.0.1")

    def assert_degraded(self, resp):
        self.assertIn(resp.status_code, (503, 504))
        body = resp.get_json()
        self.assertTrue(body["degraded"])
        self.assertEqual(len(body["suggestions"]), 3)
        self.assertTrue(body["answer"].strip())
        for leak in self.LEAKS:
            self.assertNotIn(leak, body["answer"], f"降级文案泄露了技术细节：{leak}")

    def ask_with(self, **patch_kwargs):
        with mock.patch.object(site.requests, "post", **patch_kwargs):
            return self.client.post("/api/ask", json={"question": "蛋白质吃多少"})

    def test_too_short_question(self):
        resp = self.client.post("/api/ask", json={"question": "a"})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()["degraded"])

    def test_connection_error_degrades(self):
        """RAG 没启动：用户不该看到 python run.py 这类运维指令。"""
        self.assert_degraded(self.ask_with(side_effect=requests.exceptions.ConnectionError()))

    def test_timeout_degrades(self):
        self.assert_degraded(self.ask_with(side_effect=requests.exceptions.Timeout()))

    def test_rag_500_degrades(self):
        self.assert_degraded(self.ask_with(return_value=mock.Mock(status_code=500, text="boom")))

    def test_rag_4xx_degrades(self):
        self.assert_degraded(self.ask_with(return_value=mock.Mock(status_code=422, text="validation error")))

    def test_non_json_response_degrades(self):
        fake = mock.Mock(status_code=200, text="<html>oops</html>")
        fake.json.side_effect = ValueError("not json")
        self.assert_degraded(self.ask_with(return_value=fake))

    def test_empty_answer_degrades(self):
        fake = mock.Mock(status_code=200, text="{}")
        fake.json.return_value = {"answer": "   ", "citations": None}
        self.assert_degraded(self.ask_with(return_value=fake))

    def test_dirty_payload_is_normalized(self):
        """RAG 返回 score=null、page 是字符串时，前端拿到的不该是脏数据。"""
        fake = mock.Mock(status_code=200, text="")
        fake.json.return_value = {
            "answer": "每天 1.6–2.2g/kg。",
            "citations": [
                {"source_file": "营养学.pdf", "page": "7", "score": None},
                "not-a-dict",
            ],
        }
        with mock.patch.object(site.requests, "post", return_value=fake):
            resp = self.client.post("/api/ask", json={"question": "蛋白质吃多少"})

        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(body["degraded"])
        self.assertEqual(body["citations"][0]["page"], 7)
        self.assertEqual(body["citations"][0]["score"], 0.0)
        self.assertEqual(len(body["citations"]), 1)   # 非 dict 的条目被丢掉

    def test_context_is_prefixed_and_capped(self):
        question = site._build_rag_question("还能吃多少", "75kg，目标 68kg，正常日 2701 大卡")
        self.assertIn("我的情况", question)
        self.assertIn("还能吃多少", question)

        # RAG 的 question 上限 500 字，超了会 422，所以必须截断
        long_question = site._build_rag_question("问" * 480, "上下文" * 100)
        self.assertLessEqual(len(long_question), 500)
        self.assertTrue(long_question.endswith("问"))


class TestDataFiles(unittest.TestCase):
    def load(self, name):
        return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))

    def test_food_schema(self):
        data = self.load("foods.json")
        items = [item for cat in data["categories"] for item in cat["foods"]]
        self.assertEqual(len(items), 23)

        for item in items:
            with self.subTest(name=item["name"]):
                self.assertIn(item.get("basis"), ("per_100g", "per_serving"))
                self.assertIn(item.get("state"), ("raw", "cooked", "ready"))
                for key in ("name", "calories", "note", "image"):
                    self.assertIn(key, item)

    def test_serving_calories_match(self):
        """每份热量必须和「每 100g × 克数」对得上，否则用户按份吃会算错。"""
        data = self.load("foods.json")
        for cat in data["categories"]:
            for item in cat["foods"]:
                serving = item.get("serving")
                if not serving or not serving.get("grams"):
                    continue
                expected = round(item["calories"] * serving["grams"] / 100)
                with self.subTest(name=item["name"]):
                    self.assertLessEqual(abs(expected - serving["calories"]), 2)

    def test_takeout_schema(self):
        data = self.load("takeout.json")
        items = [item for cat in data["categories"] for item in cat["foods"]]
        self.assertEqual(len(items), 14)

        for item in items:
            with self.subTest(name=item["name"]):
                self.assertEqual(item["basis"], "per_serving")
                self.assertEqual(item["serving"]["calories"], item["calories"])

    def test_variants_are_well_formed(self):
        """“其他情况”会被直接渲染到页面上，字段缺失会让卡片出现空行。"""
        for name in ("foods.json", "takeout.json"):
            data = self.load(name)
            for cat in data["categories"]:
                for item in cat["foods"]:
                    for variant in item.get("variants") or []:
                        with self.subTest(food=item["name"], variant=variant.get("label")):
                            self.assertIsInstance(variant.get("label"), str)
                            self.assertTrue(variant["label"].strip())
                            self.assertIsInstance(variant.get("calories"), int)
                            self.assertGreater(variant["calories"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
