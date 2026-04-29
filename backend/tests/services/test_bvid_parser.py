from app.services.bilibili import extract_bvid
from tests.utils.utils import random_bvid


def test_extract_bvid_from_link() -> None:
    bvid = random_bvid()
    extracted = extract_bvid(f"https://www.bilibili.com/video/{bvid}/?spm_id_from=333")
    assert extracted == bvid


def test_extract_bvid_from_plain_text() -> None:
    bvid = random_bvid()
    extracted = extract_bvid(f"请处理这个视频 {bvid}")
    assert extracted == bvid


def test_extract_bvid_returns_none_for_invalid_input() -> None:
    assert extract_bvid("not-a-bvid") is None

