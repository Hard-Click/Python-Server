"""메가스터디 썸네일 사진 폴더의 PNG → 640x360 JPEG q80 base64 data URI 로 변환해
`scripts/demo_thumbnails.py`(THUMBS dict)로 저장. course.thumbnail_url(TEXT, 64KB) 에 인라인.

EC2엔 원본 폴더가 없으므로 이 스크립트는 로컬 1회 실행 → 생성된 demo_thumbnails.py 를 레포에 커밋.
실행: python scripts/build_demo_thumbnails.py [원본폴더]
"""
import base64
import io
import os
import sys

from PIL import Image

SRC = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\user\Desktop\메가스터디\썸네일 사진"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_thumbnails.py")
TARGET = 16 / 9


def to_uri(path):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    if w / h > TARGET:                       # 너무 넓으면 좌우 크롭
        nw = int(h * TARGET)
        im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:                                     # 너무 높으면 상하 크롭
        nh = int(w / TARGET)
        im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    im = im.resize((640, 360))
    for q in (80, 70, 60, 50):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q)
        uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        if len(uri) < 64000:                  # TEXT 64KB 여유
            return uri, q
    return uri, q


def main():
    thumbs = {}
    for fn in sorted(os.listdir(SRC)):
        if fn.lower().endswith((".png", ".jpg", ".jpeg")):
            uri, q = to_uri(os.path.join(SRC, fn))
            thumbs[fn] = uri
            print(f"  {fn}: q{q}, {len(uri)//1024}KB")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# 자동생성(build_demo_thumbnails.py) — 640x360 JPEG base64 data URI. course.thumbnail_url(TEXT)용.\n")
        f.write("# 원본: 메가스터디 썸네일 사진. 수정하려면 원본 교체 후 빌더 재실행.\n")
        f.write("THUMBS = {\n")
        for k, v in thumbs.items():
            f.write(f"    {k!r}: {v!r},\n")
        f.write("}\n")
    print(f"wrote {OUT} ({len(thumbs)} images)")


if __name__ == "__main__":
    main()
