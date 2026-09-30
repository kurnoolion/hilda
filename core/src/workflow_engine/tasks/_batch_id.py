"""Env-scoped BATCH-id generation for owner-outreach ownership.

Two HILDA environments share the OMADM_BOT inbox (staging + production).
Owner replies land in that shared inbox with the outbound BATCH-<id> token
in the subject. Without an env token embedded in the id, the receiving side
cannot tell from the subject alone which env sent the batch, and either env
would race to fetch + mark read the reply -- the losing env then never sees
its own reply.

Format:  BATCH-<env>-<10-hex>

where <env> comes from the HILDA_ENV process-env var (defaults to "prd" so
production behavior is the safe fallback when unconfigured) and <10-hex> is
the first 10 hex chars of a seed (correlation_id or a uuid5 hex).

The inbound owner-reply path (email_polling) uses the env token to fast-path
ownership: if the incoming subject's BATCH-<env>- prefix matches this HILDA's
env, we own it; if it names a different env, the other HILDA owns it and we
leave the mail unread. Batches sent before this helper landed lack the env
token entirely; those fall back to a Postgres lookup against
communication_log (see storage.audit_ops.is_outbound_batch_here).
"""
from __future__ import annotations

import os


__all__ = ["env_token", "make_batch_id"]


def env_token() -> str:
    """HILDA env token embedded in outbound BATCH-ids.

    Default 'prd' when HILDA_ENV is unset -- production behavior is the safe
    fallback; the only side effect of a misconfigured staging env is that its
    batch_ids look like prod's and rely on the Postgres-lookup slow path for
    ownership discrimination.
    """
    return (os.environ.get("HILDA_ENV") or "prd").strip().lower()


def make_batch_id(seed: str) -> str:
    """Return an env-scoped BATCH-<env>-<10-hex> id from a hex-safe seed.

    seed: correlation_id (UUID string, dashes tolerated) or a uuid5 hex.
          Dashes are stripped; the first 10 chars of the remainder are used.
    """
    hex_part = (seed or "").replace("-", "")[:10]
    return f"BATCH-{env_token()}-{hex_part}"
