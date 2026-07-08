"""배치 실패 시 기존 error-router(/webhook/error)로 Slack 알림. 새 인프라 안 만들고 재사용."""
import os
import requests


class ErrorRouterNotifier:
    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or os.environ["ERROR_ROUTER_URL"]  # 예: http://<python-ec2-ip>:4000

    def notify_failure(self, title: str, message: str, source: str = "python") -> None:
        """source='python' - error-router의 전용 채널로 라우팅됨(백엔드/프론트 알림과 분리)."""
        try:
            requests.post(
                f"{self.base_url}/webhook/error",
                json={"source": source, "title": title, "message": message},
                timeout=5,
            )
        except requests.RequestException:
            pass  # 알림 자체 실패는 배치 흐름을 막지 않음(로그만 남기고 계속)
