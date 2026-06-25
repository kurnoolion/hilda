# Module: customizations/credentials

**Purpose**: Per-deployment drop-zone for sops-encrypted credential files that `core/src/credential_service/SopsCredentialService.load()` consumes at container start. Anchors `[D-001]` three-tier convention (customizations as data drop-zone), `[D-019]` (credential_service Protocol contract), `[D-038]` (sops + age encryption + ops-runbook rotation), and serves NFR-2 (no credential material in logs / reports / git plaintext) and NFR-3 (Ph-1/Ph-2 shared ops-team credential model; per-PM Vault deferred to Ph-3+ per DEF-14).

This module is **data, not code**. There is no `__init__.py` runtime surface, no CLI, no public Python API. The directory layout + file naming convention IS the contract; `core/src/credential_service/` validates conformance via `python -m core.src.credential_service.credential_service_cli --diagnostic` against the repo-source path.

---

## Layout

```
customizations/credentials/
├── age.key.pub                              # age public key (encryption-side, in git);
│                                            # ops uses this to encrypt new credential files
│                                            # before commit. NOT the decryption key.
│
├── email.env.sops                           # HILDA mailbox IMAP/SMTP credentials per FR-12 / FR-23.
│                                            # Single shared credential (HILDA team mailbox).
│                                            # Env vars: HILDA_EML_AUTH_TYPE, HILDA_EML_USERNAME,
│                                            # HILDA_EML_PASSWORD (or HILDA_EML_BEARER for OAuth2).
│
├── sharepoint.env.sops                      # SP service account NTLM/Kerberos per [D-006].
│                                            # Single shared credential (SP service account).
│                                            # Env vars: HILDA_SHP_AUTH_TYPE=ntlm|kerberos,
│                                            # HILDA_SHP_USERNAME, HILDA_SHP_PASSWORD (NTLM only),
│                                            # HILDA_SHP_KEYTAB_PATH (Kerberos only).
│
├── llm_ollama_a4000.env.sops                # Ollama on RTX A4000 box per [D-052].
│                                            # MAY be empty / no-auth (lab subnet). If empty,
│                                            # `credential_service.--validate` reports
│                                            # `auth_type=none`; LLMGatewayServer skips Authorization
│                                            # header for this backend.
│
├── llm_vllm_dgx.env.sops                    # vLLM on DGX Spark box per [D-052].
│                                            # Same no-auth pattern as llm_ollama_a4000.
│
├── llm_corp_llm.env.sops                    # Corp on-prem LLM per [D-007] / [D-052].
│                                            # Single shared credential (HILDA team's corp LLM API account).
│                                            # Env vars: HILDA_LLM_CORP_LLM_AUTH_TYPE=api_token|oauth2_bearer,
│                                            # HILDA_LLM_CORP_LLM_API_TOKEN (or _BEARER for OAuth2).
│
├── customer_jira/                           # PER-(account, customer) credentials per FR-25 (b)
│                                            # carrier-governance lock 2026-06-19. Each carrier's
│                                            # JIRA expects identifiable individual accounts for
│                                            # audit — NOT a service account.
│   ├── <account_id>/                        # `account_id` is EITHER assigned_pm_id
│   │                                        # (= TPM.User_name per [D-088]) when PM has personal
│   │                                        # carrier JIRA account, OR a HILDA OPS member id
│   │                                        # (= corp directory slug for the ops member).
│   │                                        # The account-id-per-customer mapping lives in
│   │                                        # `customizations/issue_tracker/<customer_id>_jira_config.yaml`.
│   │   ├── <customer_id>.env.sops           # Per-(account, customer) carrier JIRA credential.
│   │                                        # Example: customer_jira/y.vasilyev/MMK.env.sops
│   │                                        # Env vars: HILDA_ITR_AUTH_TYPE=api_token|basic,
│   │                                        # HILDA_ITR_API_TOKEN (api_token PAT)
│   │                                        # OR HILDA_ITR_USERNAME + HILDA_ITR_PASSWORD (basic).
│   │   └── ...                              # Additional <customer_id>.env.sops per customer
│   │                                        # under the same account
│   └── ...                                  # Additional <account_id>/ trees
│
└── customer/                                # PER-customer credentials per FR-19 / FR-77.
                                             # Ph-1: Google Drive upload per [D-054].
    ├── <customer_id>.env.sops               # Per-customer carrier upload destination credential.
    │                                        # Example: customer/MMK.env.sops
    │                                        # Env vars: HILDA_CUSTOMER_AUTH_TYPE=api_token,
    │                                        # HILDA_CUSTOMER_API_TOKEN (Google Drive OAuth2 token
    │                                        # OR service account key per [D-054]).
    └── ...                                  # Additional per-customer credential files
```

**Container view** (bind-mount target at `/etc/hilda/credentials/`) sees identical layout — same encrypted files, just accessed from inside the container's filesystem. `core/src/credential_service/SopsCredentialService` resolves the `.enc.env` files at container start, decrypts via `sops --decrypt` using the age PRIVATE key at `/etc/hilda/age.key` (mode 0400, ops-provisioned outside git per `[D-038]`), and caches decrypted `Credential` objects in process memory for the container lifetime.

