"""Application services.

Each service opens (or joins) a transaction, calls ``core`` for domain logic,
writes an audit event, and commits. Routers stay thin: parse, authorize, call a
service, render (brief section 2).
"""
