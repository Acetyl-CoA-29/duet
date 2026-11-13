"""Plain-text invoices for the billing export."""
from decimal import ROUND_HALF_UP, Decimal


def render_invoice(order):
    lines = []
    name = order.get("customer")
    if name is None or not str(name).strip():
        lines.append("Customer: (unknown)")
    else:
        lines.append("Customer: " + str(name).strip())
    cur = order.get("currency") or "USD"
    cur = cur.upper()
    lines.append("Currency: " + cur)
    lines.append("-" * 44)
    items = order.get("items") or []
    items = sorted(items, key=lambda it: it["sku"].lower())
    sub = Decimal("0")
    taxable = Decimal("0")
    shown = 0
    for it in items:
        q = it.get("qty", 1)
        if q == 0:
            continue
        price = Decimal(str(it["price"]))
        amount = price * q
        pct = it.get("discount_pct")
        if pct is None:
            pct = order.get("discount_pct")
        if pct is None:
            pct = 0
        if pct:
            amount = amount - amount * Decimal(str(pct)) / Decimal("100")
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        label = it.get("name") or it["sku"]
        if len(label) > 20:
            label = label[:17] + "..."
        if amount < 0:
            money = "(" + str(-amount) + ")"
        else:
            money = str(amount)
        if q < 0:
            qty_text = "x" + str(-q) + " RETURN"
        else:
            qty_text = "x" + str(q)
        lines.append(label.ljust(20) + " " + qty_text.ljust(11) + " " + money.rjust(11))
        sub = sub + amount
        if it.get("taxable", True):
            taxable = taxable + amount
        shown = shown + 1
    if shown == 0:
        lines.append("(no items)")
    lines.append("-" * 44)
    rate = order.get("tax_rate")
    if rate is None:
        rate = 0
    tax = (taxable * Decimal(str(rate)) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if tax < 0:
        tax = Decimal("0.00")
    total = sub + tax
    lines.append("Subtotal:".ljust(33) + str(sub.quantize(Decimal("0.01"))).rjust(11))
    if rate:
        lines.append(("Tax (" + str(rate) + "%):").ljust(33) + str(tax).rjust(11))
    if total < 0:
        lines.append("CREDIT DUE:".ljust(33) + str(-total.quantize(Decimal("0.01"))).rjust(11))
    else:
        lines.append("TOTAL DUE:".ljust(33) + str(total.quantize(Decimal("0.01"))).rjust(11))
    lines.append(cur + " " + str(shown) + " line(s)")
    return "\n".join(lines) + "\n"
