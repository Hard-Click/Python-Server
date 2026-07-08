from scheduler.cp_sat_scheduler import generate_weekly_schedule

# 박모범: 수학 3강, 4주 안에 완주해야 함, 주간cap 180분
lessons = [
    {"id": "math_1", "duration_min": 60, "deadline_week": 3},
    {"id": "math_2", "duration_min": 60, "deadline_week": 3},
    {"id": "math_3", "duration_min": 90, "deadline_week": 3},
]
prerequisites = [("math_1", "math_2"), ("math_2", "math_3")]
weekly_caps = [180, 180, 180, 180]  # 4주치

result = generate_weekly_schedule(lessons, weekly_caps, prerequisites)
print("배정 결과:", result)
assert result is not None, "배정 실패 - infeasible"
assert result["math_1"] <= result["math_2"] <= result["math_3"], "선수관계 위반"
print("✅ 선수관계·마감일·cap 제약 전부 만족")

# 최밀림 시나리오: 이번 주(0) cap을 확 줄여서 infeasible 유도 -> "완주불가" 감지되는지
tight_caps = [10, 180, 180, 180]
result2 = generate_weekly_schedule(lessons, tight_caps, prerequisites)
print("빡빡한 cap 결과:", result2)
