"""Gemini(Google) 임베딩. 여러 텍스트를 배치로 묶어 호출(비용·속도 유리).

- 모델: gemini-embedding-001 (출력 차원 조절 가능 = Matryoshka)
- output_dimensionality를 config.EMBEDDING_DIM(1536)로 맞춰 기존 Qdrant 컬렉션과 호환 유지.
- task_type=RETRIEVAL_DOCUMENT: 문서(문제)를 인덱싱용으로 임베딩.
  추천 검색은 저장된 문서 벡터를 그대로 query로 재사용하므로(query/doc 비대칭 없음) 전부 DOCUMENT로 통일.
- 코사인 거리(Qdrant Distance.COSINE)는 스케일 불변이라 별도 L2 정규화 불필요.
"""
from google import genai
from google.genai import types

try:                       # 패키지로 import될 때 / 스크립트로 직접 실행될 때 모두 지원
    from . import config
except ImportError:
    import config

_client = genai.Client(api_key=config.GEMINI_API_KEY)
_BATCH = 100   # 한 요청에 넣을 최대 텍스트 수 (API 배치 한도 내에서 보수적으로)


def embed(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), _BATCH):
        resp = _client.models.embed_content(
            model=config.EMBEDDING_MODEL,
            contents=texts[i:i + _BATCH],
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT",
                output_dimensionality=config.EMBEDDING_DIM,
            ),
        )
        out.extend(e.values for e in resp.embeddings)
    return out
