# PRSense architecture decisions

Status: Phase 1 (foundation) in progress. Decisions below were approved by the project owner
unless marked otherwise.

## Decisions

| # | Decision | Rationale | Status |
|---|----------|-----------|--------|
| 1 | Modular monolith (Django), not microservices | One deployable unit, simpler to test and explain at MVP scale | Approved |
| 2 | Django 5.2 LTS | Long-term support; Django 6.1 is newer but has a shorter support window | Approved |
| 3 | Python 3.12 target | Matches the project plan | **Deviation:** see below |
| 4 | Custom `accounts.User` from day one | Avoids a painful migration later; email is unique and required | Implemented |
| 5 | GitHub App with user-to-server OAuth | Granular permissions, expiring tokens with refresh, webhooks on the App | Approved (Phase 2) |
| 6 | PostgreSQL + pgvector for embeddings | One database, real vector indexes | Approved (Phase 5) |
| 7 | LLM and embedding providers undecided | Build against mocks until a provider is chosen | Pending |
| 8 | Config from environment variables only | No secrets in code; production refuses to start without `DJANGO_SECRET_KEY` | Implemented |
| 9 | Deny-by-default DRF permissions | Every endpoint must opt in to public access | Implemented |

## Deviation: Python version used for development

Python 3.12 was approved, but the sandbox used to build PRSense could not download a 3.12
interpreter (the release host was unreachable and the system package mirror was blocked).
Development in this environment uses **Python 3.11.2**, which Django 5.2 supports.

Compatibility plan: the code is written to run on 3.11 and 3.12. Before deployment, the
test suite must be run on 3.12, and CI will pin Python 3.12. This is recorded here rather than
silently changed, so it can be revisited.

## Local testing note

The development sandbox has no Docker, apt, or local PostgreSQL service. PostgreSQL 16 with
pgvector was provided through the `pgserver` PyPI package, which is a sandbox-only convenience
and is not a project dependency. On a normal developer machine, install PostgreSQL directly or
use Docker Compose (Phase 9).

## Database

Phase 1 implements only `accounts.User`. The remaining entities from the product specification
(GitHubConnection, Repository, PullRequest, PullRequestFile, AnalysisJob, RiskAnalysis, RiskFactor,
AIReview, AIFinding, SuggestedTest, RepositoryFile, WebhookEvent) are added in the phase that
uses them, so that each migration is tested against real behavior.

## Phase 2 decisions (GitHub integration)

| # | Decision | Rationale |
|---|----------|-----------|
| 10 | GitHub REST API version defaults to `2026-03-10` (configurable) | Current version listed by GitHub; `2022-11-28` remains supported until March 2028 |
| 11 | OAuth state stored as a SHA-256 hash in an `OAuthState` table | Single-use and expiring in the database. Redis is not needed yet, and a hash means a database read cannot replay a state |
| 12 | `connect` returns `authorization_url` as JSON | A browser redirect cannot carry the Bearer JWT without placing it in a URL, which the specification forbids |
| 13 | Repositories are per PRSense user (unique on `user` + `github_repo_id`) | Access and sync state differ per user. The GitHub ID remains the external identity |
| 14 | Repositories missing from GitHub are deactivated, not deleted | Keeps PR history for analysis instead of destroying it |
| 15 | Token refresh runs under `select_for_update` | Two concurrent requests must not both spend a single-use refresh token |
| 16 | Webhook payloads are validated and recorded, but not used to write PR data in Phase 2 | Expensive and trust-sensitive work moves to Celery in Phase 7; the payload is not trusted until PRSense re-fetches it |
| 17 | `WebhookEvent` added to the design | Needed for idempotency, which the specification requires |
| 18 | PR sync reads all pages (capped by `GITHUB_PR_SYNC_MAX_PAGES`) before writing | Avoids holding a database transaction open during HTTP calls. The cap means very large repositories are only partially synced; this is a known limitation |

Known limitations in Phase 2: live GitHub OAuth has not been exercised against a real GitHub App;
the callback and token exchange are covered by mocked tests only. Synchronization runs inside the
request, which will move to Celery in Phase 7.
