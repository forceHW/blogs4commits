# Learning Progress

## Pending decision
Decision: none pending. Next: learner installs SAM CLI + AWS credentials and runs first deploy; then next component (likely generator or GitHub Actions access to DynamoDB).

## Stack layout and data protection (IMPLEMENTED, not deployed)
- Learner chose: one stack, us-east-1. Initially proposed local PC backup; asked about AWS backups.
- Introduced (AI explained): PITR, on-demand backups, AWS Backup, export to S3; restores create a new table; backup = recovery vs Retain = prevention; single stack means changes hit live table.
- Learner chose weekly AWS Backup, keep 4 weeks; no PITR. Reasoning: low volume (≤7 posts/week) so up to 7 days loss acceptable. Chose Retain + deletion protection ("the safer the better"). Learner's stated goal for backups was recovering a working stack after code breaks; AI clarified backups restore data only, code recovery is via Git.
- Implemented: template.yaml, samconfig.toml, .gitignore, git init. Approved additions: generated table name, AWS::DynamoDB::Table, Sun 05:00 UTC, Retain on vault, commit .vibe-wise.

## Infrastructure as code
- Introduced (AI explained): console vs CloudFormation/SAM vs CDK vs Terraform.
- Learner chose CloudFormation + SAM. Reasons: keep services consistent within AWS; some prior YAML knowledge.

## DynamoDB key design (CONFIRMED, not implemented)
- Learner's placement decision: table PK=project, SK=date; GSI PK=status, SK=date. Reasoning: (project, date) yields exactly one post (unique → table key); GSI should yield multiple posts. Demonstrated understanding of table-key uniqueness vs GSI non-uniqueness.
- AI-proposed additions approved: ISO YYYY-MM-DD dates; ScanIndexForward=false + Limit; attribute_not_exists(project) conditional put, catch ConditionalCheckFailedException; GSI projection ALL; PAY_PER_REQUEST; GSI name status-date-index. AI noted low-cardinality status GSI partition is fine at this scale.
Learner chose to start building with DynamoDB + keys (no reasoning given for order).
- Learner's first proposal: PK = project, SK = "a blog post" (value unspecified); GSI on (project, status) to filter drafts for admin and published for public; skip-on-rerun by querying for an existing post with same date+project before writing. Learner believed a PK is optional at their scale.
- AI feedback: PK is mandatory in DynamoDB; SK value needs to be defined (affects ordering); proposed layout does not serve "all published, newest first, across projects" without one query per project; query-then-write vs atomic conditional put (attribute_not_exists). Asked learner to revise. Stage: awaiting revised proposal.
- Learner's revision: two key pairs — (status, date) to serve public newest-first published and admin drafts; (project, date) for filtering by project. Placement (which is table key vs GSI) not stated.
- AI explained: table primary key must be unique per item and is immutable (changing status would mean delete+recreate); GSI keys need not be unique and can change on update (index updates automatically); conditional put only checks the item with the exact same primary key. Asked learner to decide which pair is the table key. Stage: awaiting placement decision.
Still open later: timezone/"day" boundary, publish→rebuild mechanism, new-draft notification, infrastructure-as-code tool, S3+CloudFront hosting (implied, not chosen).

## Scheduling / automation
- Introduced (AI explained): options for triggering scheduled jobs — GitHub Actions schedule (ephemeral, may be delayed), platform cron, always-on server with cron, event-driven via webhooks; trigger choice ties to where the job runs and what it can reach.
- Learner chose GitHub Actions as nightly runner; noted platform cron worth revisiting if using one platform for multiple services.
- AI explained consequences: ephemeral runner must write drafts to an internet-reachable DB; secrets in Actions.

## Deployment
- Learner self-reports familiarity only with Cloudflare and AWS; unsure where site will live.
- Introduced (AI explained): hosting kinds (static, serverless/edge, PaaS, VM), database options (platform-native vs hosted Postgres vs self-run), Cloudflare vs AWS contrast.
- Learner chose AWS serverless hosting. Reasons given: may not want static-only later; wants to learn AWS services.

## Data storage
- Learner reasoning: no relational entities needed, so asks whether NoSQL fits.
- Introduced (AI explained): choose by access patterns and runtime fit; DynamoDB (serverless, key design up front) vs RDS (ad-hoc queries, but always-on cost and Lambda connection issues / RDS Proxy).
- Learner chose DynamoDB (no further reasoning given yet; revisit at key design).
- Learner chose a status field on the post item for drafts. Reasoning: not interested in saving drafts/revisions, so a single item with status is simplest.
- AI flagged: public query must filter to published (trust boundary); re-run idempotency.

## LLM integration
- Learner requirements: AI picks code highlights from diffs; wants AI to have full project context for quality; diffs usually small; accepts higher cost.
- Introduced (AI explained): context window limits; send-all vs curated subset vs tool-based fetching; multi-repo days; risk that model output may alter snippet text.
- Learner chose: let the AI decide what context it needs (tool use); model returns a JSON object.
- Introduced (AI explained): tool-use loop mechanics — program executes tools, owns loop limits, tools define access boundary; structured outputs for schema-valid JSON.
- Needs reinforcement: snippet verification — learner defined what a highlight is semantically but not how to verify; AI then offered verify vs reference options (proposals). Learner chose to pass diff in, believing that prevents alteration; AI corrected: the guarantee comes from returning pointers that the program resolves, not from passing the diff in.
- Learner then chose verification over pointers. Reasoning: pointers prone to line-formatting errors (valid); "more cost effective" (AI noted likely the opposite — code text costs more output tokens). Proposed strip +/- and exact match via script/regex; AI noted literal substring is safer than regex. Proposed retry on mismatch to "refresh diff inputs"; AI corrected: diff is fixed, retries need feedback and a cap.
- Needs reinforcement: retry semantics (same input → same error), LLM output-token cost.
- Demonstrated understanding: after AI flagged redundancy, learner assigned date/project to the program and summary/highlights to the model. Chose one run per project with shared prompt/tools and per-run arguments.

## Auth
- AI offered alternatives to building auth (PR-as-draft, local tool, Cognito). Learner chose Cognito (managed auth) for an admin page.

## System design: post lifecycle
- Learner requirement: review posts before publishing → AI pointed out this implies draft/published states and a private review surface (trust boundary).

## Stack selection
- Learner chose Python for generator (familiar). Proposed Astro for frontend. Reasoned Spring Boot is too heavy for project scale (AI agreed: JVM cold starts on Lambda, tiny backend).
- Introduced (AI explained): Astro static vs SSR and AWS implications (S3+CloudFront + rebuild on publish vs community adapter/SST on Lambda); admin flow via API Gateway Cognito authorizer + Lambda; Python vs TS Lambdas tradeoff.
- Learner chose static Astro with rebuild on publish (no reasoning given).
- Learner unsure about backend; suggested a JS framework. AI explained: Lambda + API Gateway replace the server framework; handlers are plain functions.
- Learner chose JavaScript (Node.js) for backend Lambda handlers. Reason: learning. AI noted the post schema is now defined in two languages and must be kept in sync.
- Introduced (AI explained): DynamoDB partition key, sort key, Query vs Scan, overwrites and condition expressions, GSIs; method = access patterns first.
