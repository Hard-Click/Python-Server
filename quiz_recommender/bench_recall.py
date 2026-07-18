"""ANN(HNSW) recall·latency 벤치마크 — 개인발표 실험용.

목적: "근사 검색이 정확 검색 대비 뭘 놓치는가"를 실측한다.
  - 정답(오라클) = Qdrant exact=True (브루트포스, 같은 데이터·같은 필터)
  - 측정 = recall@k(근사∩정확/k), latency p50/p95
  - 축   = hnsw_ef(검색시점 다이얼) × 필터 선택도(100%/10%/1%/0.1%) × 양자화 on/off

Gemini 불필요(랜덤 벡터, 비용 0). .env의 QDRANT_URL/QDRANT_API_KEY 사용.

사용법:
  1) 데이터 적재 (한 번):
     .venv\\Scripts\\python.exe bench_recall.py setup --n 20000
     .venv\\Scripts\\python.exe bench_recall.py setup --n 20000 --quant   # 양자화 컬렉션
  2) 스윕 실행 (여러 번 가능):
     .venv\\Scripts\\python.exe bench_recall.py run --k 10 --queries 100
  3) 결과: bench_results.csv (엑셀/차트용) + 콘솔 요약

주의:
  - 랜덤 벡터는 실제 임베딩보다 HNSW에 '어려운' 분포(군집 없음) → recall 하한선 성격.
  - Cloud로 재는 latency는 네트워크 왕복 포함(수십ms). recall은 네트워크 무관하게 정확.
    깨끗한 latency가 필요하면 로컬 Qdrant(Docker) 띄우고 --url http://localhost:6333 로.
"""
import argparse
import csv
import os
import statistics
import sys
import time
from pathlib import Path

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, PayloadSchemaType,
    Filter, FieldCondition, MatchValue,
    HnswConfigDiff, OptimizersConfigDiff, SearchParams,
    ScalarQuantization, ScalarQuantizationConfig, ScalarType,
    CollectionStatus,
)

DIM = 1536                      # 실제 서비스와 동일 차원
COLL_PLAIN = "bench_vectors"
COLL_QUANT = "bench_vectors_q"
CSV_PATH = Path(__file__).parent / "bench_results.csv"
SEED_CORPUS, SEED_QUERY = 42, 123   # 재현 가능하게 고정

# 필터 레벨: (라벨, payload 필드, 선택도) — 필드값 0으로 필터하면 해당 비율만 남음
FILTER_LEVELS = [
    ("none", None, 1.0),
    ("10pct", "g10", 0.10),
    ("1pct", "g100", 0.01),
    ("0.1pct", "g1000", 0.001),
]
EF_SWEEP = [None, 8, 16, 32, 64, 128, 256]   # None = Qdrant 기본값(우리 서비스 설정)


def _load_dotenv() -> None:
    env = Path(__file__).parent / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _client(url_override: str | None) -> QdrantClient:
    _load_dotenv()
    url = url_override or os.environ["QDRANT_URL"]
    key = None if url_override else os.environ.get("QDRANT_API_KEY")
    return QdrantClient(url=url, api_key=key, timeout=120)


