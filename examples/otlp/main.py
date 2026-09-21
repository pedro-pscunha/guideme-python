"""Build the support-triage questions and print what each one puts on the wire.

This half of the example needs no API key and makes no request: it shows how a
caller declares their own options and levels, and what the rubric the model
reads looks like once it is built. Run it with `uv run main.py`.
"""

from guideme import Choice, Levels, choose, fallback, noul, score


class Department(Choice):
    """Where a support ticket should be routed. The value of a member is its rubric."""

    billing = "Payments, invoicing, refunds"
    technical = "Bugs, outages, integrations"
    sales = fallback("Pricing, upgrades, new accounts")


class Frustration(Levels):
    """How the customer sounds, low to high. Declaration order is level order."""

    calm = "Calm and polite"
    frustrated = "Frustrated"
    very_angry = "Very angry"


def main() -> None:
    """Print the id, the instructions and the rubric of each question in the batch."""
    batch = (
        noul("Should this ticket be escalated?"),
        choose(Department, "Which team should handle this?"),
        score(Frustration, "How frustrated is the customer?"),
    )
    for index, question in enumerate(batch):
        print(f"q{index}: {question.instructions}")
        print(f"     {question.spec}")


if __name__ == "__main__":
    main()
