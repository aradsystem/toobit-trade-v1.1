import sqlite3, json, time
from config import DB_PATH

class Store:
    def __init__(self):
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS candles(
            symbol TEXT, timeframe TEXT, ts INTEGER, open REAL, high REAL,
            low REAL, close REAL, volume REAL, PRIMARY KEY(symbol,timeframe,ts))""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS snapshots(
            ts INTEGER, symbol TEXT, payload TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS events(
            ts INTEGER, symbol TEXT, event TEXT, payload TEXT)""")
        self.db.commit()

    def save_candles(self, symbol, timeframe, rows):
        self.db.executemany("INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?,?)",
            [(symbol,timeframe,int(r[0]),*map(float,r[1:6])) for r in rows])
        self.db.commit()

    def event(self, symbol, event, payload):
        self.db.execute("INSERT INTO events VALUES(?,?,?,?)",
            (int(time.time()*1000), symbol, event, json.dumps(payload, ensure_ascii=False, default=str)))
        self.db.commit()

    def snapshot(self, positions):
        now = int(time.time()*1000)
        self.db.executemany("INSERT INTO snapshots VALUES(?,?,?)",
            [(now, p.get("symbol",""), json.dumps(p, ensure_ascii=False, default=str)) for p in positions])
        self.db.commit()
