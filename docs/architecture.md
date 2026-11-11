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
