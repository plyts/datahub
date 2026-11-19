"""Deterministic synthetic data used by the demo ingestion assets.

The data for a given day is always identical (seeded by the date), which makes
re-runs and backfills idempotent and lets tests assert exact values.
Replace these functions by real extractors (API, database, files) in production.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

import pandas as pd

NB_CUSTOMERS = 500
COUNTRIES = ["FR", "FR", "FR", "FR", "BE", "CH", "LU", "DE", "ES", "IT", "MA", "SN", "CA"]
FIRST_NAMES = [
    "Camille",
    "Léa",
    "Hugo",
    "Louis",
    "Emma",
    "Jade",
    "Lucas",
    "Inès",
    "Nathan",
    "Chloé",
    "Yanis",
    "Sarah",
    "Adam",
    "Manon",
    "Noah",
    "Lina",
    "Ethan",
    "Zoé",
    "Mathis",
    "Alice",
]
LAST_NAMES = [
    "Martin",
    "Bernard",
    "Dubois",
    "Thomas",
    "Robert",
    "Richard",
    "Petit",
    "Durand",
    "Leroy",
    "Moreau",
    "Simon",
    "Laurent",
    "Lefebvre",
    "Michel",
    "Garcia",
    "David",
    "Bertrand",
    "Roux",
]
CATEGORIES = ["electronics", "books", "home", "fashion", "sports", "beauty"]
STATUSES = ["placed", "shipped", "completed", "returned", "cancelled"]
PAYMENT_METHODS = ["card", "paypal", "bank_transfer"]

CUSTOMER_COLUMNS = ["customer_id", "first_name", "last_name", "email", "country_code", "created_at"]
ORDER_COLUMNS = ["order_id", "customer_id", "order_date", "status", "product_category", "amount"]
PAYMENT_COLUMNS = ["payment_id", "order_id", "payment_method", "amount", "paid_at"]


def generate_customers(n: int = NB_CUSTOMERS) -> pd.DataFrame:
    rng = random.Random("customers")
    rows = []
    start = datetime(2025, 1, 1)
    for customer_id in range(1, n + 1):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        rows.append(
            {
                "customer_id": customer_id,
                "first_name": first,
                "last_name": last,
                "email": f"{first}.{last}{customer_id}@example.com".lower(),
                "country_code": rng.choice(COUNTRIES),
                "created_at": start + timedelta(minutes=rng.randint(0, 365 * 24 * 60)),
            }
        )
    return pd.DataFrame(rows, columns=CUSTOMER_COLUMNS)


def expected_order_count(day: date) -> int:
    """Base daily volume with weekly seasonality (weekends +30 %)."""
    base = 80
    if day.weekday() >= 5:
        base = int(base * 1.3)
    rng = random.Random(f"volume-{day.isoformat()}")
    return max(1, base + rng.randint(-15, 15))


def _status_for(rng: random.Random) -> str:
    return rng.choices(STATUSES, weights=[10, 20, 60, 5, 5])[0]


def generate_orders(day: date) -> pd.DataFrame:
    rng = random.Random(f"orders-{day.isoformat()}")
    rows = []
    for i in range(expected_order_count(day)):
        amount = round(min(2000.0, rng.lognormvariate(4.0, 0.7)), 2)
        rows.append(
            {
                "order_id": f"O{day:%Y%m%d}-{i:04d}",
                "customer_id": rng.randint(1, NB_CUSTOMERS),
                "order_date": day,
                "status": _status_for(rng),
                "product_category": rng.choice(CATEGORIES),
                "amount": amount,
            }
        )
    return pd.DataFrame(rows, columns=ORDER_COLUMNS)


def generate_payments(day: date, orders: pd.DataFrame | None = None) -> pd.DataFrame:
    """Payments are derived from the orders of the same day.

    - `placed` and `cancelled` orders have no payment yet;
    - 15 % of paid orders are split between a voucher and another method.
    """
    if orders is None:
        orders = generate_orders(day)
    rng = random.Random(f"payments-{day.isoformat()}")
    rows = []
    for order in orders.itertuples(index=False):
        if order.status in ("placed", "cancelled"):
            continue
        paid_at = datetime.combine(day, time(0)) + timedelta(minutes=rng.randint(0, 24 * 60 - 1))
        method = rng.choices(PAYMENT_METHODS, weights=[75, 15, 10])[0]
        amount = round(float(order.amount), 2)
        if rng.random() < 0.15 and amount > 20:
            voucher = round(min(amount / 2, 20.0), 2)
            rows.append(
                {
                    "payment_id": f"P{order.order_id}-V",
                    "order_id": order.order_id,
                    "payment_method": "voucher",
                    "amount": voucher,
                    "paid_at": paid_at,
                }
            )
            amount = round(amount - voucher, 2)
        rows.append(
            {
                "payment_id": f"P{order.order_id}-1",
                "order_id": order.order_id,
                "payment_method": method,
                "amount": amount,
                "paid_at": paid_at,
            }
        )
    return pd.DataFrame(rows, columns=PAYMENT_COLUMNS)
