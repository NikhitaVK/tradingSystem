# Decision: Manual Trello↔Sheets sync for the 2-week project

**Date**: 2026-05-11

## Decision

Do not wire up Zapier/Apps Script automation between Trello and Google Sheets; update the Gantt manually.

## Reason

A 10-second manual update per card is faster than configuring Zapier for a 2-week project; the automation would have to be re-tested and is overkill for the scope.

## Alternatives Considered

- **Zapier free tier** — rejected: 10-minute setup overhead with no real time saved for a 2-week project.
- **Google Apps Script** — rejected: requires custom JS, more brittle than manual updates at this scale.


## Related

- MOC: [[_tasks]]
- [[2026-05-11-trello-gantt-planning]]
