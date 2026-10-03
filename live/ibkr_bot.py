"""
Interactive Brokers broker adapter — for futures and commodities (crude CL,
S&P ES, gold GC, natgas NG, ...) that Alpaca can't trade. Also handles
stocks if you want everything under one broker.

Built on ib_async (the maintained successor to ib_insync; the original
maintainer passed away in early 2024 and the community renamed the project).

WHAT YOU NEED RUNNING:
  IBKR's API doesn't take web keys like Alpaca. Instead you run a local
  gateway app that this connects to over a socket:
    1. Open an IBKR account and enable paper trading.
    2. Install & launch "IB Gateway" (lightweight) or "Trader Workstation"
       (full GUI). Log into the PAPER account.
    3. Enable the API: in TWS/Gateway -> Global Config -> API -> Settings,
       check "Enable ActiveX and Socket Clients".
    4. Note the port. Defaults:
         Paper:  TWS 7497   |  Gateway 4002
         Live:   TWS 7496   |  Gateway 4001
  Then:  pip install ib_async

FUTURES CONTRACT NOTE:
  A futures symbol like "CL" isn't one instrument — it's a chain of monthly
  contracts (CLN6, CLQ6, ...). This adapter auto-resolves the front-month
  contract for you. Crude oil trades on NYMEX, so construct the broker with
  sec_type="FUT", exchange="NYMEX". One CL contract = 1,000 barrels, so
  position sizing is coarse — mind your account size.

NEW: term_structure() snapshots the front of the futures curve. For crude
this tells you contango vs backwardation — which matters even if you never
trade CL directly, because USO holds front-month CL futures and bleeds roll
yield in contango. A practical veto: don't open new LONG USO positions when
the curve is in steep contango (slope_pct meaningfully positive).
"""

import pandas as pd
from live.broker_base import Broker


