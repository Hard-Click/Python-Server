"""데모 콘텐츠 시드용 썸네일 리사이즈 + S3 업로드.

로컬 폴더(기본: 메가스터디 썸네일 사진)의 이미지를 640x360 JPEG로 리사이즈해
scripts/demo_content_thumbs/ 에 저장하고, S3에 올린 뒤(aws CLI), seed_content.py가
URL을 만들 수 있게 키 매핑 모듈(scripts/demo_content_thumbnails.py)을 생성한다.

왜 S3인가: next/image(<Image>)는 로컬 경로/상대경로/데이터URI를 못 쓰고
next.config.ts remotePatterns 에 등록된 호스트(*.s3.ap-northeast-2.amazonaws.com 등)의
public URL만 렌더한다. BE S3UrlPresigner.publicUrl()은 http(s):// 로 시작하는 값은
그대로 통과시키므로 course.thumbnail_url / members.profile_image_url 에 S3 public URL을 넣는다.

기존 build_demo_thumbnails.py 와 같은 흐름(로컬 리사이즈 → aws s3 cp)이되:
  - 파일명을 하드코딩하지 않고 폴더 안 이미지를 전부 glob (폴더 구성이 바뀌어도 동작).
  - 기존 데모 썸네일(thumbnails/demo/*)과 안 겹치게 별도 프리픽스(thumbnails/demo-content).
  - boto3 의존 없음 — 시스템 aws CLI(설치 확인됨) 사용. 없으면 수동 업로드 명령을 출력.

실행 (Python-Server 디렉토리):
  # 리사이즈 + 자동 업로드 (aws 자격증명이 환경/프로파일에 있어야 함)
  S3_BUCKET=<버킷명> AWS_DEFAULT_REGION=ap-northeast-2 python -m scripts.upload_demo_thumbs ["원본폴더"]
  # 리사이즈만(업로드 스킵): NO_UPLOAD=1 python -m scripts.upload_demo_thumbs
"""
import glob
import os
import subprocess
import sys

from PIL import Image

DEFAULT_SRC = r"C:\Users\user\Desktop\메가스터디\썸네일 사진"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "demo_content_thumbs")
OUT_PY = os.path.join(HERE, "demo_content_thumbnails.py")

S3_PREFIX = os.environ.get("S3_CONTENT_THUMB_PREFIX", "thumbnails/demo-content")
REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
TARGET = 16 / 9   # 코스카드 16:9


def _resize_to_file(path, out_path):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    if w / h > TARGET:
        nw = int(h * TARGET)
        im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = int(w / TARGET)
        im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    im = im.resize((640, 360))
    im.save(out_path, "JPEG", quality=82)
    return os.path.getsize(out_path)


def _source_images(src_dir):
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.PNG", "*.JPG"):
        files.extend(glob.glob(os.path.join(src_dir, ext)))
    # Windows glob는 확장자 대소문자 무시 → *.png/*.PNG 가 같은 파일을 중복 반환. 경로 기준 dedupe.
    seen, uniq = set(), []
    for f in sorted(files):
        k = os.path.normcase(os.path.abspath(f))
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq   # 정렬로 content0..N 키 배정 안정(멱등)


def _write_mapping(keys):
    with open(OUT_PY, "w", encoding="utf-8") as f:
        f.write("# 자동생성(upload_demo_thumbs.py). seed_content.py 가 S3_BUCKET env 와 조합해 URL 생성.\n")
        f.write(f"S3_PREFIX = {S3_PREFIX!r}\n")
        f.write("THUMB_KEYS = [\n")
        for k in keys:
            f.write(f"    {k!r},\n")
        f.write("]\n")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    src_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC
    bucket = os.environ.get("S3_BUCKET")
    no_upload = os.environ.get("NO_UPLOAD") == "1"

    images = _source_images(src_dir)
    if not images:
        print(f"[에러] 이미지 없음: {src_dir}")
        sys.exit(1)
    os.makedirs(OUT_DIR, exist_ok=True)
    # 이전 실행 잔여 jpg 제거 → aws s3 cp --recursive 가 stale 파일을 올리지 않게(멱등).
    for old in glob.glob(os.path.join(OUT_DIR, "*.jpg")):
        os.remove(old)
    print(f"원본 {len(images)}장 → 리사이즈: {src_dir}")

    keys = []
    for i, path in enumerate(images):
        key = f"content{i}.jpg"          # ASCII 키(원본이 한글파일명이라 별도 부여)
        size = _resize_to_file(path, os.path.join(OUT_DIR, key))
        print(f"  {os.path.basename(path)} -> {key} ({size // 1024}KB)")
        keys.append(key)
    _write_mapping(keys)
    print(f"wrote {OUT_DIR}/ ({len(keys)} jpg) + {OUT_PY}")

    dest = f"s3://{bucket or '<S3_BUCKET>'}/{S3_PREFIX}/"
    cp_cmd = ["aws", "s3", "cp", OUT_DIR + os.sep, dest, "--recursive",
              "--content-type", "image/jpeg"]
    if no_upload or not bucket:
        print("\n업로드 스킵. 수동 업로드 명령:")
        print("  " + " ".join(f'"{c}"' if " " in c else c for c in cp_cmd))
        return
    print(f"\n업로드: s3://{bucket}/{S3_PREFIX}/ …")
    r = subprocess.run(cp_cmd)
    if r.returncode != 0:
        print("[에러] aws s3 cp 실패. 자격증명/버킷 확인 후 위 명령을 수동 실행하세요.")
        sys.exit(r.returncode)
    print(f"완료: https://{bucket}.s3.{REGION}.amazonaws.com/{S3_PREFIX}/content0.jpg ...")


if __name__ == "__main__":
    main()
