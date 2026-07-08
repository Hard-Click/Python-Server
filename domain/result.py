"""정상 분기(에러 아님)를 표현하는 결과 객체. 도메인 함수는 이걸 리턴하고,
호출측은 result.status로 분기한다 - "왜 None이 왔지?" 같은 모호함을 없앤다.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Status(str, Enum):
    OK = "OK"
    INFEASIBLE = "INFEASIBLE"                # CP-SAT 풀이 불가 (정상)
    COLD_START = "COLD_START"                # FSRS 학생 파라미터 없음 -> global fallback
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # 데이터 부족으로 스킵


@dataclass
class AiResult:
    status: Status
    data: Optional[dict] = None
    reason: Optional[str] = None
