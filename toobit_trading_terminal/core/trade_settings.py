"""تنظیمات مدیریت پوزیشن — ذخیره در data/trade_settings.json"""
from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass, asdict, field
from config import DATA_DIR

SETTINGS_PATH = DATA_DIR / "trade_settings.json"


@dataclass
class TradeSettings:
    # SL / TP هنگام باز شدن پوزیشن
    auto_set_stops: bool = True
    sl_mode: str = "percent"  # percent | price_offset
    tp_mode: str = "percent"
    sl_percent: float = 2.0  # درصد حرکت قیمت (نه ROE)
    tp_percent: float = 6.0
    sl_offset: float = 0.0  # فاصله مطلق قیمت
    tp_offset: float = 0.0

    # انتقال استاپ به سربه‌سر
    move_sl_to_be: bool = True
    be_trigger_percent: float = 3.0  # بعد از این % سود قیمتی
    be_fee_buffer_percent: float = 0.15  # کمی بالاتر از ورود برای پوشش کارمزد

    # محافظ سود قله (اگر سود به peak رسید و نصف شد ببند)
    peak_protect: bool = True
    peak_min_roe_percent: float = 100.0  # حداقل ROE برای فعال شدن
    peak_drawdown_ratio: float = 0.5  # اگر ROE فعلی <= peak * ratio → ببند

    # مانیتور
    monitor_enabled: bool = True
    monitor_interval_sec: int = 8

    # ثبت اسنپ‌شات اندیکاتور هنگام ورود دستی
    log_entry_snapshot: bool = True

    # افزایش پوزیشن (Scale-in) پس از حرکت به نفع
    scale_in_enabled: bool = False
    scale_in_trigger_percent: float = 1.0  # حرکت قیمت به نفع برای هر مرحله
    scale_in_stages: int = 2  # حداکثر چند بار اضافه شود
    scale_in_size_percent: float = 50.0  # هر مرحله چند٪ از حجم اولیه

    def save(self):
        SETTINGS_PATH.parent.mkdir(exist_ok=True)
        SETTINGS_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls) -> "TradeSettings":
        if not SETTINGS_PATH.exists():
            s = cls()
            s.save()
            return s
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore
            filtered = {k: v for k, v in data.items() if k in known}
            return cls(**filtered)
        except Exception:
            return cls()


def calc_stop_prices(side: str, entry: float, settings: TradeSettings):
    """محاسبه قیمت SL و TP از روی تنظیمات. side: long|short"""
    if entry is None or entry <= 0:
        return None, None
    side = (side or "").lower()
    is_long = side in ("long", "buy")

    sl = tp = None
    if settings.sl_mode == "percent" and settings.sl_percent:
        dist = entry * (settings.sl_percent / 100.0)
        sl = entry - dist if is_long else entry + dist
    elif settings.sl_mode == "price_offset" and settings.sl_offset:
        sl = entry - settings.sl_offset if is_long else entry + settings.sl_offset

    if settings.tp_mode == "percent" and settings.tp_percent:
        dist = entry * (settings.tp_percent / 100.0)
        tp = entry + dist if is_long else entry - dist
    elif settings.tp_mode == "price_offset" and settings.tp_offset:
        tp = entry + settings.tp_offset if is_long else entry - settings.tp_offset

    return sl, tp


def calc_breakeven_price(side: str, entry: float, fee_buffer_percent: float) -> float:
    is_long = (side or "").lower() in ("long", "buy")
    buf = entry * (fee_buffer_percent / 100.0)
    return entry + buf if is_long else entry - buf
