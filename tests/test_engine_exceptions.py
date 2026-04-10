from common.exceptions import VenueCircuitBreakerOpen


def test_exception_is_importable():
    err = VenueCircuitBreakerOpen("boom")
    assert str(err) == "boom"
