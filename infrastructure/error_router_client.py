"""배치 실패 시 기존 error-router(/webhook/error)로 Slack 알림. 새 인프라 안 만들고 재사용."""
import os
import requests


class ErrorRouterNotifier:
    def __init__(self, base_url: str | None = None):
        # 알림은 부가기능이다 — URL이 없어도 배치 본흐름(review_card.due 생산)을 죽이면 안 된다.
        # 과거 os.environ["ERROR_ROUTER_URL"] 하드참조가 import 시점 KeyError를 내 배치를 통째로
        # 세웠음. qdrant 지연초기화(57187ab)와 동일하게 '누락 시 무해화' 계약으로 복구.
        self.base_url = base_url or os.environ.get("ERROR_ROUTER_URL")  # 예: http://<python-ec2-ip>:4000

    def notify_failure(self, title: str, message: str, source: str = "python") -> None:
        """source='python' - error-router의 전용 채널로 라우팅됨(백엔드/프론트 알림과 분리)."""
        if not self.base_url:
            # URL 미설정: 알림만 생략하고 배치는 계속. 로그로 남겨 사후 추적 가능.
            print(f"[error-router] ERROR_ROUTER_URL 미설정 — 알림 생략: {title}: {message}")
            return
        try:
            requests.post(
                f"{self.base_url}/webhook/error",
                json={"source": source, "title": title, "message": message},
                timeout=5,
            )
        except requests.RequestException:
            pass  # 알림 자체 실패는 배치 흐름을 막지 않음(로그만 남기고 계속)
