from hyperliquid_candles.hyperliquid.candles import Candle
from hyperliquid_candles.storage.writer import insert_candles


class RecordingInsertClient:
    """ClickHouse insert stub that records the size of each insert call."""

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def insert(
        self,
        table: str,
        data: list[tuple[object, ...]],
        column_names: list[str],
    ) -> object:
        self.batch_sizes.append(len(data))
        return None


def _candle(open_ms: int) -> Candle:
    """Build a minimal candle at a given open time."""
    return Candle("BTC", open_ms, open_ms + 59_999, 1.0, 1.0, 1.0, 1.0, 1.0, 1)


def test_insert_candles_chunks_by_batch_max_rows() -> None:
    """A large insert must be split into chunks no larger than batch_max_rows."""
    candles = [_candle(i * 60_000) for i in range(250)]
    client = RecordingInsertClient()

    inserted = insert_candles(
        client, database="hyperliquid", candles=candles, batch_max_rows=100
    )

    assert inserted == 250
    # 250 rows at 100 per chunk -> 100, 100, 50.
    assert client.batch_sizes == [100, 100, 50]


def test_insert_candles_empty_is_noop() -> None:
    """No rows means no insert calls and a zero count."""
    client = RecordingInsertClient()

    inserted = insert_candles(
        client, database="hyperliquid", candles=[], batch_max_rows=100
    )

    assert inserted == 0
    assert client.batch_sizes == []
