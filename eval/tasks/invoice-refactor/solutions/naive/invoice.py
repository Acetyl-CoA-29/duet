"""Plain-text invoices for the billing export."""
from decimal import Decimal

RULE = "-" * 44


def _money(value):
    return f"{round(value, 2):.2f}"


def _header(order):
    name = order.get("customer") or "(unknown)"
    currency = (order.get("currency") or "USD").upper()
    return [f"Customer: {name}", f"Currency: {currency}", RULE], currency


def _amount(item, order):
    pct = item.get("discount_pct") or order.get("discount_pct") or 0
    amount = Decimal(str(item["price"])) * item.get("qty", 1)
    return round(amount * (100 - Decimal(str(pct))) / 100, 2)


def _item_line(item, amount):
    label = item.get("name") or item["sku"]
    if len(label) > 20:
        label = label[:17] + "..."
    return f"{label:<20} {'x' + str(item.get('qty', 1)):<11} {_money(amount):>11}"


def _items(order):
    items = [it for it in order.get("items") or [] if it.get("qty", 1) > 0]
    return sorted(items, key=lambda it: it["sku"])


def _totals(order, amounts, items):
    subtotal = sum(amounts, Decimal("0"))
    rate = order.get("tax_rate") or 0
    taxable = sum((a for a, it in zip(amounts, items) if it.get("taxable", True)), Decimal("0"))
    tax = round(taxable * Decimal(str(rate)) / 100, 2)
    lines = [f"{'Subtotal:':<33}{_money(subtotal):>11}"]
    if rate:
        lines.append(f"{'Tax (' + str(rate) + '%):':<33}{_money(tax):>11}")
    lines.append(f"{'TOTAL DUE:':<33}{_money(subtotal + tax):>11}")
    return lines


def render_invoice(order):
    lines, currency = _header(order)
    items = _items(order)
    amounts = [_amount(it, order) for it in items]
    lines += [_item_line(it, a) for it, a in zip(items, amounts)] or ["(no items)"]
    lines.append(RULE)
    lines += _totals(order, amounts, items)
    lines.append(f"{currency} {len(items)} line(s)")
    return "\n".join(lines) + "\n"
