from invoice import render_invoice


def test_simple_order():
    order = {
        "customer": "Ada Lovelace",
        "currency": "usd",
        "tax_rate": 10,
        "items": [
            {"sku": "B-200", "name": "Widget", "price": "4.50", "qty": 2},
            {"sku": "A-100", "name": "Gadget", "price": "10.00", "qty": 1},
        ],
    }
    assert render_invoice(order) == (
        "Customer: Ada Lovelace\n"
        "Currency: USD\n"
        "--------------------------------------------\n"
        "Gadget               x1                10.00\n"
        "Widget               x2                 9.00\n"
        "--------------------------------------------\n"
        "Subtotal:                              19.00\n"
        "Tax (10%):                              1.90\n"
        "TOTAL DUE:                             20.90\n"
        "USD 2 line(s)\n"
    )


def test_order_discount():
    order = {
        "customer": "Bob",
        "discount_pct": 50,
        "items": [{"sku": "x", "price": 3, "qty": 4}],
    }
    assert render_invoice(order) == (
        "Customer: Bob\n"
        "Currency: USD\n"
        "--------------------------------------------\n"
        "x                    x4                 6.00\n"
        "--------------------------------------------\n"
        "Subtotal:                               6.00\n"
        "TOTAL DUE:                              6.00\n"
        "USD 1 line(s)\n"
    )
