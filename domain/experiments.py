"""결정적(deterministic) A/B 그룹 배정 - 순수 로직, 랜덤·DB 없음.

같은 member_id는 항상 같은 variant를 받아야 한다(sticky) - 매주 리플로우가 돌 때마다
그룹이 바뀌면 "그 학생에게 그 variant가 실제로 어떤 영향을 줬는지" 자체를 추적할 수
없어져서 실험이 무의미해진다. 랜덤 배정 대신 해시 기반으로 하는 이유가 이거다:
별도의 "배정 테이블"을 미리 만들어 저장/조회하지 않아도, 같은 입력이면 항상 같은
해시가 나오므로 매번 재현 가능(멱등)하다.
"""
import hashlib


def assign_variant(member_id: str, experiment_name: str, variants: list):
    """variants 중 하나를 member_id+experiment_name 기준으로 결정적으로 배정.

    experiment_name을 해시에 같이 섞는 이유: 실험이 여러 개 동시에 돌아도(예:
    스트레치 팩터 실험 + 다른 실험) 한 학생이 매번 같은 그룹으로만 쏠리지 않게 하기 위함
    (실험마다 독립적인 해시가 나옴).
    """
    if not variants:
        raise ValueError("variants must not be empty")
    digest = hashlib.md5(f"{experiment_name}:{member_id}".encode()).hexdigest()
    index = int(digest, 16) % len(variants)
    return variants[index]
