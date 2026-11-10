# Project Map

## Purpose
A public blog that shows people what the learner is up to. An AI reads the learner's commit history every day and writes a summary post for days that had activity.

## Requirements
- MVP: Run daily. Read the learner's commit history and generate an AI summary of that day's work.
- MVP: Skip days with no activity.
- MVP: The learner can add their own notes/ideas to posts (optional per post).
- MVP: A public website displays the posts.
- MVP: Learner reviews each draft before it is published (draft → finalize → publish).
- Commit source: one GitHub account, public repos.
- Hosting: AWS serverless (chosen; specific services unknown).
- Database stores only approved/published posts; public site queries it (chosen). DB type unknown (DynamoDB vs relational under discussion).
- Admin page protected by AWS Cognito (chosen).
- Database: DynamoDB. Key design CONFIRMED (not implemented): table PK=project, SK=date (ISO YYYY-MM-DD); GSI status-date-index PK=status, SK=date, projection ALL; on-demand billing. Public: Query GSI status=published newest-first; admin drafts: Query GSI status=draft; get one: GetItem(project,date); nightly skip: conditional put attribute_not_exists. Drafts stored in same table with a status field (draft/published); no revision history (chosen; supersedes earlier "DB holds only approved posts").
- Requested feature (MVP status not specified): posts show code highlights; AI picks them from the diffs (chosen). Verification that displayed code matches diff: unknown.
- AI decides how much context it needs via tool use (chosen; tool set and limits unknown). Cost acceptable.
- One post per (date, project); posts have a project attribute (chosen).
- Draft generation pipeline: CONFIRMED design (not implemented):
  - Learner's decisions: one agent run per project per day; same system prompt and tools, args date/project/diff. Diff passed in; model may use tools for extra context. Model returns JSON summary + code highlights; program sets date/project. Highlights verified against diff with +/- markers stripped, exact match; retry model on mismatch. Draft written to DynamoDB status=draft.
  - AI's additions (approved by learner): highlight = {file, code, explanation}; normalize trailing whitespace and \r, literal substring check (not regex); retry includes failure feedback; max 2 retries then drop+log failing highlights and save draft; read-only read_file/list_directory tools scoped to repo at that commit via GitHub API; tool-use turn cap (~15); structured outputs with JSON schema; re-run skips if item for (date, project) already exists.
- AI should have some repository context beyond commits (how much: unknown; tradeoff of tokens/time).
- Future (not MVP): The AI also reads the learner's logs to see what they've been working through and adds that to posts.

## Components
None chosen yet.

## Main Flow
(learner's flow; chosen: GitHub Actions nightly runner, admin page for review — not implemented)
GitHub Actions nightly schedule --> any commits today on GitHub? --no--> stop
                                         |yes
                                         v
                       AI drafts post + picks code highlights (context: ?) --> DynamoDB item status=draft
                                         --> learner reviews on admin page (auth: ?) + adds notes + finalizes
                                         --> status=published --> public website (AWS serverless; queries published only)

## Data and Trust Boundaries
Unknown. Open: where commit history comes from (which repos/hosts, public or private), which AI provider, where posts are stored, who can add notes.

## Build and Deployment
IaC: AWS SAM / CloudFormation. Git repo. One stack `blog-notes` in us-east-1 (samconfig.toml).
template.yaml (implemented, not yet deployed): PostsTable (Retain, deletion protection, generated name), BackupVault (Retain), weekly BackupPlan (Sun 05:00 UTC, 28-day retention), BackupRole, BackupSelection; outputs table name/ARN, vault name.

## Unknowns
- Generator language: Python (chosen). Frontend: Astro static build, rebuilt on publish (chosen; hosting likely S3+CloudFront, not chosen). Publish→rebuild mechanism unknown. Backend: JavaScript (Node.js) Lambda handlers, no framework (chosen; not implemented). API Gateway + Cognito authorizer in front: AI-explained typical shape, not explicitly chosen. Post schema exists in Python and JS — keep in sync.
- AI provider/model (tool use + structured outputs assumed in design)
- How GitHub Actions / Lambdas get AWS credentials and the table name
- Timezone / "day" boundary for nightly run
- Notification of new drafts
- Public site and admin page design (AWS serverless services), how notes are added to posts
- Meaning of "logs" for the future feature
