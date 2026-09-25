from swingtrading.data.cache import CacheStore


def test_cache_roundtrip(tmp_path, ohlcv) -> None:
    store = CacheStore(tmp_path)
    store.save_ohlcv(ohlcv)
    loaded = store.load_ohlcv()
    assert set(loaded["symbol"]) >= {"AAA", "SPY"}
    assert store.last_cached_date() is not None
