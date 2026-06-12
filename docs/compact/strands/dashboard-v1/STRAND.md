# dashboard-v1

**Status:** in-flight
**Opened:** 2026-06-12
**Landed:**
**Assignees:** ai-math-01
**Target modules:** dashboard, auth
**Active phase:**

## Summary

Ph-1 dashboard module — HTTP entry point for HILDA. Architecture phase draft of MODULE.md + Jinja2 server-side HTML rendering for FR-57/FR-59/FR-60 document section + `/dl/<token>` download endpoint per FR-61 + `/milestone/<id>/refresh` per FR-56 + `/admin/overrides` admin view per FR-31. Kerberos/SPNEGO auth via corp reverse proxy. Per D-074 (Variant A link-out architecture); per D-073 (SP lists pre-exist). 6 open architectural decisions to lock during architecture review pass (HTML engine, reverse-proxy identity forwarding, `core/src/auth/` split, content negotiation, token-expiry UX, CORS posture).

## Notes
