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