class IBKRBroker(Broker):
    # timeframe -> (ib bar size, history duration to request)
    _TF = {
        "1Min": ("1 min", "1 D"),
        "5Min": ("5 mins", "3 D"),
        "15Min": ("15 mins", "5 D"),
        "1Hour": ("1 hour", "10 D"),
        "1Day": ("1 day", "1 Y"),
    }

    def __init__(self, host="127.0.0.1", port=7497, client_id=1,
                 sec_type="FUT", exchange="NYMEX", currency="USD",
                 contracts=None):
        """
        sec_type/exchange/currency are the defaults for every symbol.
        contracts: optional per-symbol overrides, e.g.
            {"ES": {"exchange": "CME"}, "AAPL": {"sec_type": "STK",
                                                 "exchange": "SMART"}}
        """
        from ib_async import IB
        self.IB = __import__("ib_async")
        self.sec_type = sec_type
        self.exchange = exchange
        self.currency = currency
        self.contracts = contracts or {}
        self._cache = {}

        self.ib = IB()
        self.ib.connect(host, port, clientId=client_id)
        print(f"Connected to IBKR at {host}:{port} (clientId={client_id})")

    # ---- contract resolution -------------------------------------------
    def _chain(self, symbol: str, exch: str | None = None, cur: str | None = None):
        """All contract details for a futures root, sorted by expiry."""
        from ib_async import Future
        base = Future(symbol, exchange=exch or self.exchange,
                      currency=cur or self.currency)
        details = self.ib.reqContractDetails(base)
        details.sort(key=lambda d: d.contract.lastTradeDateOrContractMonth)
        return details

    def _resolve(self, symbol: str):
        if symbol in self._cache:
            return self._cache[symbol]
        from ib_async import Future, Stock
        spec = self.contracts.get(symbol, {})
        sec = spec.get("sec_type", self.sec_type)
        exch = spec.get("exchange", self.exchange)
        cur = spec.get("currency", self.currency)

        if sec == "FUT":
            if spec.get("expiry"):
                c = Future(symbol, spec["expiry"], exch, currency=cur)
                self.ib.qualifyContracts(c)
            else:
                # Front month: ask IBKR for the chain, pick the nearest
                # non-expired expiry.
                details = self._chain(symbol, exch, cur)
                if not details:
                    raise SystemExit(f"No contracts found for future {symbol} "
                                     f"on {exch}. Check symbol/exchange.")
                today = pd.Timestamp.now().strftime("%Y%m%d")
                live = [d for d in details
                        if d.contract.lastTradeDateOrContractMonth >= today]
                c = (live or details)[0].contract
                print(f"  {symbol}: using front-month "
                      f"{c.localSymbol or c.lastTradeDateOrContractMonth}")
        else:
            c = Stock(symbol, exch, cur)
            self.ib.qualifyContracts(c)

        self._cache[symbol] = c
        return c

    # ---- term structure --------------------------------------------------
    def term_structure(self, symbol: str, n: int = 3) -> dict:
        """Snapshot the first n live contracts of a futures curve using each
        contract's latest daily close. Returns e.g.:
            {"symbol": "CL",
             "contracts": [(expiry, localSymbol, close), ...],
             "front": 78.1, "second": 79.0,
             "slope_pct": 1.15,      # (second/front - 1) * 100
             "contango": True}
        slope_pct > 0 => contango (later months pricier; long USO bleeds on
        the roll). slope_pct < 0 => backwardation (roll yield is a tailwind).
        """
        details = self._chain(symbol)
        today = pd.Timestamp.now().strftime("%Y%m%d")
        live = [d for d in details
                if d.contract.lastTradeDateOrContractMonth >= today][:n]
        rows = []
        for d in live:
            bars = self.ib.reqHistoricalData(
                d.contract, endDateTime="", durationStr="2 D",
                barSizeSetting="1 day", whatToShow="TRADES",
                useRTH=False, formatDate=1)
            if bars:
                rows.append((d.contract.lastTradeDateOrContractMonth,
                             d.contract.localSymbol, float(bars[-1].close)))
        out = {"symbol": symbol, "contracts": rows}
        if len(rows) >= 2 and rows[0][2] > 0:
            front, second = rows[0][2], rows[1][2]
            out["front"], out["second"] = front, second
            out["slope_pct"] = round((second / front - 1.0) * 100, 3)
            out["contango"] = second > front
        return out

    # ---- Broker interface ----------------------------------------------
    def get_account(self) -> dict:
        vals = {v.tag: v.value for v in self.ib.accountValues()
                if v.currency in ("USD", "", "BASE")}
        def g(tag, default=0.0):
            try:
                return float(vals.get(tag, default))
            except (TypeError, ValueError):
                return default
        return {"equity": g("NetLiquidation"),
                "cash": g("TotalCashValue"),
                "buying_power": g("BuyingPower")}

    def get_position(self, symbol: str) -> float:
        for p in self.ib.positions():
            if p.contract.symbol == symbol:
                return float(p.position)
        return 0.0

    def get_bars(self, symbol: str, lookback: int, timeframe: str) -> pd.DataFrame:
        from ib_async import util
        c = self._resolve(symbol)
        bar_size, duration = self._TF.get(timeframe, ("1 min", "1 D"))
        bars = self.ib.reqHistoricalData(
            c, endDateTime="", durationStr=duration, barSizeSetting=bar_size,
            whatToShow="TRADES", useRTH=False, formatDate=1)
        if not bars:
            return pd.DataFrame()
        df = util.df(bars)
        df = df.set_index("date")[["open", "high", "low", "close", "volume"]]
        df.index = pd.to_datetime(df.index)
        return df.tail(lookback)

    def submit_order(self, symbol: str, units: float):
        from ib_async import MarketOrder
        units = int(round(units))   # futures/stocks trade in whole contracts
        if units == 0:
            return None
        c = self._resolve(symbol)
        action = "BUY" if units > 0 else "SELL"
        order = MarketOrder(action, abs(units))
        return self.ib.placeOrder(c, order)

    def is_market_open(self) -> bool:
        # Futures trade nearly 24h on weekdays, so a simple always-open
        # default is reasonable. To respect a specific session, parse the
        # contract's tradingHours from reqContractDetails and check the
        # current time against it.
        return True

    def sleep(self, seconds: float):
        # Pump ib_async's event loop while waiting so background messages
        # (fills, price updates) keep processing. Do NOT use time.sleep here.
        self.ib.sleep(seconds)

    def disconnect(self):
        try:
            self.ib.disconnect()
            print("Disconnected from IBKR.")
        except Exception:
            pass
