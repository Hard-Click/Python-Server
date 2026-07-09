"""OpenAI 임베딩. 여러 텍스트를 배치로 묶어 호출(비용·속도 유리)."""
from openai import OpenAI
import config

_client = OpenAI()   # OPENAI_API_KEY 환경변수 자동 사용
_BATCH = 256         # 한 번의 요청에 넣을 최대 텍스트 수


def embed(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), _BATCH):
        resp = _client.embeddings.create(model=config.EMBEDDING_MODEL, input=texts[i:i + _BATCH])
        out.extend(item.embedding for item in resp.data)
    return out
