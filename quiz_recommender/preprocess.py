"""문제 텍스트 정규화 전처리 (임베딩 전).

'완벽'이 목표가 아니라 '일단 일관되게' — 자주 나오는 수식 표기만 통일한다.
RDS 문제가 x²(위첨자)/√ 처럼 들어와도 임베딩 입력을 x^2/sqrt 로 맞춰
같은 유형이 비슷하게 임베딩되도록.
"""
import re

_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    # 위첨자 런 → ^숫자   (x²  → x^2,  x²³ → x^23)
    text = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+", lambda m: "^" + m.group(0).translate(_SUPERSCRIPT), text)
    # 흔한 수학 기호 통일
    text = text.replace("√", "sqrt").replace("×", "*").replace("÷", "/").replace("−", "-")
    # 공백 정리 (여러 칸/개행 → 한 칸)
    text = re.sub(r"\s+", " ", text).strip()
    return text


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949) 출력 오류 방지
    for s in ["x² - 5x + 6 = 0", "1 / (√2 − 1)", "  a  ×  b ÷ c "]:
        print(f"{s!r} -> {normalize(s)!r}")