---

## What does NOT live here

These systems have **no HILDA-side credential** per architect locks 2026-06-21 + FR-25 (a) + FR-51 pattern (d):

- **`corp_plm_gateway/`** — corp PLM gateway authentication is handled gateway-side via IP-allowlist + fixed-key infrastructure on the reverse-proxy PC. HILDA calls the gateway passing only the corp human id (from `customizations/issue_tracker/<corp_plm_slug>_adapter_config.yaml`) as an API parameter; HILDA→gateway is lab-subnet IP-allowlist. No `.env.sops` file exists here.
- **`corp_messenger_gateway/`** — same pattern. Gateway uses fixed corp identity to talk to corp messenger; HILDA→gateway is lab-subnet IP-allowlist + identity passed via API parameter. No `.env.sops` file exists here.

Calls to `credential_service.get_credential(pm_id, "messenger" | "issue_tracker")` for corp PLM (under ISSUE_TRACKER) intentionally raise `CRD-E001`. Callers MUST use the IP-allowlist + identity-assertion pattern instead (see `customizations/issue_tracker/<corp_plm_slug>_adapter_config.yaml`).

---

## Ops runbook references

- **Initial provisioning**: `docs/ops/credential_provisioning.md` (Ph-1 deliverable; `[D-038]` impl note 2026-05-26 references). Generates age keypair, encrypts `.env` files via `sops`, commits encrypted files to git.
- **Rotation**: per-system rotation runbook per `[D-038]`. SIGHUP `credential_service` (via `core/src/credential_service/SopsCredentialService.reload()`) after committing rotated `.env.sops`; no container restart needed.
- **Onboarding a new customer**: add `customer/<new_customer_id>.env.sops` + customer_jira tree entries; SIGHUP HILDA processes; update `customizations/issue_tracker/<customer_id>_jira_config.yaml` with the account-id mapping.
- **Onboarding a new PM JIRA account**: add `customer_jira/<account_id>/<customer_id>.env.sops`; update `customizations/issue_tracker/<customer_id>_jira_config.yaml`'s `account_id` field; SIGHUP.

---

## Invariants

- **Never commit plaintext credentials.** Pre-commit hook validates `.env.sops` files are sops-encrypted (header check). Plaintext `.env` files in this directory are a hard error.
- **Never commit the age private key.** The `age.key` file is ops-provisioned outside git per `[D-038]`; only `age.key.pub` (public side) lives in git.
- **Per-(account, customer) and per-customer file paths are stable identifiers.** Renaming a customer mid-Ph-1 requires file rename + git history rewrite OR an additional alias file (lookup falls through). Recommended: lock `customer_id` at first onboarding per FR-2 R&R discipline.
- **`.env.sops` env-var prefix follows `HILDA_<MODULE_PREFIX>_*`** per `core/src/credential_service/protocol.py` `SYSTEM_ENV_PREFIX`. Per-customer/per-(account,customer) files use the same prefix as the system_type they serve.
- **No credentials for corp PLM gateway or corp messenger gateway** in Ph-1/Ph-2. Adding `.env.sops` files for these systems is a design error — they use the IP-allowlist + identity-assertion pattern per FR-25 (a) + FR-51 pattern (d).

---

## Non-goals

- **Not a credential authoring UI.** Ops uses `sops` CLI directly. Web UI for credential management deferred to Ph-3+ per DEF-14.
- **Not a runtime API.** This directory is read at container start only; runtime SIGHUP refreshes (no per-request decryption).
- **Not a secret manager.** Vault-backed implementation lands at Ph-3+ as a new `VaultCredentialService` class per `[D-019]`; the sops drop-zone does not call Vault.
- **Not the audit log.** `CommunicationLog` writes for FR-42 are performed by the adapter that called `get_credential`, not by this drop-zone or `credential_service` itself.

---

## Depends on

- `core/src/credential_service/` — Protocol + `SopsCredentialService` implementation that consumes this drop-zone.
- `sops` binary on the host — installed by ops as a system dependency.
- `age` private key at `/etc/hilda/age.key` (ops-provisioned, NOT in git).

---

## Depended on by

- `core/src/credential_service/SopsCredentialService.load()` reads all `.env.sops` files at container start.
- `customizations/issue_tracker/<customer_id>_jira_config.yaml` carries the `account_id` mapping that selects which `customer_jira/<account_id>/<customer_id>.env.sops` is the correct file per FR-25 (b).

---

## Test interface

Validation runs through `core/src/credential_service/credential_service_cli.py`:

```
# Validate all repo-source credential files decrypt cleanly:
python -m core.src.credential_service.credential_service_cli --diagnostic \
  --env-dir customizations/credentials --age-key /etc/hilda/age.key

# Validate a specific system_type's credential is structurally complete:
python -m core.src.credential_service.credential_service_cli --validate \
  --system customer_jira --account-id y.vasilyev --customer-id MMK
```

The `--mock` mode bypasses this drop-zone entirely (uses `MockCredentialService` with in-memory synthetic credentials per SystemType).

---

<!-- BEGIN:STRUCTURE -->

_No public surface detected (module is empty or all-internal)._

<!-- END:STRUCTURE -->
