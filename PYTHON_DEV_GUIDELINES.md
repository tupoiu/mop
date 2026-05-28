# Python Development Guidelines

## Sensible well defined interfaces

Prefer `TypedDict`over untyped `dict[str, Any]` or `dict[str, str]` where the row structure is known.