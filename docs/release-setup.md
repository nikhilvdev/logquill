# Release setup: environments and trusted publishers

Everything here happens outside the repository, in GitHub's settings and on each registry's website, so none of it can be done
by a commit. The release workflow (`.github/workflows/release.yml`) assumes all of it exists. Do it once, before pushing the first
tag. Registry websites change their screens; where a menu path is given it is how it looked when this was written, and each
registry's own "trusted publishing" documentation is the authority for the exact fields.

**The values you will type again and again**

| Field | Value |
|---|---|
| GitHub owner | `nikhilvdev` |
| Repository | `logquill` |
| Workflow file name | `release.yml` |
| Environment name | the one in the table in section 1, per registry |

## 0. What exists today (checked 2026-10-30)

| Name | State | Consequence |
|---|---|---|
| crates.io `logquill-core`, `-runtime`, `-ffi`, `-cli` | all free | new crates: see the bootstrap note in section 2.1 |
| PyPI `logquill` | exists, 2.0.0, yours | add a second trusted publisher to the existing project; the old one points at the retired `logquill-python` repository |
| npm `logquill` | exists, 1.0.1, maintainer `n-dev-11` | add a trusted publisher to the existing package |
| npm `@logquill/wasm`, `@logquill/native-*` | free | the `logquill` organisation (scope) already exists under your npm account; the packages are new, see section 2.3 |
| RubyGems `logquill` | free | a *pending* trusted publisher can be registered before the gem exists |
| NuGet `LogQuill` | free | |
| GitHub `nikhilvdev/logquill-swift` | does not exist | create it (section 4) |
| GitHub environments | `pypi` already exists with **no** protection rules; `github-pages` also exists (unrelated) | section 1 updates `pypi` in place; it never touches `github-pages` |
| Maven group `io.github.nikhilvdev` | not verified | `io.logquill` was dropped: the domain `logquill.io` is not ours (section 3) |

## 1. The seven GitHub environments

An environment is where a publish job pauses for approval, and it is also the name each registry's trusted publisher is bound to,
so the names must match the table exactly.

| Environment | Used by | Registry setup (section) |
|---|---|---|
| `crates-io` | crates.io publish | 2.1 |
| `pypi` | PyPI publish | 2.2 |
| `npm` | `@logquill/wasm`, and `logquill` + `@logquill/native-*` | 2.3 |
| `rubygems` | RubyGems publish | 2.4 |
| `nuget` | NuGet publish | 2.5 |
| `maven-central` | Maven Central upload | 3 |
| `swift-mirror` | Swift mirror publish | 4 |

### In the browser

Repository **Settings → Environments → New environment**, one per row. For each:

1. **Required reviewers:** tick it and add yourself (and anyone else who may approve a release).
2. **Prevent self-review:** leave it **off** if you are the only reviewer. With it on, the person who pushed the tag cannot approve
   their own deployment and the release can never start.
3. **Deployment branches and tags → Selected branches and tags → Add deployment branch or tag rule**, type **Tag**, pattern `v*`.
   This stops a job on any other ref from using the environment, and with it the registry's trusted publisher.
4. Leave environment secrets empty for now; sections 3 and 4 add the few that exist.

### With the GitHub CLI

The command is safe to repeat: `PUT` creates an environment or updates the existing one, which is how it adds reviewers to the
`pypi` environment that is already there.

The same, for all seven. It needs a login that can administer the repository (`gh auth status`); the reviewer is identified by
numeric id.

```sh
me=$(gh api users/nikhilvdev --jq .id)
for env in crates-io pypi npm rubygems nuget maven-central swift-mirror; do
  gh api --method PUT "repos/nikhilvdev/logquill/environments/$env" \
    --input - <<JSON
{
  "reviewers": [{"type": "User", "id": $me}],
  "prevent_self_review": false,
  "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}
}
JSON
  gh api --method POST "repos/nikhilvdev/logquill/environments/$env/deployment-branch-policies" \
    -f name='v*' -f type=tag
done
```

Check:

```sh
gh api repos/nikhilvdev/logquill/environments --jq '.environments[] | "\(.name): \(.protection_rules | length) rule(s)"'
```

You should see seven lines, each with at least one rule (the reviewers). If the tag-rule call is rejected, the environment still
exists; add the rule in the browser.

## 2. Trusted publishers

A trusted publisher lets the registry accept a publish from this one workflow, on this one environment, with a short-lived token the
job asks GitHub for. No password or token is stored anywhere. Each registry asks for the same four facts: owner `nikhilvdev`,
repository `logquill`, workflow `release.yml`, environment as below.

