"""배치 실패 시 기존 error-router(/webhook/error)로 Slack 알림. 새 인프라 안 만들고 재사용.

종준 스케줄러의 infrastructure/error_router_client.py와 동일 계약을 쓴다
(POST {ERROR_ROUTER_URL}/webhook/error, source='python' → 파이썬 전용 채널로 라우팅).
quiz_recommender는 플랫 모듈 구조라 그 클래스를 직접 import하지 않고 같은 엔드포인트만 재사용한다.

ERROR_ROUTER_URL 미설정이면 조용히 no-op → 로컬/테스트/스모크에서는 알림이 안 걸린다.
"""
import os
import requests


def notify_failure(title: str, message: str, source: str = "python") -> None:
    base_url = os.environ.get("ERROR_ROUTER_URL")
    if not base_url:
        return  # 미설정 = 알림 비활성 (로컬/테스트)
    try:
        requests.post(
            f"{base_url}/webhook/error",
            json={"source": source, "title": title, "message": message},
            timeout=5,
        )
    except requests.RequestException:
        pass  # 알림 자체 실패가 배치 흐름을 막지 않음
