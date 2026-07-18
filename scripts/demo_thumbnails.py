# 자동생성(build_demo_thumbnails.py). S3 업로드된 썸네일 키. 시더가 S3_BUCKET env 와 조합해 URL 생성.
S3_PREFIX = 'thumbnails/demo'
DEMO_THUMB_KEYS = {
    'kor': 'kor.jpg',
    'math1': 'math1.jpg',
    'math2': 'math2.jpg',
    'soc': 'soc.jpg',
}
CATALOG_THUMB_KEYS = [
    'cat0.jpg',
    'cat1.jpg',
    'cat2.jpg',
    'cat3.jpg',
    'cat4.jpg',
    'cat5.jpg',
    'cat6.jpg',
    'cat7.jpg',
]
