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

- [x] **Pull request checks**: `.github/workflows/ci.yml` runs lint, tests,
  the frontend build, a Docker build, a secret scan and the
  `VERSION`/changelog check on every pull request.
- [x] **Release workflow**: `.github/workflows/release.yml`.
- [x] **Dependabot**: `.github/dependabot.yml`: weekly, grouped updates for
  Python and npm packages, the Docker base images and GitHub Actions.
- [x] **Image cleanup**: `.github/workflows/cleanup-images.yml` deletes
  untagged images from `ghcr.io/pidi3000/fuel_tracker` every week, keeping the
  10 newest versions. The release workflow builds without provenance
  attestations, so there are no untagged versions that belong to a tagged
  image.
- [x] **Pre-commit checks**: `.pre-commit-config.yaml` with ruff,
  ESLint/Prettier, gitleaks and file hygiene checks, also run in CI.
- [x] **Test receipts**: the example receipts in `backend/tests/fixtures/receipts`
  are generated with made-up values; real receipts are not committed.
- [ ] **GitHub setting**: allow workflows to write (Settings → Actions →
  General → Workflow permissions), needed for tags, releases and images.
