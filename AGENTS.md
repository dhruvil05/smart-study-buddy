# Smart study buddy

This project: it's and EdTech site to learn new things, user by self learners.to know more read `README.md`

## Setup
- Check `README.md` to know how to run this project.

## Tests and checks
- Tests:
- Lint:
Keep these the same as the check steps set in the factory dashboard.

## Conventions
- follow the project structure.
- Make sure UI is as per theme.
- maintain the code quality.

## Software factory
`python factory/bin/run.py` (or `/factory run` in Claude Code) builds open GitHub issues
labeled `factory`: intake → spec → implement → gate → review → approve, one PR per issue.
State: `factory/jobs/` (gitignored), written only by `python factory/bin/job.py`.
Settings and check steps: `python factory/bin/serve.py` (dashboard). Each job works in
`.worktrees/<job-id>`; tools that scan the repo should exclude `.worktrees/`.


## Factory profile

### What this project is
- [Product name]: [what it does] for [who pays for it].
- Public site plus a signed-in app; users sign up themselves.

### Users and roles
- Visitor: public pages, sign up, sign in.
- Member: own data inside own [workspace/account].
- Owner: members, plan and billing of own [workspace].
- [Support/Admin]: [internal tools; read-only on customer data unless stated].

### Sensitive areas
Always goes to a human:
- Billing: plans, prices, checkout, refunds, trials, [payment provider] webhooks.
- Sign-up, sign-in, password reset, sessions, OAuth.
- Access to another user's or workspace's data.
- Account or data deletion.
- Emails sent to customers.
- Privacy: tracking, cookies, consent, personal data.

### Authorization
- Checks: [middleware/guards path]; every request scoped to the signed-in user's [workspace].
- Plan limits enforced on the server in [path].
- Verify by: [test requesting another workspace's record and expecting 404].

### Data rules
- Every customer table has [workspace_id]; queries always filter by it.
- Money: [integer cents], amounts come from [payment provider]; never trust client prices.
- Migrations: [tool]; additive, zero-downtime, reversible.
- Personal data: [where it lives]; deletion requests via [process].

### UI conventions
- Components: [library, path]; theme/tokens: [path].
- Every new page: loading, empty and error states; works at phone width.
- Public pages: title and meta description; [SEO rules only if relevant].

### Verify
- Run: [dev server command] at [http://localhost:{PORT}].
- Sign in as: seeded users from factory/seed-users.json ([member, owner]).
- Payments: [provider] test mode only.

### Terms
- [Workspace]: [the account a customer pays for]