**The bootstrap problem.** crates.io and npm attach a trusted publisher to a package that already exists, and the four crates and
the six new npm packages do not exist yet. (Check each registry's current documentation: if either now lets you register a publisher
for a name that does not exist, skip the hand publish and use the pending form, as RubyGems does.) Until then, the first version of each
of those ten names has to be published once by hand, with a short-lived token, before its trusted publisher can be added. That makes
the first tag the workflow publishes the *next* release candidate: publish `3.0.0-rc.1` by hand, then bump the manifests to
`3.0.0-rc.2` (`scripts/check-versions.py` keeps them in step) and tag `v3.0.0-rc.2`. A registry refuses the same version twice, so the
workflow cannot reuse the version you published by hand.

### 2.1 crates.io: environment `crates-io`

1. Sign in at crates.io with GitHub and verify your email.
2. **Account settings → API tokens → New token**, scope *publish-new* and *publish-update*, crate pattern `logquill-*`, expiry one
   day.
3. From a clean checkout of the tag, in this order (each depends on the previous being indexed; cargo waits for it):
   ```sh
   cargo publish -p logquill-core
   cargo publish -p logquill-runtime
   cargo publish -p logquill-ffi
   cargo publish -p logquill-cli
   ```
4. For each crate: **Settings → Trusted Publishing → Add** (GitHub): owner `nikhilvdev`, repository `logquill`, workflow `release.yml`,
   environment `crates-io`. Then **revoke the token**.

### 2.2 PyPI: environment `pypi`

The project `logquill` exists, so there is no bootstrap. **PyPI → Your projects → logquill → Manage → Publishing → Add a new
publisher → GitHub**: owner `nikhilvdev`, repository `logquill`, workflow `release.yml`, environment `pypi`. Leave the old publisher
(`logquill-python`) in place or remove it; it does not affect this repository.

### 2.3 npm: environment `npm`

1. The organisation that owns the scope, `logquill`, already exists under your npm account (it is why the name shows as taken);
   check it under **npmjs.com → profile → Organizations** and that you are an owner. No new organisation is needed.
2. Existing package `logquill`: **Package → Settings → Trusted Publisher → GitHub Actions**: organisation or user `nikhilvdev`,
   repository `logquill`, workflow `release.yml`, environment `npm`. There is no bootstrap for this one.
3. The six new packages (`@logquill/wasm` and `@logquill/native-darwin-arm64`, `-darwin-x64`, `-linux-arm64-gnu`, `-linux-x64-gnu`,
   `-win32-x64-msvc`) need one hand publish each, with a granular access token (**profile → Access Tokens → Generate New Token**,
   permission *Read and write*, packages and scopes limited to `@logquill`, expiry one day):
   ```sh
   # in bindings/wasm, after: npm ci --ignore-scripts && npm run build && npm run build:sdk
   npm publish --access public --tag next
   # the five native packages: in bindings/node, after building each platform's .node file, run
   #   npx napi create-npm-dirs && npx napi artifacts -d artifacts
   # then in each npm/<platform> directory:
   npm publish --access public --tag next
   ```
   Then add the trusted publisher (same four values) to each of the six, and revoke the token. A native package needs its own
   platform's binary, so five machines (or the workflow's artifacts) are involved; if that is more than you want to do by hand,
   publish the five from a one-off run of the build jobs and download the `node-*` artifacts.
4. The workflow publishes with `--provenance`, which only the trusted-publisher path can produce, so only the unavoidable first
   publish of each name should ever be done from a laptop.

### 2.4 RubyGems: environment `rubygems`

**rubygems.org → profile → Trusted publishers → Pending trusted publishers → Create** (the gem does not exist yet): gem name
`logquill`, GitHub owner `nikhilvdev`, repository `logquill`, workflow `release.yml`, environment `rubygems`. The first successful
`gem push` from the workflow turns the pending publisher into a real one and creates the gem, so there is no bootstrap. Enable
multi-factor authentication on the account first; the gemspec requires it (`rubygems_mfa_required`).

### 2.5 NuGet: environment `nuget`

1. Sign in at nuget.org. Note the exact **username** (not the email); it becomes the repository variable `NUGET_USER`:
   **Settings → Secrets and variables → Actions → Variables → New repository variable**, name `NUGET_USER`.
2. **nuget.org → profile → Trusted Publishing → Create**: policy owner (you), repository owner `nikhilvdev`, repository `logquill`,
   workflow file `release.yml`, environment `nuget`.
3. If nuget.org requires the package to exist before a policy can be attached, push `LogQuill.3.0.0-rc.1.nupkg` once with an API key
   limited to the package id `LogQuill` (**API Keys → Create**, glob pattern `LogQuill`, one day) and delete the key afterwards; that
   also makes the next tag `rc.2`.

