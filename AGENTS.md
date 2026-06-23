The purpose of this project is to collect hyperliquid perpetural future's minute level candle chart data, since hyperliquid only offer 5000 past candels, i need to continousely collect them, for my future use. 
It should support resuming (if the gap is less than 5000 candles), in case the collector's docker/clickhouse's docker goes down. 
It should have great compression rate for my clickhouse to save space.

