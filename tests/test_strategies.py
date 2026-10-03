"""Strategy interface checks. Run: python -m unittest discover -s tests"""
import inspect
import unittest

from strategies.breakout import Breakout
from strategies.mean_reversion import MeanReversion
from strategies.momentum import Momentum
from strategies.pairs import PairsTrading
from strategies.pyramiding_breakout import PyramidingBreakout
from strategies.xsec_momentum import CrossSectionalMomentum

ALL = (MeanReversion, Momentum, PairsTrading, Breakout,
       PyramidingBreakout, CrossSectionalMomentum)


class Params(unittest.TestCase):
    def test_every_strategy_records_its_constructor_params(self):
        # The backtest tools size warmup from strat.params; three duck-typed
        # strategies once lacked it and crashed them.
        for cls in ALL:
            with self.subTest(cls.__name__):
                want = {n: p.default
                        for n, p in inspect.signature(cls.__init__).parameters.items()
                        if n not in ("self", "symbols")}
                symbols = ["A", "B"] if cls is PairsTrading else ["A", "B", "C"]
                self.assertEqual(getattr(cls(symbols), "params", None), want)


if __name__ == "__main__":
    unittest.main()
