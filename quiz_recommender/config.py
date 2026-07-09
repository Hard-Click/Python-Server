"""환경변수 기반 설정. 비밀키는 절대 하드코딩하지 않는다."""
import os

EMBEDDING_MODEL = "text-embedding-3-small"   # 1536차원, 저렴·한국어 무난
EMBEDDING_DIM = 1536
COLLECTION = "quiz_questions"

QDRANT_URL = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ["QDRANT_API_KEY"]
# OPENAI_API_KEY 는 openai SDK가 환경변수에서 자동으로 읽는다.
