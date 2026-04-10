from parent.paradex_private_ws import ParadexPrivateWSReconciler


def test_authentication_message_matches_expected_shape():
    msg = ParadexPrivateWSReconciler.authentication_message("jwt-token")
    assert msg == {
        "jsonrpc": "2.0",
        "method": "auth",
        "params": {"bearer": "jwt-token"},
        "id": 0,
    }


def test_subscription_messages_cover_default_private_channels():
    reconciler = ParadexPrivateWSReconciler()
    messages = reconciler.subscription_messages()
    channels = [m["params"]["channel"] for m in messages]
    assert channels == ["orders", "fills", "positions", "account"]


def test_disconnect_marks_snapshot_needed_again():
    reconciler = ParadexPrivateWSReconciler()
    reconciler.mark_connected(now_ms=1000)
    reconciler.mark_authenticated(now_ms=1000)
    reconciler.apply_rest_snapshot(orders=[], positions=[], balances=[], now_ms=1001)
    assert reconciler.needs_snapshot() is False

    reconciler.mark_disconnected(now_ms=2000)
    assert reconciler.needs_snapshot() is True
    assert reconciler.state.connected is False
    assert reconciler.state.authenticated is False
    assert reconciler.state.reconnect_count == 1


def test_should_refresh_jwt_after_threshold():
    reconciler = ParadexPrivateWSReconciler(jwt_refresh_after_s=180)
    assert reconciler.should_refresh_jwt(now_ms=0) is True
    reconciler.mark_authenticated(now_ms=1000)
    assert reconciler.should_refresh_jwt(now_ms=180_999) is False
    assert reconciler.should_refresh_jwt(now_ms=181_000) is True


def test_rest_snapshot_then_ws_events_update_state():
    reconciler = ParadexPrivateWSReconciler()
    reconciler.apply_rest_snapshot(
        orders=[{"id": "o1", "status": "open"}],
        positions=[{"market": "BTC-USD-PERP", "size": "1"}],
        balances=[{"asset": "USDC", "available": "1000"}],
        now_ms=1000,
    )

    reconciler.apply_message(
        {
            "method": "subscription",
            "params": {
                "channel": "orders",
                "data": {"id": "o2", "status": "open"},
            },
        },
        now_ms=1010,
    )
    reconciler.apply_message(
        {
            "method": "subscription",
            "params": {
                "channel": "fills",
                "data": {"id": "f1", "market": "BTC-USD-PERP", "qty": "0.1"},
            },
        },
        now_ms=1020,
    )
    reconciler.apply_message(
        {
            "method": "subscription",
            "params": {
                "channel": "positions",
                "data": {"market": "BTC-USD-PERP", "size": "0"},
            },
        },
        now_ms=1030,
    )
    reconciler.apply_message(
        {
            "method": "subscription",
            "params": {
                "channel": "account",
                "data": {"asset": "USDC", "available": "0"},
            },
        },
        now_ms=1040,
    )

    assert set(reconciler.state.open_orders) == {"o1", "o2"}
    assert len(reconciler.state.fills) == 1
    assert reconciler.state.positions == {}
    assert reconciler.state.balances == {}
    assert reconciler.state.last_message_ms == 1040


def test_reconcile_with_rest_uses_callbacks():
    reconciler = ParadexPrivateWSReconciler()
    reconciler.reconcile_with_rest(
        fetch_orders=lambda: [{"order_id": "a1", "status": "open"}],
        fetch_positions=lambda: [{"symbol": "ETH-USD-PERP", "size": "2"}],
        fetch_balances=lambda: [{"currency": "USDC", "total": "55"}],
        now_ms=5000,
    )

    assert reconciler.state.open_orders["a1"]["status"] == "open"
    assert reconciler.state.positions["ETH-USD-PERP"]["size"] == "2"
    assert reconciler.state.balances["USDC"]["total"] == "55"
    assert reconciler.state.last_snapshot_ms == 5000
    assert reconciler.needs_snapshot() is False
