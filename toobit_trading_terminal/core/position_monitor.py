"""مانیتور پوزیشن‌های باز بر اساس TradeSettings"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from core.trade_settings import TradeSettings, calc_breakeven_price


def _f(v, default=None):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def position_roe_percent(pos: dict) -> Optional[float]:
    """ROE تقریبی بر اساس unrealizedPnl و initialMargin یا percentage"""
    pct = pos.get("percentage")
    if pct is not None:
        return _f(pct)
    pnl = _f(pos.get("unrealizedPnl"))
    margin = _f(pos.get("initialMargin") or pos.get("collateral"))
    if pnl is not None and margin and margin > 0:
        return (pnl / margin) * 100.0
    return None


def price_move_percent(side: str, entry: float, mark: float) -> Optional[float]:
    if not entry or mark is None:
        return None
    is_long = (side or "").lower() in ("long", "buy")
    raw = ((mark - entry) / entry) * 100.0
    return raw if is_long else -raw


class PositionStateTracker:
    """نگه‌داری قله ROE برای هر پوزیشن (برای peak protect)"""

    def __init__(self):
        self.peak_roe: Dict[str, float] = {}
        self.be_moved: Dict[str, bool] = {}
        self.scale_in_count: Dict[str, int] = {}
        self.scale_in_entry: Dict[str, float] = {}
        self.scale_in_base_size: Dict[str, float] = {}

    def key(self, pos: dict) -> str:
        return f"{pos.get('symbol')}|{pos.get('side')}"

    def update_peak(self, pos: dict, roe: Optional[float]):
        if roe is None:
            return
        k = self.key(pos)
        prev = self.peak_roe.get(k, roe)
        self.peak_roe[k] = max(prev, roe)

    def cleanup(self, active_keys: set):
        for k in list(self.peak_roe.keys()):
            if k not in active_keys:
                self.peak_roe.pop(k, None)
                self.be_moved.pop(k, None)
                self.scale_in_count.pop(k, None)
                self.scale_in_entry.pop(k, None)
                self.scale_in_base_size.pop(k, None)


def evaluate_position(
    pos: dict,
    settings: TradeSettings,
    tracker: PositionStateTracker,
) -> List[Dict[str, Any]]:
    """
    برمی‌گرداند لیست اکشن‌ها:
      {action: 'close'|'move_sl', reason, ...}
    """
    actions: List[Dict[str, Any]] = []
    symbol = pos.get("symbol")
    side = (pos.get("side") or "").lower()
    entry = _f(pos.get("entryPrice"))
    mark = _f(pos.get("markPrice") or pos.get("lastPrice"))
    contracts = _f(pos.get("contracts"), 0) or 0
    if contracts == 0 or not symbol or entry is None:
        return actions

    roe = position_roe_percent(pos)
    tracker.update_peak(pos, roe)
    k = tracker.key(pos)
    peak = tracker.peak_roe.get(k, roe or 0)

    # 1) Peak protect
    if settings.peak_protect and peak is not None and roe is not None:
        if peak >= settings.peak_min_roe_percent:
            threshold = peak * settings.peak_drawdown_ratio
            if roe <= threshold:
                actions.append(
                    {
                        "action": "close",
                        "reason": f"peak_protect: peak ROE={peak:.1f}% → now {roe:.1f}%",
                        "symbol": symbol,
                        "side": side,
                        "contracts": contracts,
                    }
                )
                return actions  # اولویت با بستن

    # 2b) Scale-in
    if settings.scale_in_enabled and entry and mark is not None:
        if k not in tracker.scale_in_entry:
            tracker.scale_in_entry[k] = entry
            tracker.scale_in_base_size[k] = contracts
            tracker.scale_in_count[k] = 0
        count = tracker.scale_in_count.get(k, 0)
        if count < int(settings.scale_in_stages):
            # قیمت مرجع = ورود اولیه + count * trigger
            base_entry = tracker.scale_in_entry.get(k, entry)
            move = price_move_percent(side, base_entry, mark)
            needed = settings.scale_in_trigger_percent * (count + 1)
            if move is not None and move >= needed:
                add_size = tracker.scale_in_base_size.get(k, contracts) * (settings.scale_in_size_percent / 100.0)
                if add_size > 0:
                    actions.append({
                        "action": "scale_in",
                        "reason": f"scale_in stage {count+1}/{settings.scale_in_stages} after {move:.2f}%",
                        "symbol": symbol,
                        "side": side,
                        "contracts": add_size,
                        "base_side": "buy" if side in ("long", "buy") else "sell",
                    })
                    tracker.scale_in_count[k] = count + 1

    # 2) Move SL to breakeven
    if settings.move_sl_to_be and not tracker.be_moved.get(k):
        move = price_move_percent(side, entry, mark) if mark else None
        if move is not None and move >= settings.be_trigger_percent:
            be = calc_breakeven_price(side, entry, settings.be_fee_buffer_percent)
            actions.append(
                {
                    "action": "move_sl",
                    "reason": f"breakeven after {move:.2f}% price move",
                    "symbol": symbol,
                    "side": side,
                    "stop_loss": be,
                    "contracts": contracts,
                }
            )
            tracker.be_moved[k] = True

    return actions
