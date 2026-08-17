# Configure provider transport and upstream-account concurrency

Status: implemented

## Problem

`gpt-image-2` was assumed to always use SSE, while upstream providers can return synchronous JSON or an asynchronous `task_id`. Worker locking was scoped to a website account and route, although the upstream limit of 50 is shared by the whole upstream account across API keys.

## Decision

- Configure image response mode per API provider: `auto`, `sync_json`, `sse`, or `async_task`.
- Keep response parsing tolerant and poll an accepted `task_id` with GET only; never repeat the paid POST.
- Configure a `concurrency_group` shared by every provider/key belonging to one upstream account.
- Enforce the strictest configured `max_concurrency` in that group with PostgreSQL advisory-lock slots held through the complete provider call, including async polling.
- Default operational concurrency to 20, leaving headroom below the known upstream account limit of 50.
- Default upstream request timeout to 600 seconds; the durable generation task deadline remains independent.

## Verification

- Provider persistence and admin HTTP/UI expose the new settings.
- Adapter tests cover sync JSON, SSE and async task polling.
- Worker tests cover the shared provider pool query and slot behavior.