def _vectors(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal((n, DIM), dtype=np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)   # 코사인용 정규화
    return v


def _wait_green(client: QdrantClient, coll: str, timeout_s: int = 600) -> None:
    """HNSW 색인이 끝날 때까지 대기 — 색인 전에 재면 결과가 왜곡된다."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if client.get_collection(coll).status == CollectionStatus.GREEN:
            return
        time.sleep(2)
    raise TimeoutError(f"{coll} 색인이 {timeout_s}s 안에 안 끝남")


def cmd_setup(args) -> None:
    client = _client(args.url)
    coll = COLL_QUANT if args.quant else COLL_PLAIN

    quant_cfg = None
    if args.quant:
        quant_cfg = ScalarQuantization(
            scalar=ScalarQuantizationConfig(type=ScalarType.INT8, always_ram=True)
        )

    if client.collection_exists(coll):
        client.delete_collection(coll)
    client.create_collection(
        collection_name=coll,
        vectors_config=VectorParams(size=DIM, distance=Distance.COSINE),
        hnsw_config=HnswConfigDiff(m=args.m, ef_construct=args.ef_construct),
        # 작은 데이터도 반드시 HNSW 색인하게 임계값 강제 하향 —
        # 안 낮추면 Qdrant가 그냥 정확검색해버려서 recall=1.0(무의미)이 나온다.
        optimizers_config=OptimizersConfigDiff(indexing_threshold=100),
        quantization_config=quant_cfg,
    )
    for field in ("g10", "g100", "g1000"):
        client.create_payload_index(coll, field_name=field, field_schema=PayloadSchemaType.INTEGER)

    vecs = _vectors(args.n, SEED_CORPUS)
    t0 = time.time()
    B = 256
    for s in range(0, args.n, B):
        pts = [
            PointStruct(
                id=i,
                vector=vecs[i].tolist(),
                payload={"g10": i % 10, "g100": i % 100, "g1000": i % 1000},
            )
            for i in range(s, min(s + B, args.n))
        ]
        client.upsert(coll, points=pts, wait=False)
        done = min(s + B, args.n)
        if done % 5120 == 0 or done == args.n:
            print(f"  적재 {done}/{args.n} ({time.time()-t0:.0f}s)")
    print("색인 대기(green)...")
    _wait_green(client, coll)
    print(f"완료: {coll} n={args.n} dim={DIM} m={args.m} quant={args.quant} "
          f"(총 {time.time()-t0:.0f}s)")


def _search_ids(client, coll, qvec, k, flt, params) -> list[int]:
    res = client.query_points(
        collection_name=coll, query=qvec.tolist(), limit=k,
        query_filter=flt, search_params=params, with_payload=False,
    )
    return [int(p.id) for p in res.points]


def cmd_run(args) -> None:
    client = _client(args.url)
    queries = _vectors(args.queries, SEED_QUERY)
    colls = [c for c in (COLL_PLAIN, COLL_QUANT) if client.collection_exists(c)]
    if not colls:
        sys.exit("컬렉션 없음 — 먼저 setup을 실행해")

    new_file = not CSV_PATH.exists()
    fh = open(CSV_PATH, "a", newline="", encoding="utf-8")
    w = csv.writer(fh)
    if new_file:
        w.writerow(["collection", "n_points", "quant", "filter", "selectivity",
                    "hnsw_ef", "k", "recall_mean", "recall_min",
                    "lat_p50_ms", "lat_p95_ms", "queries"])

    for coll in colls:
        n_points = client.get_collection(coll).points_count
        quant = coll == COLL_QUANT
        print(f"\n===== {coll} (n={n_points}, quant={quant}) =====")
        for flabel, ffield, sel in FILTER_LEVELS:
            flt = None
            if ffield:
                flt = Filter(must=[FieldCondition(key=ffield, match=MatchValue(value=0))])

            # 정답(오라클): 같은 필터로 정확(브루트포스) 검색
            truth = [set(_search_ids(client, coll, q, args.k, flt,
                                     SearchParams(exact=True)))
                     for q in queries]

            for ef in EF_SWEEP:
                ef_label = "default" if ef is None else ef
                params = SearchParams(exact=False) if ef is None \
                    else SearchParams(hnsw_ef=ef, exact=False)
                for q in queries[:3]:                      # 워밍업
                    _search_ids(client, coll, q, args.k, flt, params)
                recalls, lats = [], []
                for qi, q in enumerate(queries):
                    t0 = time.perf_counter()
                    got = _search_ids(client, coll, q, args.k, flt, params)
                    lats.append((time.perf_counter() - t0) * 1000)
                    denom = min(args.k, len(truth[qi])) or 1
                    recalls.append(len(set(got) & truth[qi]) / denom)
                lats.sort()
                row = [coll, n_points, quant, flabel, sel, ef_label, args.k,
                       round(statistics.mean(recalls), 4), round(min(recalls), 4),
                       round(lats[len(lats)//2], 2),
                       round(lats[int(len(lats)*0.95)], 2), len(queries)]
                w.writerow(row); fh.flush()
                print(f"  filter={flabel:7s} ef={ef_label!s:>7}  "
                      f"recall@{args.k}={row[7]:.3f} (min {row[8]:.2f})  "
                      f"p50={row[9]}ms p95={row[10]}ms")
    fh.close()
    print(f"\nCSV 저장: {CSV_PATH}")


def cmd_cleanup(args) -> None:
    client = _client(args.url)
    for coll in (COLL_PLAIN, COLL_QUANT):
        if client.collection_exists(coll):
            client.delete_collection(coll)
            print(f"삭제: {coll}")
    print("정리 완료 (quiz_questions 컬렉션은 건드리지 않음)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="랜덤 벡터 생성·적재(+색인 대기)")
    s.add_argument("--n", type=int, default=20000)
    s.add_argument("--m", type=int, default=16, help="HNSW m (색인 시점, 기본 16)")
    s.add_argument("--ef-construct", type=int, default=100)
    s.add_argument("--quant", action="store_true", help="int8 양자화 컬렉션으로 적재")
    s.add_argument("--url", default=None, help="Qdrant URL 재정의(로컬 등)")
    s.set_defaults(fn=cmd_setup)

    r = sub.add_parser("run", help="ef×필터 스윕 → recall/latency 측정")
    r.add_argument("--k", type=int, default=10)
    r.add_argument("--queries", type=int, default=100)
    r.add_argument("--url", default=None)
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("cleanup", help="벤치 컬렉션 삭제 (무료 클러스터 용량 회수)")
    c.add_argument("--url", default=None)
    c.set_defaults(fn=cmd_cleanup)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
