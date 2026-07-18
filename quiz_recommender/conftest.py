"""pytest 수집 설정 — 레포 루트에서 `pytest` 한 번으로 전체 스위트가 돌게 한다.

quiz_recommender 모듈들은 flat import(`import recommender` 등)를 쓰므로,
루트에서 수집할 때 이 디렉터리가 sys.path에 있어야 import가 잡힌다.

smoke_test.py는 pytest 테스트가 아니라 일회성 실행 스크립트(라이브 Qdrant에
샘플 문제를 upsert함) — 이름이 test 패턴이라 수집되면 안 되므로 제외한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

collect_ignore = ["smoke_test.py"]
