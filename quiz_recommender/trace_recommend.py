"""유리상자 검증 — 현재 실데이터(Qdrant 1051~1210)로 개인화가 설계대로 도는지 추적.

라벨(관련성) 없이도 '기능이 매커니즘대로 동작하는가'는 증명된다:
시나리오를 손으로 짜서, 각 추천 문제의 (section, difficulty)와 사다리 상태·목표
난이도를 나란히 찍어 → ①이미 맞힌 문제 제외 ②사다리 난이도 적중 ③상태 전이를 눈으로 본다.

실행:  .venv\\Scripts\\python.exe trace_recommend.py
전제:  .env(QDRANT_URL/KEY) + 현재 인덱싱 상태 (course 87/88, 난이도 1/2/3)
"""
import sys
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))

# ── 손으로 짠 시나리오 (현재 실 id 기준) ──
# course 87 · sec135: d1[1081-83] d2[1084-87] d3[1088-90] / sec136: d1[1091-93] d2[1094-97] d3[1098-1100]
SCENARIOS = [
    {
        "name": "A · TOP (직전 라운드 전부 정답 → 상,상 기대)",
        "student_id": 7001, "query_id": 1084, "course_id": 87,
        "solved": [1085, 1086],
        "rounds": [
            {"section_id": 135, "answers": [(1085, 1), (1086, 1)]},   # 전부 정답 → TOP
            {"section_id": 135, "answers": [(1084, 0)]},              # 촉발 라운드(판정 제외)
        ],
    },
    {
        "name": "B · FLOOR (계속 틀림 → 원난이도,하 기대)",
        "student_id": 7002, "query_id": 1094, "course_id": 87,
        "solved": [],
        "rounds": [
            {"section_id": 136, "answers": [(1091, 0), (1092, 0)]},   # 틀림 → FLOOR
            {"section_id": 136, "answers": [(1094, 0)]},              # 촉발
        ],
    },
    {
        "name": "C · BASE (그 단원 첫 복습 → 원난이도,원+1 기대)",
        "student_id": 7003, "query_id": 1104, "course_id": 87,
        "solved": [],
        "rounds": [
            {"section_id": 137, "answers": [(1104, 0)]},              # 촉발뿐 → 판정용 이력 없음 = BASE
        ],
    },
    {
        "name": "D · 이미 맞힌 문제 제외 확인",
        "student_id": 7004, "query_id": 1084, "course_id": 87,
        "solved": [1088, 1089],                                       # d3 두 개를 이미 빠르게 맞힘
        "rounds": [
            {"section_id": 135, "answers": [(1088, 1), (1089, 1)]},   # 전부 정답 → TOP (상,상 원하지만 1088/89는 제외돼야)
            {"section_id": 135, "answers": [(1084, 0)]},
        ],
    },
]

_ALL = {s["student_id"]: s["rounds"] for s in SCENARIOS}
fake_db = types.ModuleType("db")
fake_db.get_answer_rounds = lambda sid: [
    {"section_id": rd["section_id"], "answers": [(int(q), bool(ok)) for q, ok in rd["answers"]]}
    for rd in _ALL.get(sid, [])
]
sys.modules["db"] = fake_db

import vector_store          # noqa: E402
import personalize          # noqa: E402
import eval_metrics as EM    # noqa: E402

section_of = EM.make_section_lookup()
difficulty_of = EM.make_difficulty_lookup()
serving = EM.make_serving_recommend(personalize.personalized_recommend)
DIFF = {1: "하", 2: "중", 3: "상"}


def tag(qid):
    d = difficulty_of(qid)
    return f"{qid}(sec{section_of(qid)}/{DIFF.get(d, d)})"


for sc in SCENARIOS:
    sid, qid, course = sc["student_id"], sc["query_id"], sc["course_id"]
    solved = set(sc["solved"])

    meta = personalize._meta_of(qid)
    odiff, osec = meta["difficulty"], meta["sectionId"]
    srounds = [rd for rd in fake_db.get_answer_rounds(sid)
               if rd["section_id"] == osec and all(x != qid for x, _ in rd["answers"])]
    state = personalize._ladder_state(srounds)
    pair = personalize._difficulty_pair(state, odiff)

    base = EM.baseline_recommend(sid, qid, course, 2)
    pers = serving(sid, qid, course, 2)

    print("═" * 72)
    print(sc["name"])
    print(f"  원문제 {tag(qid)} · 이미맞힘 {sorted(solved) or '없음'}")
    print(f"  사다리 상태 = {state}  →  목표 난이도 = {tuple(DIFF[d] for d in pair)}")
    print(f"  [기존]   {[tag(x) for x in base]}")
    print(f"  [개인화] {[tag(x) for x in pers]}")
    got = sorted(difficulty_of(x) for x in pers)
    print(f"    ✓ 난이도: 목표 {sorted(pair)} vs 개인화 실제 {got}  "
          f"→ {'일치' if got == sorted(pair) else '근접(폴백)'}")
    print(f"    ✓ 이미맞힌문제 샘: 기존 {[x for x in base if x in solved] or '없음'} / "
          f"개인화 {[x for x in pers if x in solved] or '없음'}")
print("═" * 72)
