# expect: reportMatchNotExhaustive
"""A `match` over a Choice that forgets a member. Adding an option must break every match."""

from guideme import Choice, fallback


class Department(Choice):
    billing = "Payments, invoicing, refunds"
    technical = "Bugs, outages, integrations"
    sales = fallback("Pricing, upgrades, new accounts")


def route(department: Department) -> str:
    match department:
        case Department.billing:
            return "payments"
        case Department.technical:
            return "engineering"
