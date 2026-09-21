# expect: reportArgumentType
"""A `Model` where an `ApiKey` belongs. Two strings; only one of them is a secret."""

from guideme import Guide, Model

guide = Guide.builder().api_key(Model("jev-latest")).build()
