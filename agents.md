# Documentation Agent Rules

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
