"""RDS 접속 없이 eval_metrics 를 실행하는 러너.

RDS 는 프라이빗 서브넷이라 로컬에서 못 붙는다. 개인화 추천기가 부르는
db.get_answer_rounds 만 eval_labels.json 의 students[].rounds 로 대체하고,
Qdrant(클라우드)는 실제로 쓴다 — 추천 파이프라인 자체는 그대로.

실행:  .venv\\Scripts\\python.exe eval_offline.py
"""
import json
import sys
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # Windows cp949 콘솔에서 화살표(→) 깨짐 방지

HERE = Path(__file__).parent
labels = json.loads((HERE / "eval_labels.json").read_text(encoding="utf-8"))
_rounds = {int(s): v.get("rounds", []) for s, v in labels["students"].items()}


def _get_answer_rounds(student_id: int) -> list[dict]:
    return [
        {"section_id": rd["section_id"],
         "answers": [(int(q), bool(ok)) for q, ok in rd["answers"]]}
        for rd in _rounds.get(student_id, [])
    ]


fake_db = types.ModuleType("db")
fake_db.get_answer_rounds = _get_answer_rounds
sys.modules["db"] = fake_db  # personalize 의 `import db` 가 이걸 받는다

import eval_metrics  # noqa: E402

if __name__ == "__main__":
    eval_metrics.main()
