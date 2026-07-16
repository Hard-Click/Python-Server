"""Shadow mode 결정 로그 집계 (순수 로직 - DB/프레임워크 import 없음).

infrastructure가 experiment_shadow_decision 테이블에서 뽑은 '결정 dict' 리스트를 받아,
"배정 variant를 실제로 적용했다면 운영 결정이 얼마나 달라졌을지"를 관리자용으로 요약한다.

각 결정 dict는 use_cases.py::_log_shadow_decision이 남긴 것과 같은 형태:
  {variant, applied_stretch_factor, applied_coeff_mean, shadow_coeff_mean,
   applied_total_min, shadow_total_min, extension_delta, weekly_minutes_delta,
   schedule_would_change}

주의: 이건 '성과(점수 향상)' 지표가 아니다 - 실사용자 없이 성과는 못 잰다. 여기서 보는 건
'variant를 켰다면 정책 결정이 얼마나 뒤집히고/부하가 얼마나 늘었을지'(정책 변화량)뿐이다.
"""


def _percentile(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return float(s[f] + (s[c] - s[f]) * (k - f))


def summarize_shadow_decisions(decisions):
    """결정 dict 리스트 -> 요약 dict. 빈 입력이면 {"n": 0}."""
    n = len(decisions)
    if n == 0:
        return {"n": 0}

    changed = sum(1 for d in decisions if d.get("schedule_would_change"))
    ext_deltas = [d.get("extension_delta", 0) or 0 for d in decisions]
    wk_deltas = [d.get("weekly_minutes_delta", 0.0) or 0.0 for d in decisions]
    ext_increase = sum(1 for e in ext_deltas if e > 0)

    ext_dist = {}
    for e in ext_deltas:
        ext_dist[e] = ext_dist.get(e, 0) + 1

    by_variant = {}
    for d in decisions:
        b = by_variant.setdefault(d.get("variant"), {"n": 0, "changed": 0, "wk": [], "ext_inc": 0})
        b["n"] += 1
        if d.get("schedule_would_change"):
            b["changed"] += 1
        b["wk"].append(d.get("weekly_minutes_delta", 0.0) or 0.0)
        if (d.get("extension_delta", 0) or 0) > 0:
            b["ext_inc"] += 1

    variant_summary = {
        v: {
            "n": b["n"],
            "would_change_rate": round(100 * b["changed"] / b["n"], 1),
            "weekly_minutes_delta_mean": round(sum(b["wk"]) / b["n"], 1),
            "extension_increase_rate": round(100 * b["ext_inc"] / b["n"], 1),
        }
        for v, b in sorted(by_variant.items(), key=lambda kv: (kv[0] is None, kv[0]))
    }

    # 가드레일 집계(로그에 필드가 없으면 관대하게 0 처리 - 구버전 로그 호환).
    guardrail_triggered = sum(1 for d in decisions if d.get("guardrail_triggered"))
    would_have_failed = sum(1 for d in decisions if d.get("would_have_failed_without_guardrail"))
    by_guardrail = {}
    for d in decisions:
        for name in d.get("guardrails", []):
            by_guardrail[name] = by_guardrail.get(name, 0) + 1

    # 품질 델타 집계(quality_delta=None인 건 - 즉 둘 중 하나 infeasible - 제외. 완주 자체가
    # 갈리는 케이스는 이미 가드레일 치명으로 별도 집계되므로 품질 저울에 안 섞는다).
    quality_rows = [d["quality_delta"] for d in decisions if d.get("quality_delta") is not None]
    quality_summary = None
    if quality_rows:
        cv_deltas = [q["load_cv_delta"] for q in quality_rows]
        overload_deltas = [q["overloaded_weeks_delta"] for q in quality_rows]
        share_deltas = [q["peak_subject_share_delta"] for q in quality_rows]
        quality_summary = {
            "n": len(quality_rows),
            "load_cv_worsened_rate": round(100 * sum(1 for x in cv_deltas if x > 0) / len(quality_rows), 1),
            "load_cv_delta_mean": round(sum(cv_deltas) / len(quality_rows), 3),
            "overloaded_weeks_worsened_rate": round(
                100 * sum(1 for x in overload_deltas if x > 0) / len(quality_rows), 1
            ),
            "peak_subject_share_delta_mean": round(sum(share_deltas) / len(quality_rows), 3),
        }

    return {
        "n": n,
        "schedule_would_change_rate": round(100 * changed / n, 1),
        "extension_increase_rate": round(100 * ext_increase / n, 1),
        "extension_delta_distribution": dict(sorted(ext_dist.items())),
        "weekly_minutes_delta": {
            "mean": round(sum(wk_deltas) / n, 1),
            "p50": round(_percentile(wk_deltas, 50), 1),
            "p95": round(_percentile(wk_deltas, 95), 1),
            "max": round(max(wk_deltas), 1),
        },
        "by_variant": variant_summary,
        "guardrails": {
            "triggered_rate": round(100 * guardrail_triggered / n, 1),
            "would_have_failed_rate": round(100 * would_have_failed / n, 1),
            "would_have_failed_count": would_have_failed,
            "by_guardrail": dict(sorted(by_guardrail.items())),
        },
        "quality": quality_summary,  # None이면 품질비교 가능한(둘 다 feasible) 로그가 아직 없음
    }


def format_summary_lines(summary):
    """요약 dict -> 사람이 읽을 줄 리스트(순수 문자열 조립, IO 없음). 호출부가 print한다."""
    if summary.get("n", 0) == 0:
        return ["shadow 결정 로그가 아직 없음 (shadow mode 배치가 최소 1회 돌아야 쌓임)."]
    wk = summary["weekly_minutes_delta"]
    lines = [
        f"shadow 결정 로그 {summary['n']}건 요약",
        f"- 배정 variant를 적용했다면 결정이 뒤집혔을 비율: {summary['schedule_would_change_rate']}%",
        f"- 연장 주수가 늘었을 비율: {summary['extension_increase_rate']}%",
        f"- 연장 주수 델타 분포(주수: 건수): {summary['extension_delta_distribution']}",
        f"- 주간분 델타(적용 시 − baseline): mean {wk['mean']} / p50 {wk['p50']} / p95 {wk['p95']} / max {wk['max']}",
        "- variant별:",
    ]
    for v, s in summary["by_variant"].items():
        lines.append(
            f"    sf={v}: n={s['n']}, 결정변화 {s['would_change_rate']}%, "
            f"주간분Δ평균 {s['weekly_minutes_delta_mean']}, 연장증가 {s['extension_increase_rate']}%"
        )
    g = summary.get("guardrails")
    if g:
        lines.append(
            f"- 가드레일: 발동 {g['triggered_rate']}%, "
            f"★막은 치명(완주불가) {g['would_have_failed_count']}건({g['would_have_failed_rate']}%), "
            f"규칙별 {g['by_guardrail']}"
        )
    q = summary.get("quality")
    if q:
        lines.append(
            f"- 스케줄 품질(둘 다 완주 가능한 {q['n']}건, 성과 아님 - 부하분산/편중 구조만): "
            f"부하변동성(CV) 악화 {q['load_cv_worsened_rate']}%(평균Δ{q['load_cv_delta_mean']}), "
            f"과부하주 악화 {q['overloaded_weeks_worsened_rate']}%, "
            f"과목편중 평균Δ{q['peak_subject_share_delta_mean']}"
        )
    return lines
