# expect: reportAssignmentType
"""The key where a plain string belongs. This is what stops it reaching a log or a payload."""

from guideme import ApiKey

SECRET: str = ApiKey("not-a-real-key")
