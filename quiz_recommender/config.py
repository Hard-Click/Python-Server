"""환경변수 기반 설정. 비밀키는 절대 하드코딩하지 않는다.

같은 폴더에 .env 파일이 있으면 자동으로 읽어 환경변수로 넣는다(외부 라이브러리 불필요).
.env 는 .gitignore 되어 있어 커밋되지 않는다.
"""
import os
from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

EMBEDDING_MODEL = "gemini-embedding-001"   # Gemini. output_dimensionality로 차원 조절(Matryoshka)
EMBEDDING_DIM = 1536                        # 1536으로 맞춰 기존 Qdrant 컬렉션과 호환 유지
COLLECTION = "quiz_questions"

QDRANT_URL = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ["QDRANT_API_KEY"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")  # 추천 경로엔 불필요(인덱싱만 씀) → 키 없어도 import 통과
