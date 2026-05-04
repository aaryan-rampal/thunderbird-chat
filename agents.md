# Agent Rules

## Autonomy

Do things yourself. When something breaks or is unclear, investigate before asking. Form a hypothesis, add instrumentation or run a probe, then confirm or revise. Only hand off to the user when there is a hard boundary you genuinely cannot cross (e.g. Thunderbird add-on reload, GUI interaction).

When you do hand off, be precise: exact file path, exact command, exact thing to look for in output. Never vague.

Add observability as you go — log files, debug endpoints, console breadcrumbs — so the next failure tells you where it broke without guessing.

Own the full local rig when you need it: start the server yourself, tail the log file, send curl probes, compare before/after. Don't wait for the user to run commands and paste output back — spin up what you need, verify it works, then proceed. If a server is already running on the port, check before starting another.

When something doesn't reach the backend, don't ask — add logging to both sides and re-trigger. When a run produces unexpected output, diff it against the previous run yourself. When a test fails, read the traceback, form a fix, verify it locally before reporting back.

## Documentation Agent Rules

This project follows these documentation conventions for all Python and JavaScript
code touched in this worktree.

## Python Docstring Standard (strict)

- Use Google-style docstrings for public modules, classes, functions, and methods.
- Use triple-quoted strings with sections in this order when needed:
  - Short summary line.
  - Blank line.
  - `Args:`
  - `Returns:`
  - `Raises:` (if exceptions are raised).
- Prefer explicit, concise names in `Args` and `Returns`.
- Keep wording actionable and technical (no decorative prose).
- Keep docstrings stable and focused on behavior and contract.

## JavaScript/JSDoc Standard (pragmatic)

- Use JSDoc blocks for public-facing helpers and async workflow functions.
- Include:
  - short summary.
  - `@param` entries for each input.
  - `@returns` with return type and meaning.
  - `@throws` when a function can reject/throw.
- Keep comments minimal and useful for consumers/debugging.
- Avoid duplicating obvious comments for one-liners.
