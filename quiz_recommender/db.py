"""공유 RDS(MySQL) 연결. 백엔드와 같은 DB를 읽기 전용으로 사용한다.
비밀번호는 환경변수로만 받는다 (Python-Server의 db.py와 동일 방식)."""
import os
import pymysql
from pymysql.cursors import DictCursor


def get_connection():
    return pymysql.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", 3306)),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        cursorclass=DictCursor,
        autocommit=True,
    )
