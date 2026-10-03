# Working rules for Claude

Project context: [README.md](README.md) (what the app does) and
[docs/technical-design.md](docs/technical-design.md) (how it is built).

## Branches and pull requests

- Give branches descriptive names, e.g. `feature/receipt-parser` or
  `docs/technical-design`, not random words.
- One topic per pull request.

## Changelog

- Every pull request with a change the user would notice adds a line to the
  `## Unreleased` section of `CHANGELOG.md`, in the same pull request.
- Write for the person running the app, not as a commit list. Use the sections
  *Breaking changes / upgrade notes*, *New* and *Fixed*, and leave out empty
  ones.
- Any change the user must act on when updating (renamed or new required
  setting, LubeLogger setup change, data migration needing attention) goes
  under *Breaking changes / upgrade notes*, with clear instructions.
- Leave out internal changes (refactoring, tests, CI, dependency updates
  without visible effect).

## Releases

- Suggest a release to the user when a good point is reached, for example a
  few new features or a finished restructure. Briefly list what would be in it
  and propose the version number.
- Don't suggest a release while connected changes are only partly merged
  (e.g. a backend feature whose UI isn't done yet, or the first half of a
  restructure). Wait until the related set is complete and usable.
- Never release without the user asking. When asked, open a release pull
  request on a branch named `release/<version>` that only raises `VERSION` and
  renames `## Unreleased` to `## <version> - <date>`. Merging it triggers the
  release workflow.
- Version numbers follow semantic versioning: breaking changes raise the major
  number (from 1.0.0 on), new features the minor number, fixes the patch
  number.
