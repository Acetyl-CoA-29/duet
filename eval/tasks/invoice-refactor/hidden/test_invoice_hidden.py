"""Differential oracle: the refactored module must match the original on every case."""
import ast
import copy
import itertools
from pathlib import Path

import pytest

import invoice
import original_invoice

MAX_LINES = 20
MIN_FUNCTIONS = 5


def outcome(function, order):
    try:
        return ("ok", function(order))
    except Exception as exc:  # the type of failure is part of the behaviour
        return ("raised", type(exc).__name__)


def same(order):
    expected = outcome(original_invoice.render_invoice, copy.deepcopy(order))
    assert outcome(invoice.render_invoice, copy.deepcopy(order)) == expected


def item(**changes):
    base = {"sku": "A-1", "name": "Thing", "price": "10.00", "qty": 1}
    base.update(changes)
    return base


def test_structure():
    tree = ast.parse(Path(invoice.__file__).read_text(encoding="utf-8"))
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert len(functions) >= MIN_FUNCTIONS
    assert all(f.end_lineno - f.lineno + 1 <= MAX_LINES for f in functions)
    assert "render_invoice" in {f.name for f in functions}


def test_simple():
    same({"customer": "Ada", "currency": "usd", "tax_rate": 10,
          "items": [item(sku="B-200", price="4.50", qty=2), item()]})


@pytest.mark.parametrize("customer", [None, "", "   ", "  Ada  ", 42, 0])
def test_customer_handling(customer):
    same({"customer": customer, "items": [item()]})
    same({"items": [item()]})


@pytest.mark.parametrize("currency", [None, "", "eur", "GBP"])
def test_currency(currency):
    same({"customer": "A", "currency": currency, "items": [item()]})


def test_sort_is_case_insensitive_and_stable():
    same({"customer": "A", "items": [item(sku="b", name="lower b"), item(sku="A", name="upper A"),
                                     item(sku="a", name="lower a"), item(sku="B", name="upper B")]})


def test_half_cents_round_up_not_to_even():
    same({"customer": "A", "discount_pct": 50, "items": [item(price="0.25"), item(sku="z", price="0.05")]})
    same({"customer": "A", "tax_rate": 2.5, "items": [item(price="1.00")]})
    same({"customer": "A", "tax_rate": "7.25", "items": [item(price="0.20", qty=3)]})


def test_item_discount_of_zero_overrides_order_discount():
    same({"customer": "A", "discount_pct": 20,
          "items": [item(discount_pct=0), item(sku="b", discount_pct=None), item(sku="c", discount_pct=5), item(sku="d")]})


def test_float_prices_and_discounts():
    same({"customer": "A", "discount_pct": 12.5, "items": [item(price=0.1, qty=3), item(sku="f", price=19.99)]})


def test_zero_quantity_is_skipped_and_default_quantity_is_one():
    order_item = item()
    del order_item["qty"]
    same({"customer": "A", "items": [item(qty=0), order_item]})
    same({"customer": "A", "items": [item(qty=0)]})


def test_returns_are_kept_and_shown_in_brackets():
    same({"customer": "A", "tax_rate": 10, "items": [item(qty=-2), item(sku="b", price="5.00")]})
    same({"customer": "A", "tax_rate": 10, "items": [item(qty=-2)]})
    same({"customer": "A", "items": [item(price="-3.00", qty=2)]})


def test_untaxed_items_and_negative_tax_floor():
    same({"customer": "A", "tax_rate": 8, "items": [item(taxable=False), item(sku="b", price="2.00")]})
    same({"customer": "A", "tax_rate": 8, "items": [item(taxable=False, price="50"), item(sku="b", qty=-1)]})


@pytest.mark.parametrize("rate", [None, 0, 0.0, "0", 5, "5", 17.5])
def test_tax_line_presence(rate):
    same({"customer": "A", "tax_rate": rate, "items": [item()]})


def test_labels():
    same({"customer": "A", "items": [item(name="x" * 20), item(sku="b", name="y" * 21),
                                     item(sku="c", name=""), item(sku="d" * 30, name=None)]})


@pytest.mark.parametrize("items", [None, [], [item(qty=0)]])
def test_no_items(items):
    same({"customer": "A", "tax_rate": 5, "items": items})
    same({})


def test_large_amounts_overflow_the_column():
    same({"customer": "A", "items": [item(price="123456789.99", qty=12)]})


@pytest.mark.parametrize("order", [
    {"items": [{"name": "no sku", "price": 1}]},
    {"items": [{"sku": "a"}]},
    {"items": [item(price="abc")]},
    {"currency": 5, "items": []},
    {"items": [item(sku=5)]},
])
def test_same_failures(order):
    same(order)


def test_input_is_not_modified():
    order = {"customer": "A", "items": [item(sku="b"), item(sku="a")]}
    before = copy.deepcopy(order)
    invoice.render_invoice(order)
    assert order == before


def test_grid():
    quantities, discounts, rates = (-1, 1, 3), (None, 0, 33), (None, 0, 9.5)
    for qty, item_pct, order_pct, rate in itertools.product(quantities, discounts, discounts, rates):
        same({"customer": "Grid", "discount_pct": order_pct, "tax_rate": rate,
              "items": [item(qty=qty, discount_pct=item_pct, price="3.33"), item(sku="t", price="0.15", taxable=False)]})
