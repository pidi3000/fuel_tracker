# Project administration

Decisions and open to-dos for things around the app (CI, releases, repository
housekeeping), not the app itself.

## Decisions

- **Repository stays private.** No license needed. Branch protection is not
  available for private repositories on the current plan, so CI on pull
  requests is the safety net instead.
- **x86 (amd64) images only.** ARM support can be added later with one line
  in the release workflow (`platforms: linux/amd64,linux/arm64`).
- **Releases** are driven by the `VERSION` file and `CHANGELOG.md`; see
  [Versioning and releases](technical-design.md#versioning-and-releases).

## To do once code exists

To be discussed with the user before setting up.

- [ ] **Pull request checks**: workflow running lint, tests, frontend build
  and the `VERSION`/changelog check on every pull request.
- [ ] **Release workflow**: move [`drafts/release.yml`](drafts/release.yml) to
  `.github/workflows/`.
- [ ] **Dependabot**: weekly, grouped updates for Python and npm packages, the
  Docker base image and GitHub Actions.
- [ ] **Image cleanup**: scheduled workflow deleting untagged images older
  than a few weeks from `ghcr.io/pidi3000/fuel_tracker`.
- [ ] **Pre-commit checks**: formatting and lint (ruff, Prettier/ESLint) and a
  secret scanner, also run in CI.
- [ ] **Test receipts**: replace dates, times and IDs in example receipts used
  as test files. Lower priority because the repository is private.
- [ ] **GitHub setting**: allow workflows to write (Settings → Actions →
  General → Workflow permissions), needed for tags, releases and images.
