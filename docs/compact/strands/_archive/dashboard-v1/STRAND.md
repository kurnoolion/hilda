# dashboard-v1

**Status:** landed
**Opened:** 2026-06-12
**Landed:** 2026-06-14
**Assignees:** ai-math-01
**Target modules:** dashboard, auth
**Active phase:**

## Summary

Ph-1 dashboard module — HTTP entry point for HILDA. Architecture phase draft of MODULE.md + Jinja2 server-side HTML rendering for FR-57/FR-59/FR-60 document section + `/dl/<token>` download endpoint per FR-61 + `/milestone/<id>/refresh` per FR-56 + `/admin/overrides` admin view per FR-31. Kerberos/SPNEGO auth via corp reverse proxy. Per D-074 (Variant A link-out architecture); per D-073 (SP lists pre-exist). 6 open architectural decisions to lock during architecture review pass (HTML engine, reverse-proxy identity forwarding, `core/src/auth/` split, content negotiation, token-expiry UX, CORS posture).

## Notes

Landed on 2026-06-14 with 8 promoted decisions: D-075, D-076, D-077, D-078, D-079, D-080, D-081, D-082. Plus 4 impl notes appended to existing canonical entries: D-006 (NTLM-only confirmation), D-073 (Customers/Devices SP HILDA-readable columns), D-077 (×2 — D-DRAFT-Z v2 AMEND-14b joint key/slug→id and AMEND-14c final R&R lock).
