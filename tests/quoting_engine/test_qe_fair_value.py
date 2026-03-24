"""Tests for FairValueCalculator."""
from quoting_engine.config import FairValueWeights
from quoting_engine.fair_value import FairValueCalculator


def test_fv_at_mid_when_symmetric():
    # Use weights that sum to 1.0 across active components (no inventory term)
    w = FairValueWeights(w_oracle=0.50, w_external=0.20, w_microprice=0.30, w_inventory=0.0)
    calc = FairValueCalculator(w)
    fv = calc.compute(oracle_price=100.0, bid=99.5, ask=100.5)
    # With symmetric bid/ask around oracle, FV should be near 100
    assert 99.5 < fv < 100.5


def test_fv_with_oracle_only_weights():
    w = FairValueWeights(w_oracle=1.0, w_external=0.0, w_microprice=0.0, w_inventory=0.0)
    calc = FairValueCalculator(w)
    fv = calc.compute(oracle_price=42.0, bid=40.0, ask=44.0)
    assert fv == 42.0


def test_fv_microprice_shifts_toward_pressure():
    w = FairValueWeights(w_oracle=0.0, w_external=0.0, w_microprice=1.0, w_inventory=0.0)
    calc = FairValueCalculator(w)
    # Oracle near bid -> buying pressure -> microprice shifts toward ask
    fv = calc.compute(oracle_price=99.0, bid=98.0, ask=102.0)
    assert fv > 99.0  # microprice above oracle when oracle near bid


def test_fv_external_ref_stub():
    w = FairValueWeights(w_oracle=0.5, w_external=0.5, w_microprice=0.0, w_inventory=0.0)
    calc = FairValueCalculator(w)
    # external_ref=0 falls back to oracle
    fv = calc.compute(oracle_price=100.0, bid=99.0, ask=101.0, external_ref=0.0)
    assert fv == 100.0  # 0.5*100 + 0.5*100


def test_fv_external_ref_used():
    w = FairValueWeights(w_oracle=0.5, w_external=0.5, w_microprice=0.0, w_inventory=0.0)
    calc = FairValueCalculator(w)
    fv = calc.compute(oracle_price=100.0, bid=99.0, ask=101.0, external_ref=110.0)
    assert fv == 105.0  # 0.5*100 + 0.5*110


def test_fv_zero_mid_returns_zero():
    calc = FairValueCalculator(FairValueWeights())
    fv = calc.compute(oracle_price=0.0, bid=0.0, ask=0.0)
    assert fv == 0.0


def test_fv_inventory_term():
    w = FairValueWeights(w_oracle=0.8, w_external=0.0, w_microprice=0.0, w_inventory=0.2)
    calc = FairValueCalculator(w)
    fv = calc.compute(oracle_price=100.0, bid=99.0, ask=101.0, inventory_term=-5.0)
    assert fv == 100.0 * 0.8 + (-5.0) * 0.2  # 80 - 1 = 79
