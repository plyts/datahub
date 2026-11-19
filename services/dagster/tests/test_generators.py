from datetime import date

import pandas as pd

from datahub_pipelines import generators as g


def test_customers_are_deterministic_and_unique():
    a, b = g.generate_customers(), g.generate_customers()
    pd.testing.assert_frame_equal(a, b)
    assert len(a) == g.NB_CUSTOMERS
    assert a["customer_id"].is_unique
    assert a["email"].is_unique
    assert a["email"].notna().all()
    assert list(a.columns) == g.CUSTOMER_COLUMNS


def test_orders_are_deterministic_per_day():
    day = date(2026, 3, 14)
    pd.testing.assert_frame_equal(g.generate_orders(day), g.generate_orders(day))
    assert not g.generate_orders(day).equals(g.generate_orders(date(2026, 3, 15)))


def test_orders_volume_and_schema():
    day = date(2026, 3, 14)
    orders = g.generate_orders(day)
    assert len(orders) == g.expected_order_count(day)
    assert orders["order_id"].is_unique
    assert (orders["order_date"] == day).all()
    assert orders["status"].isin(g.STATUSES).all()
    assert (orders["amount"] > 0).all()
    assert orders["customer_id"].between(1, g.NB_CUSTOMERS).all()


def test_weekend_volume_is_higher_on_average():
    weekdays = [g.expected_order_count(date(2026, 1, d)) for d in range(5, 10)]  # Mon-Fri
    weekend = [g.expected_order_count(date(2026, 1, d)) for d in (10, 11)]
    assert sum(weekend) / len(weekend) > sum(weekdays) / len(weekdays)


def test_payments_match_orders():
    day = date(2026, 5, 1)
    orders = g.generate_orders(day)
    payments = g.generate_payments(day, orders)
    assert payments["payment_id"].is_unique
    paid = payments.groupby("order_id")["amount"].sum().round(2)
    for order in orders.itertuples():
        if order.status in ("placed", "cancelled"):
            assert order.order_id not in paid.index
        else:
            assert abs(paid[order.order_id] - order.amount) < 0.011
    assert set(payments["payment_method"]) <= {"card", "paypal", "bank_transfer", "voucher"}
