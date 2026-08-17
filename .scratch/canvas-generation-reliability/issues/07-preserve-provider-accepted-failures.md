# Preserve Provider-accepted failures for billing reconciliation

Type: task
Status: resolved

## Problem

An OpenAI-compatible image Provider can return HTTP 200, start an SSE response, charge the upstream account, and then emit an error event without an image. The adapter currently records that outcome as `provider_rejected`, discarding the upstream request ID and making Provider cost reconciliation misleading.

## Decision

- Treat an error received after a successful SSE connection, or after an asynchronous task ID was returned, as an accepted submission whose image delivery failed.
- Persist a safe Provider task reference, preferring the returned task ID, then the upstream request ID, then the local idempotency key.
- Keep the task terminal and release the user's frozen credits, but expose the sanitized Provider failure reason and request reference for billing reconciliation.
- Poll only when the Provider returns an explicit task ID, using GET and never repeating the paid POST.

## Answer

HTTP 200 SSE errors and asynchronous task failures now produce `ProviderSubmissionDeliveryFailed`. The attempt remains Provider-accepted, the preferred safe upstream reference is persisted, and the terminal task error contains a sanitized reconciliation reference while user credits are released. Ordinary HTTP rejection behavior is unchanged.

The deployed Worker image is `wandou-studio-local:provider-accepted-failure-zh-20260814`. All four Workers were rolled with no active generation tasks. Targeted tests report 71 passed; the full backend suite reports 536 passed and 4 skipped, with two unrelated pre-existing Web UI static-version assertion failures.

## Comments

2026-08-14：修复 OriginBoost HTTP 200/SSE 后错误被误记为 Provider 拒绝的问题，并上线 4 个生成 Worker。
