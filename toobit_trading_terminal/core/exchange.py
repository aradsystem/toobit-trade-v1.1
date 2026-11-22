import math
import ccxt
from config import API_KEY, API_SECRET


class ExchangeService:
    def __init__(self):
        self.exchange = ccxt.toobit({
            "apiKey": API_KEY,
            "secret": API_SECRET,
            "enableRateLimit": True,
            "timeout": 20000,
            "options": {"defaultType": "swap"},
        })
        self.markets_loaded = False

    def load_markets(self):
        if not self.markets_loaded:
            self.exchange.load_markets()
            self.markets_loaded = True
        return self.exchange.markets

    def public_tickers(self):
        self.load_markets()
        return self.exchange.fetch_tickers()

    def ohlcv(self, symbol, timeframe="1m", limit=250):
        self.load_markets()
        return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def balance(self):
        if not API_KEY or not API_SECRET:
            raise RuntimeError("برای اطلاعات حساب، API Key و Secret را در فایل .env وارد کنید.")
        self.load_markets()
        return self.exchange.fetch_balance({"type": "swap"})

    def positions(self):
        if not API_KEY or not API_SECRET:
            raise RuntimeError("برای مشاهده پوزیشن‌ها، API Key و Secret لازم است.")
        self.load_markets()
        return self.exchange.fetch_positions()

    def market(self, symbol):
        self.load_markets()
        return self.exchange.market(symbol)

    def market_meta(self, symbol: str) -> dict:
        m = self.market(symbol)
        limits = m.get("limits") or {}
        amount_lim = limits.get("amount") or {}
        lev_lim = limits.get("leverage") or {}
        prec = m.get("precision") or {}
        info = m.get("info") or {}

        contract_size = float(m.get("contractSize") or info.get("contractMultiplier") or 1) or 1.0

        # در API توبیت quantity واحدش قرارداد است؛ UI ما مقدار پایه (BTC) می‌گیرد
        min_contracts = amount_lim.get("min")
        try:
            min_contracts = float(min_contracts) if min_contracts is not None else 1.0
        except Exception:
            min_contracts = 1.0
        # اگر min خیلی کوچک بود (داده CCXT)، حداقل ۱ قرارداد منطقی‌تر است
        if min_contracts < 0.001:
            min_contracts = 1.0

        min_base = min_contracts * contract_size
        step_contracts = prec.get("amount")
        try:
            step_contracts = float(step_contracts) if step_contracts is not None else 1.0
        except Exception:
            step_contracts = 1.0
        if step_contracts <= 0:
            step_contracts = 1.0

        max_lev = lev_lim.get("max")
        if max_lev is None:
            try:
                max_lev = int(info.get("maxLeverage") or info.get("max_leverage") or 200)
            except Exception:
                max_lev = 200

        return {
            "symbol": symbol,
            "contract_size": contract_size,
            "min_contracts": min_contracts,
            "min_base": min_base,
            "step_contracts": step_contracts,
            "max_leverage": int(max_lev) if max_lev else 200,
            "base": m.get("base"),
            "quote": m.get("quote"),
            "precision_price": prec.get("price"),
        }

    def base_to_contracts(self, symbol: str, base_amount: float) -> float:
        """تبدیل مقدار پایه (مثلاً BTC) به تعداد قرارداد API"""
        meta = self.market_meta(symbol)
        cs = meta["contract_size"] or 1.0
        contracts = float(base_amount) / cs
        step = meta["step_contracts"] or 1.0
        # گرد کردن به نزدیک‌ترین گام
        if step >= 1:
            contracts = math.floor(contracts + 1e-12)
            if contracts < 1:
                contracts = 1
        else:
            # precision اعشاری
            precision = max(0, int(round(-math.log10(step)))) if step < 1 else 0
            contracts = float(f"{contracts:.{precision}f}")
        if contracts < meta["min_contracts"]:
            raise ValueError(
                f"حجم خیلی کوچک است. حداقل ≈ {meta['min_base']} {meta.get('base')} "
                f"(= {meta['min_contracts']} قرارداد). شما وارد کردید {base_amount} {meta.get('base')} "
                f"(≈ {float(base_amount)/cs:.6g} قرارداد)."
            )
        return contracts


    def max_openable(self, symbol: str, leverage: int) -> dict:
        """
        حداکثر مقدار قابل باز شدن با موجودی آزاد و اهرم.
        خروجی بر حسب واحد پایه (BTC/ETH/...) و قرارداد.
        """
        self.load_markets()
        meta = self.market_meta(symbol)
        cs = float(meta["contract_size"] or 1)
        lev = max(1, int(leverage))

        # موجودی آزاد USDT
        free = 0.0
        if API_KEY and API_SECRET:
            try:
                bal = self.balance()
                free = float((bal.get("free") or {}).get("USDT") or 0)
            except Exception:
                free = 0.0

        # قیمت لحظه
        last = None
        try:
            t = self.public_tickers().get(symbol) or {}
            last = float(t.get("last") or t.get("close") or 0) or None
        except Exception:
            last = None
        if not last:
            raise RuntimeError("قیمت نماد دریافت نشد.")

        # ارزش اسمی تقریبی قابل باز شدن = موجودی * اهرم
        max_notional = free * lev
        # مقدار پایه از روی ارزش
        max_base_by_balance = max_notional / last if last else 0.0

        # سقف بازار (اگر در limits باشد) — limits.amount در CCXT گاهی بر حسب قرارداد است
        m = self.market(symbol)
        max_amt = (m.get("limits") or {}).get("amount") or {}
        max_contracts_mkt = max_amt.get("max")
        try:
            max_contracts_mkt = float(max_contracts_mkt) if max_contracts_mkt is not None else None
        except Exception:
            max_contracts_mkt = None

        max_base_by_market = None
        if max_contracts_mkt and max_contracts_mkt > 0:
            # اگر max خیلی بزرگ بود احتمالاً واحد پایه است نه قرارداد
            if max_contracts_mkt >= 1:
                max_base_by_market = max_contracts_mkt * cs
            else:
                max_base_by_market = max_contracts_mkt

        candidates = [max_base_by_balance]
        if max_base_by_market is not None:
            candidates.append(max_base_by_market)
        max_base = max(0.0, min(candidates)) if candidates else 0.0

        # حداقل
        min_base = float(meta.get("min_base") or cs)
        if max_base < min_base:
            max_base = 0.0  # واقعاً نمی‌توان باز کرد

        max_contracts = max_base / cs if cs else max_base

        return {
            "symbol": symbol,
            "free_usdt": free,
            "leverage": lev,
            "last": last,
            "max_notional": max_notional,
            "max_base": max_base,
            "max_contracts": max_contracts,
            "min_base": min_base,
            "contract_size": cs,
            "base": meta.get("base"),
        }

    def create_market_order(self, symbol, side, base_amount, position_side=None):
        """
        base_amount: مقدار به واحد پایه مثل سایت (BTC)
        به قرارداد تبدیل و به API ارسال می‌شود.
        """
        self.load_markets()
        contracts = self.base_to_contracts(symbol, float(base_amount))
        # CCXT برای toobit quantity را مستقیم می‌فرستد؛ مقدار را به واحد قرارداد می‌دهیم
        params = {}
        if position_side:
            params["positionSide"] = position_side
        # amount در CCXT toobit بدون تبدیل contractSize به API می‌رود
        return self.exchange.create_order(symbol, "market", side, contracts, None, params)

    def close_position_market(self, symbol, side, contracts_or_base, position_side=None):
        """بستن: مقدار از پوزیشن معمولاً contracts است — گرد شده به گام بازار"""
        self.load_markets()
        import math
        meta = self.market_meta(symbol)
        step = float(meta.get("step_contracts") or 1)
        amt = float(contracts_or_base)
        if step >= 1:
            amt = math.floor(amt + 1e-9)
            if amt < 1:
                amt = 1
        else:
            precision = max(0, int(round(-math.log10(step)))) if step < 1 else 0
            amt = float(f"{amt:.{precision}f}")
        if amt <= 0:
            raise ValueError("مقدار بستن پس از گرد کردن صفر شد.")
        params = {"reduceOnly": True}
        if position_side:
            params["positionSide"] = position_side
        return self.exchange.create_order(symbol, "market", side, amt, None, params)

    def open_additional(self, symbol, side, contracts, position_side=None):
        """افزایش پوزیشن با تعداد قرارداد (نه base)"""
        self.load_markets()
        import math
        meta = self.market_meta(symbol)
        step = float(meta.get("step_contracts") or 1)
        amt = float(contracts)
        if step >= 1:
            amt = max(1, int(math.floor(amt + 1e-9)))
        else:
            precision = max(0, int(round(-math.log10(step)))) if step < 1 else 0
            amt = float(f"{amt:.{precision}f}")
        params = {}
        if position_side:
            params["positionSide"] = position_side
        return self.exchange.create_order(symbol, "market", side, amt, None, params)

    def set_trading_stop(self, symbol, side, stop_loss=None, take_profit=None):
        market = self.market(symbol)
        raw_symbol = market.get("id")
        payload = {"symbol": raw_symbol, "side": side.upper()}
        if stop_loss is not None:
            payload["stopLoss"] = str(stop_loss)
            payload["slTriggerBy"] = "CONTRACT_PRICE"
        if take_profit is not None:
            payload["takeProfit"] = str(take_profit)
            payload["tpTriggerBy"] = "CONTRACT_PRICE"
        return self.exchange.private_post_api_v1_futures_position_trading_stop(payload)

    def add_isolated_margin(self, symbol, side, amount):
        market = self.market(symbol)
        payload = {
            "symbol": market.get("id"),
            "side": side.upper(),
            "amount": str(amount),
        }
        return self.exchange.private_post_api_v1_futures_position_margin(payload)

    def set_leverage(self, symbol, leverage):
        market = self.market(symbol)
        return self.exchange.private_post_api_v1_futures_leverage({
            "symbol": market.get("id"),
            "leverage": int(leverage),
        })