## 3. Maven Central: environment `maven-central` (no trusted publisher)

Central has no OIDC login, so this environment holds three secrets, and the workflow uploads for a manual release in the portal.

1. Account at <https://central.sonatype.com>, then **Namespaces → Add Namespace**.
   - `io.github.nikhilvdev` is verified by creating a temporary public repository with the key the portal shows.
   - `io.logquill` is verified with a DNS TXT record on `logquill.io`, so it needs you to own that domain. If you do not, change the
     group in `bindings/jvm/build.gradle.kts`, `bindings/jvm/scripts/central-bundle.sh`, `bindings/jvm/scripts/pack-smoke.sh`, the
     JVM README and the namespace URL in `release.yml` to `io.github.nikhilvdev`. The Java package `io.logquill` does not change.
2. **Account → Generate User Token.** The portal shows a username and a password. The upload needs them combined:
   `printf '%s:%s' "<username>" "<password>" | base64`. That string is `CENTRAL_PORTAL_TOKEN`.
3. A signing key, on any machine:
   ```sh
   gpg --quick-generate-key "LogQuill release <you@example.com>" ed25519 sign 2y
   gpg --list-secret-keys --keyid-format long          # note the key id
   gpg --keyserver keyserver.ubuntu.com --send-keys <KEY_ID>
   gpg --armor --export-secret-keys <KEY_ID>           # this armoured block is GPG_PRIVATE_KEY
   ```
4. Environment secrets (**Settings → Environments → maven-central → Environment secrets**): `GPG_PRIVATE_KEY` (the armoured block),
   `GPG_PASSPHRASE`, `CENTRAL_PORTAL_TOKEN`.

The upload uses `publishingType=USER_MANAGED`, so after a tag the bundle sits in the portal under **Deployments** until you press
**Publish**. A published Maven version can never be changed or deleted.

## 4. Swift mirror: environment `swift-mirror`

1. Create the public repository `nikhilvdev/logquill-swift` (empty; no README, so the first push has no conflict).
2. **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate**: resource owner you, repository access
   **only** `nikhilvdev/logquill-swift`, permission **Contents: Read and write**, a one-year expiry.
3. Add it as the environment secret `SWIFT_MIRROR_TOKEN` on `swift-mirror`.

## 5. Everything the workflow reads

| Kind | Name | Where | For |
|---|---|---|---|
| Environment | `crates-io`, `pypi`, `npm`, `rubygems`, `nuget`, `maven-central`, `swift-mirror` | section 1 | approval and the trusted publishers |
| Repository variable | `NUGET_USER` | section 2.5 | NuGet login |
| Environment secret (`maven-central`) | `GPG_PRIVATE_KEY`, `GPG_PASSPHRASE`, `CENTRAL_PORTAL_TOKEN` | section 3 | signing and upload |
| Environment secret (`swift-mirror`) | `SWIFT_MIRROR_TOKEN` | section 4 | push to the mirror |

The Go job needs nothing: it uses the workflow's own token to push the tag `bindings/go/<version>`.

## 6. Running the first tag

1. Make sure the tree is committed, pushed, and CI is green on it.
2. If you published anything by hand (sections 2.1, 2.3, 2.5), bump the ten manifests to the next release candidate first
   (`3.0.0-rc.2`), rebuild `bindings/go/core.wasm` (`bindings/go/scripts/build-core.sh`), and run `scripts/check-versions.py`.
3. `git tag v3.0.0-rc.2 && git push origin v3.0.0-rc.2` (or `rc.1` if nothing was published by hand).
4. Open **Actions → Release**. Each publish job waits for its environment's approval; approve them one at a time and watch the
   first one fully.
5. Expect something to need a fix. The release candidate goes to npm's `next` tag, is a pre-release on GitHub, and waits in the
   Maven portal, so a failed first run costs nothing public. A version that a registry accepted can be superseded but not changed.

## 7. If something is wrong

| Symptom | Likely cause |
|---|---|
| A publish job fails with "token request failed" or "not a trusted publisher" | the workflow file name, repository owner, or the environment name differs from what was registered, character for character |
| The job never starts | the environment's tag rule does not match the tag, or "prevent self-review" is on and you are the only reviewer |
| npm: `404` or `E403` on `@logquill/...` | you are not an owner of the `logquill` organisation, or the package's first publish has not been done (section 2.3) |
| "version already exists" | that version was published by hand; bump to the next release candidate (section 6) |
| NuGet login fails | `NUGET_USER` is unset or is the email address instead of the username |
| Maven: bundle rejected in the portal | the namespace is not verified, or the group ID in the POM does not match it |
| Swift: `git push` rejected | the token lacks *Contents: write*, or the mirror repository is not empty and has a branch named differently |
