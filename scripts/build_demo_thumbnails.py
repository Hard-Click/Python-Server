"""메가스터디 썸네일 사진 → 640x360 JPEG 파일로 리사이즈 저장 + S3 키 매핑 생성.

next/image 는 data URI 를 못 쓰고 public S3 URL(*.s3.ap-northeast-2.amazonaws.com)만 허용하므로,
이미지를 S3에 올리고 그 URL을 course.thumbnail_url 에 쓴다. 이 스크립트는:
  1) scripts/demo_thumbs/*.jpg  (리사이즈 파일 — 레포 커밋 → EC2로, 거기서 S3 업로드)
  2) scripts/demo_thumbnails.py (S3 키 매핑 — 시더가 S3_BUCKET env 와 조합해 URL 생성)

실행(로컬 1회): python scripts/build_demo_thumbnails.py [원본폴더]
업로드(EC2):    aws s3 cp scripts/demo_thumbs/ s3://$S3_BUCKET/demo-thumbs/ --recursive
"""
import io
import os
import sys

from PIL import Image

SRC = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\user\Desktop\메가스터디\썸네일 사진"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "demo_thumbs")
OUT_PY = os.path.join(HERE, "demo_thumbnails.py")
S3_PREFIX = "demo-thumbs"
TARGET = 16 / 9

# 데모 4코스 → (원본파일, 출력 ASCII 파일명). 수학 2코스=칠판 강사·순열조합, 국어·사탐=강사.
DEMO = {
    "kor":   ("스크린샷 2026-07-18 225217.png", "kor.jpg"),
    "math1": ("수학 강사.png",                   "math1.jpg"),
    "math2": ("스크린샷 2026-07-18 225148.png", "math2.jpg"),
    "soc":   ("수학강사.png",                     "soc.jpg"),
}
# 카탈로그(둘러보기) 8코스 → 나머지 이미지
CATALOG = [
    ("image (3).png",                "cat0.jpg"),
    ("스크린샷 2026-07-18 225338.png", "cat1.jpg"),
    ("스크린샷 2026-07-18 225436.png", "cat2.jpg"),
    ("스크린샷 2026-07-18 225539.png", "cat3.jpg"),
    ("스크린샷 2026-07-18 230131.png", "cat4.jpg"),
    ("스크린샷 2026-07-18 230645.png", "cat5.jpg"),
    ("스크린샷 2026-07-18 230841.png", "cat6.jpg"),
    ("스크린샷 2026-07-18 231057.png", "cat7.jpg"),
]


def save(src_name, out_name):
    im = Image.open(os.path.join(SRC, src_name)).convert("RGB")
    w, h = im.size
    if w / h > TARGET:
        nw = int(h * TARGET); im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = int(w / TARGET); im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    im = im.resize((640, 360))
    im.save(os.path.join(OUT_DIR, out_name), "JPEG", quality=82)
    return os.path.getsize(os.path.join(OUT_DIR, out_name))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for key, (src, out) in DEMO.items():
        print(f"  demo/{key}: {out} ({save(src, out)//1024}KB)")
    for src, out in CATALOG:
        print(f"  catalog: {out} ({save(src, out)//1024}KB)")
    with open(OUT_PY, "w", encoding="utf-8") as f:
        f.write("# 자동생성(build_demo_thumbnails.py). S3 업로드된 썸네일 키. 시더가 S3_BUCKET env 와 조합해 URL 생성.\n")
        f.write(f"S3_PREFIX = {S3_PREFIX!r}\n")
        f.write("DEMO_THUMB_KEYS = {\n")
        for k, (_, out) in DEMO.items():
            f.write(f"    {k!r}: {out!r},\n")
        f.write("}\n")
        f.write("CATALOG_THUMB_KEYS = [\n")
        for _, out in CATALOG:
            f.write(f"    {out!r},\n")
        f.write("]\n")
    print(f"wrote {OUT_DIR}/ ({len(DEMO)+len(CATALOG)} jpg) + {OUT_PY}")


if __name__ == "__main__":
    main()
