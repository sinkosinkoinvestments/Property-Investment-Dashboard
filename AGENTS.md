# Codex / Agent Instructions

This repository powers the Property Investment Dashboard published through GitHub Pages.

## Project context

- Primary app files live under `dashboard/`.
- Static data files live under `data/` and are loaded by dashboard HTML/JavaScript using GitHub raw URLs.
- The public site is served from GitHub Pages, so relative paths and file names matter.

## Default workflow

- Prefer small, focused changes.
- Do not commit directly to `main` for larger feature work unless explicitly requested.
- For non-trivial changes, create a branch and pull request with a clear summary.
- Preserve existing dashboard behaviour unless the task explicitly asks to change it.
- Keep the site usable on mobile and desktop.

## HTML/CSS/JavaScript rules

- Avoid placing page sections after `</body>` or `</html>`.
- Keep each dashboard page section inside the main `.content` container.
- Keep navigation anchors aligned with section IDs.
- Avoid introducing build steps unless explicitly requested.
- Prefer plain HTML, CSS, and JavaScript for this repo.
- Be careful with inline event handlers because the current dashboard uses them.

## Testing checklist

After changes to dashboard pages:

1. Check that the target page loads at `dashboard/test.html#target`.
2. Check that dashboard navigation still works.
3. Check that CSV data loads from the `data/` folder.
4. Check browser console for JavaScript errors.
5. Check that layout remains readable on small screens.

## Current known priority

The `dashboard/test.html` page should be cleaned up so the Target Properties section is inside the document structure and uses the card-grid layout from `dashboard/backup-property-investment-scoring-dashboard.html#target`.
