---
name: submission-audit
description: Final evidence-based Microsoft Inside the Game submission readiness audit
# Run only when the operator explicitly invokes /submission-audit.
disable-model-invocation: true
---

Perform a read-only, evidence-grounded final submission audit of MatchLens AI.

1. Recheck the official rules at https://github.com/microsoft/insidethegamehackathon/blob/main/OFFICIAL%20RULES.md if web access is enabled. Otherwise use the checked-in docs and state the verification limit.
2. Check that the repository is public and contains original source and setup instructions; do not claim public visibility unless verified.
3. Run documented tests and lint when available. Record exact commands and results. Check data invariants, reproducibility and evidence-link validation.
4. Verify that the project demonstrably uses a Microsoft/Azure technology, identifying the exact code path and configuration. No unsupported claims.
5. Check working public judge demo, no required payment/key, documented fallback, and accessible page where possible.
6. Check that a public demonstration video under 2 minutes exists and shows real working behavior.
7. Search tracked files for secrets and third-party assets with missing rights.
8. Produce a compact table with status PASS, FAIL or UNVERIFIED, supporting evidence, and highest-priority fixes.
9. Do not deploy, commit, publish or submit on behalf of the user without approval.
