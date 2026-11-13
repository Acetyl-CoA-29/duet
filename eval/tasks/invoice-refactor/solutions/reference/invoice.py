"""Plain-text invoices for the billing export."""
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
RULE = "-" * 44


def _cents(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _header(order):
    name = order.get("customer")
    known = name is not None and str(name).strip()
    currency = (order.get("currency") or "USD").upper()
    return ["Customer: " + (str(name).strip() if known else "(unknown)"), "Currency: " + currency, RULE], currency


def _discount_pct(item, order):
    pct = item.get("discount_pct")
    if pct is None:
        pct = order.get("discount_pct")
    return 0 if pct is None else pct


def _amount(item, quantity, order):
    amount = Decimal(str(item["price"])) * quantity
    pct = _discount_pct(item, order)
    if pct:
        amount = amount - amount * Decimal(str(pct)) / Decimal("100")
    return _cents(amount)


def _item_line(item, quantity, amount):
    label = item.get("name") or item["sku"]
    if len(label) > 20:
        label = label[:17] + "..."
    money = "(" + str(-amount) + ")" if amount < 0 else str(amount)
    qty_text = "x" + str(-quantity) + " RETURN" if quantity < 0 else "x" + str(quantity)
    return label.ljust(20) + " " + qty_text.ljust(11) + " " + money.rjust(11)


def _item_lines(order):
    lines, subtotal, taxable = [], Decimal("0"), Decimal("0")
    for item in sorted(order.get("items") or [], key=lambda it: it["sku"].lower()):
        quantity = item.get("qty", 1)
        if quantity == 0:
            continue
        amount = _amount(item, quantity, order)
        lines.append(_item_line(item, quantity, amount))
        subtotal += amount
        if item.get("taxable", True):
            taxable += amount
    return lines, subtotal, taxable


def _totals(order, subtotal, taxable):
    rate = order.get("tax_rate")
    rate = 0 if rate is None else rate
    tax = max(_cents(taxable * Decimal(str(rate)) / Decimal("100")), Decimal("0.00"))
    total = subtotal + tax
    lines = ["Subtotal:".ljust(33) + str(subtotal.quantize(CENT)).rjust(11)]
    if rate:
        lines.append(("Tax (" + str(rate) + "%):").ljust(33) + str(tax).rjust(11))
    if total < 0:
        lines.append("CREDIT DUE:".ljust(33) + str(-total.quantize(CENT)).rjust(11))
    else:
        lines.append("TOTAL DUE:".ljust(33) + str(total.quantize(CENT)).rjust(11))
    return lines


def render_invoice(order):
    lines, currency = _header(order)
    item_lines, subtotal, taxable = _item_lines(order)
    lines += item_lines or ["(no items)"]
    lines.append(RULE)
    lines += _totals(order, subtotal, taxable)
    lines.append(currency + " " + str(len(item_lines)) + " line(s)")
    return "\n".join(lines) + "\n"
