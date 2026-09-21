# expect: reportOperatorIssue
"""Two different scales compared against each other. Both are ordered; neither orders the other."""

from guideme import Levels


class Frustration(Levels):
    calm = "Calm and polite"
    frustrated = "Frustrated"


class Urgency(Levels):
    can_wait = "Can wait"
    today = "Today"


MIXED = Frustration.calm >= Urgency.today
