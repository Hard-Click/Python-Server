"""도메인 함수 예외 계약 (종호 제안, 팀 통일).

기준: 호출한 쪽 잘못이거나 절대 일어나면 안 되는 상황 -> raise.
      예상 가능한 도메인 분기(풀이불가/콜드스타트/데이터부족)는 result.AiResult로 리턴.
application 레이어는 최소 AiFunctionError 하나만 잡으면 되고, Spring 경계로 넘길 때
ErrorCode 매핑은 그 경계(application/presentation)에서 한다 - domain은 모른다.
"""


class AiFunctionError(Exception):
    """도메인 함수들의 베이스. 호출측은 최소 이거 하나만 잡으면 됨."""


class SchedulerInputError(AiFunctionError):
    """CP-SAT 입력이 계약 위반(필수값 없음, 음수 등) - 호출측 버그."""


class FsrsComputationError(AiFunctionError):
    """FSRS 계산 중 복구 불가 오류(입력값 범위 위반 포함)."""
