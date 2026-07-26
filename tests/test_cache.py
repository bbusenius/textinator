from textinator.cache import AudioCache


def test_miss_then_hit(tmp_path):
    cache = AudioCache(tmp_path)
    key = cache.key("edge", "voice-a", "", "Hello.")
    assert cache.get(key, "mp3") is None
    cache.path_for(key, "mp3").write_bytes(b"audio-bytes")
    hit = cache.get(key, "mp3")
    assert hit is not None
    assert hit.read_bytes() == b"audio-bytes"


def test_key_varies_by_all_inputs():
    k = AudioCache.key
    base = k("edge", "v1", "rate=+0%", "text")
    assert k("grok", "v1", "rate=+0%", "text") != base
    assert k("edge", "v2", "rate=+0%", "text") != base
    assert k("edge", "v1", "rate=+10%", "text") != base
    assert k("edge", "v1", "rate=+0%", "other") != base
    assert k("edge", "v1", "rate=+0%", "text") == base


def test_empty_file_is_a_miss(tmp_path):
    cache = AudioCache(tmp_path)
    key = cache.key("edge", "v", "", "t")
    cache.path_for(key, "mp3").touch()
    assert cache.get(key, "mp3") is None
