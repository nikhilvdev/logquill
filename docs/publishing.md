# Publishing guide — every language

How a LogQuill release reaches every registry, and what has to be set up once before the
first one. This expands section 7 of `docs/rust-core-plan.md`. Nothing is published from
`develop` yet: the workspace has `publish = false` and no release workflow exists. The
workflows below are sketches to be written in the release stage; every third-party action
must be pinned to a commit SHA (`<pin to commit SHA>`).

Items marked **verify** are claims taken from documentation or search results that were not
exercised here. Check them against the registry's current docs when you reach that step.

## 1. The packages

| # | Language | Registry | Package(s) | Built by |
|---|---|---|---|---|
| 1 | Rust | crates.io | `logquill-core`, `logquill-runtime`, `logquill-ffi`, `logquill-cli` | `cargo` |
| 2 | Python | PyPI | `logquill` (wheels + sdist) | maturin |
| 3 | Node / TypeScript | npm | `logquill`, `@logquill/native-<platform>` (one per target) | napi-rs |
| 4 | Browser, Deno, Bun, Workers | npm | `@logquill/wasm` | wasm-bindgen + tsup (`bindings/wasm`) |
| 5 | JVM, Android | Maven Central | `logquill` (JAR; AAR for Android) | Gradle + JNA over `logquill-ffi` |
| 6 | Swift | Swift Package Manager | `LogQuill` (binary XCFramework + Swift sources) | `xcodebuild` over `logquill-ffi` |
| 7 | Go | Go module proxy | `…/bindings/go/v3` | `go` |
| 8 | C# / .NET | NuGet | `LogQuill` | `dotnet pack` |
| 9 | Ruby | RubyGems | `logquill` (platform gems + source gem) | `gem build` |
| 10 | Command line | GitHub Releases, `cargo install`, wheels / npm | `logquill` binary | `cargo` |

**Names checked on 2026-10-26** (HTTP 404 = free): crates.io `logquill`, `logquill-core`,
`logquill-runtime`, `logquill-ffi`, `logquill-cli`; RubyGems `logquill`; NuGet `logquill`;
npm `@logquill/wasm`, `@logquill/cli`, `@logquill/core`. Already ours: PyPI `logquill`, npm
`logquill`. Not checkable from here: the npm scope `@logquill` (the org `logquill` already exists under your npm account), the Maven group
ID, the Go module path. Claim the free names early with a placeholder release.

## 2. One version, many spellings

One lockstep version, written in `Cargo.toml` (`[workspace.package] version`) and copied into every manifest by hand: there
is no bump script yet. `scripts/check-versions.py [vX.Y.Z]` fails if any of the ten manifests disagrees (it reads the Cargo,
PyPI, npm, RubyGems, NuGet and Gradle spellings of the same version) and, given a tag, if the tag differs; CI runs it on every
push, and a release job must run it with the tag first. Go and Swift take their version from the tag alone. A tag `vX.Y.Z`
triggers `.github/workflows/release.yml`, which builds and publishes to every registry below (Maven Central as a bundle held for a
manual release in the portal); `docs/stages/stage-9.md` lists the accounts, trusted publishers, secrets and variables it needs.
The per-registry sections say what each step does and how to undo or verify it by hand. The one-time setup outside the repository
(GitHub environments, trusted publishers, secrets) is `docs/release-setup.md`.

| Registry | Final | Release candidate | Notes |
|---|---|---|---|
| Cargo | `3.0.0` | `3.0.0-rc.1` | SemVer |
| PyPI | `3.0.0` | `3.0.0rc1` | PEP 440; maturin converts a Cargo pre-release — **verify** |
| npm | `3.0.0` | `3.0.0-rc.1` with `--tag next` | |
| Maven | `3.0.0` | `3.0.0-rc.1` | versions are immutable |
| NuGet | `3.0.0` | `3.0.0-rc.1` | |
| RubyGems | `3.0.0` | `3.0.0.rc1` | |
| Go | `v3.0.0` | `v3.0.0-rc.1` | tag `bindings/go/v3.0.0` (see §10) |
| Swift | `3.0.0` | `3.0.0-rc.1` | |

Rehearse with a real `3.0.0-rc.1` on every registry before `3.0.0`. A bad pre-release costs
nothing; a bad final version is permanent on most registries (§15).

## 3. One-time setup

1. **Accounts** with 2FA on every registry, owned by an organisation rather than one person
   where the registry allows it (crates.io owners, PyPI organisation, npm org `logquill`,
   NuGet organisation, Sonatype namespace).
2. **Repository name.** Trusted publishing on PyPI, crates.io, npm, NuGet and RubyGems is
   bound to *repository + workflow file name + environment*. Decide the final repository name
   and the file name `release.yml` **before** configuring any of them; renaming later means
   reconfiguring all of them.
3. **GitHub environments**, each with required reviewers and no secrets unless noted:
   `crates-io`, `pypi`, `npm`, `maven-central` (holds secrets), `nuget`, `rubygems`,
   `go-swift` (holds a deploy key or token for the mirror repos, if used).
4. **Licence decision** (open). It affects publishing: pkg.go.dev only shows documentation for
   packages whose licence it recognises as redistributable, so a custom source-available
   licence may hide the Go docs — **verify**. Maven, crates.io, npm, NuGet and RubyGems accept
   any declared licence. Settle the licence first.
5. **Secrets inventory** (everything else is OIDC):

| Secret | Used by | Lifetime |
|---|---|---|
| crates.io API token | the very first publish of each crate only | revoke after trusted publishing is configured |
| npm token | the first publish of a new package, if npm requires one — **verify** | revoke afterwards |
| PyPI | none (trusted publisher) | — |
| NuGet | none after trusted publishing; first version may need an API key — **verify** | revoke afterwards |
| RubyGems | none (trusted publisher; a pending publisher covers a new gem) | — |
| `MAVEN_CENTRAL_USERNAME`, `MAVEN_CENTRAL_PASSWORD` | Maven Central user token | rotate yearly |
| `GPG_PRIVATE_KEY`, `GPG_PASSPHRASE` | signing Maven artifacts | key expiry set; rotate |
| mirror-repo deploy key | Swift / Go mirrors, if used | rotate yearly |

## 4. Release flow

```
prepare (PR):  bump version everywhere → update CHANGELOG → CI green → merge
tag:           git tag vX.Y.Z && git push origin vX.Y.Z
release.yml:
  1 verify     tag == every manifest's version; refuse otherwise
  2 test       the full test matrix on the tagged commit (not a previous green run)
  3 build      one matrix job per artifact type; upload artifacts; nothing is published yet
  4 gate       a human approves in the GitHub environment
  5 publish    in this order, each job independent and re-runnable:
               crates.io → PyPI → npm (platform pkgs, wasm, main) → NuGet → RubyGems →
               Maven Central → Go tag → Swift mirror → GitHub Release (CLI binaries)
  6 verify     install every package from its registry in a clean environment
```

Rules (carried over from the Python and JS releases): the tag is the only trigger; nobody
publishes from a laptop; CI must be green on the exact tagged commit; no skipped versions —
if a job fails, fix forward on the same version for the registries that did not publish and
never reuse a version number that reached a registry.

Least privilege: workflow-level `permissions: contents: read`; only publish jobs get
`id-token: write`; build jobs have no publish rights.

## 5. Rust — crates.io

**Prerequisites in the code**
- Remove `publish = false` from `Cargo.toml` and give every path dependency a version:
  `logquill-core = { path = "../logquill-core", version = "3.0.0" }`.
- Package metadata per crate: `description`, `license` or `license-file`, `repository`,
  `readme`, `keywords`, `categories`.
- `cargo package --list -p <crate>` shows exactly what ships; `cargo publish --dry-run -p
  <crate>` must pass.

**One-time**
1. Log in with GitHub, verify the email, create an API token scoped to publishing.
2. Publish each crate's first version once with that token (trusted publishing needs the crate
   to exist).
3. On each crate's settings page add a **trusted publisher**: GitHub owner, repository,
   workflow file `release.yml`, environment `crates-io`. Then delete the API token.

**Publish** (order: `logquill-core` → `logquill-runtime` → `logquill-ffi` and `logquill-cli`)
```yaml
publish-crates:
  environment: crates-io
  permissions: { id-token: write, contents: read }
  steps:
    - uses: actions/checkout@<pin to commit SHA>
    - uses: rust-lang/crates-io-auth-action@<pin to commit SHA>   # exchanges OIDC for a short-lived token
      id: auth
    - run: |
        for crate in logquill-core logquill-runtime logquill-ffi logquill-cli; do
          cargo publish -p "$crate"
        done
      env:
        CARGO_REGISTRY_TOKEN: ${{ steps.auth.outputs.token }}
```
`cargo publish` waits for the index so the next crate can resolve its dependency. A workspace
publish command exists on recent toolchains — **verify** before relying on it. Trusted
publishing from a workspace subdirectory has failed with a "config did not match" error when
the workflow file name differed from the one registered.

**Verify:** `cargo search logquill`, the crate page, and that docs.rs built the docs.
`cargo install logquill-cli --version 3.0.0` then `logquill --version`.
**Undo:** `cargo yank --version 3.0.0 logquill-core` hides it from new resolutions (it stays
downloadable). Nothing can be deleted.

## 6. Python — PyPI

`logquill` already exists, so this is an *existing project*, not a pending publisher.

**One-time**
1. PyPI → the `logquill` project → Publishing → add a trusted publisher: owner, repository,
   workflow `release.yml`, environment `pypi`. Keep the old (2.x) publisher until the 2.x line
   is retired.
2. On TestPyPI create the same project and publisher for rehearsals.

**Build** (one job per target, all uploading to the same artifact):
- Wheels with `PyO3/maturin-action@<pin>`: `manylinux` x86_64 and aarch64, `musllinux`
  x86_64 and aarch64, macOS x86_64 and arm64, Windows x86_64 (and arm64 if a runner exists).
- Use the stable-ABI wheel (`cp310-abi3`) for 3.10+. **Free-threaded builds (3.13t, 3.14t) need
  their own non-abi3 wheels** — **verify** against the current PyO3 and maturin docs.
- One sdist: `maturin sdist`. Check the contents (allowlist, no secrets) and run `twine check`.
- Smoke test every wheel: install into a clean virtual environment and import it.

**Publish**
```yaml
publish-pypi:
  environment: pypi
  permissions: { id-token: write }
  steps:
    - uses: actions/download-artifact@<pin>
      with: { name: dist, path: dist/ }
    - uses: pypa/gh-action-pypi-publish@<pin>     # trusted publishing; generates attestations
```
**Verify:** `pip install logquill==3.0.0` in a clean venv on each OS and each Python version;
`python -c "import logquill; print(logquill.__version__)"`; the release page shows a
provenance attestation. Confirm the `logquill` console script exists (bundling the CLI
binary in the wheel is **verify**).
**Undo:** "yank" the release in the PyPI UI (it stays installable by exact pin). Files can
never be re-uploaded under the same name.

## 7. Node and TypeScript — npm

**Packages:** `logquill` (JS loader + types, `optionalDependencies` on every platform package),
`@logquill/native-<platform>` for each build target (`linux-x64-gnu`, `linux-x64-musl`,
`linux-arm64-gnu`, `linux-arm64-musl`, `darwin-x64`, `darwin-arm64`, `win32-x64-msvc`,
`win32-arm64-msvc`). The WebAssembly fallback for platforms with no native build ships inside `logquill`
itself (the `logquill.wasi-*` files), not as a separate package; `@logquill/wasm` is the browser package, section 7b.

**One-time**
1. The npm organisation `logquill` (the scope) already exists under your account; add any other owners.
2. For each package configure a **trusted publisher** (repository, workflow `release.yml`,
   environment `npm`). npm's trusted publishing was announced as generally available in July
   2025 and needs npm CLI ≥ 11.5.1; whether a package must already exist before its publisher
   can be configured — **verify**; if so, do the first publish of each new package with a
   short-lived token.
3. Scoped public packages need `--access public` (or `publishConfig.access`).

**Build:** `napi build --platform --release` per target in a matrix, uploading each `.node`
binary.

**Publish** (order matters; the napi-rs publish is *not atomic*: platform packages can reach
npm before the root package does, and a failure partway leaves them published)
1. Download all artifacts; `napi artifacts` places the binaries; `napi create-npm-dirs`
   creates the per-target package directories.
2. `napi prepublish` (check the flags of the CLI version you use) stamps the root version and
   publishes the platform packages; or publish them in a loop yourself.
3. Publish `logquill` last, so it never points at a platform package that does not exist.
4. Release candidates: `npm publish --tag next` so `latest` is untouched.
```yaml
publish-npm:
  environment: npm
  permissions: { id-token: write, contents: read }
  steps:
    - uses: actions/setup-node@<pin>
      with: { node-version: "22", registry-url: "https://registry.npmjs.org" }
    - run: npm install -g npm@latest        # needs >= 11.5.1 for OIDC
    - run: ./scripts/publish-npm.sh         # platform pkgs → main; provenance is automatic
```
**Gate before publishing:** the package-contents check from the JS repo, generalised: allowed
files only, every `exports` target present, no secret-looking files, size limits.
**Verify:** `npm view logquill version`; in a clean project on Linux, macOS and Windows,
`npm i logquill@3.0.0` then `node -e "require('logquill')"` and an ESM import; confirm the WASM
fallback by installing with `--omit=optional`.
**Undo:** `npm deprecate logquill@3.0.0 "message"`. Unpublishing is restricted to a short
window and only without dependants — **verify** the current rule.

## 7b. Browser, Deno, Bun and Workers — npm (`@logquill/wasm`)

**Package:** `@logquill/wasm`, built from `bindings/wasm` (README there). One
package, no platform variants: the `.wasm` is the same everywhere. It exports the browser SDK at `.`, the raw core at
`./core` and the module itself at `./logquill_wasm_bg.wasm`.

**One-time:** as section 7 (the `@logquill` scope, a trusted publisher for `@logquill/wasm`, `--access public`).

**Build** (the `wasm` job in `.github/workflows/ci.yml` does exactly this on Linux and macOS):
```sh
rustup target add wasm32-unknown-unknown
# the CLI must be the version bindings/wasm/Cargo.toml pins; the CI job downloads the release and checks its SHA-256
cargo install wasm-bindgen-cli --version 0.2.129 --locked
cd bindings/wasm && npm ci --ignore-scripts && npm rebuild bun
npm run build && npm run build:sdk      # pkg/ (module + glue), dist/ (SDK bundle, types, module copy)
```
**Gate before publishing:** `npm test` (the whole corpus in Node, Deno, Bun, headless Chrome and a Worker; the examples;
`npm run pack-smoke`; the size and speed budget). `pack-smoke` packs the tarball, fails on any file outside the allowed
list or an `exports` target that is missing, installs it into an empty project and uses it by name.
**Publish:** `npm publish --access public --provenance` from `bindings/wasm` once `"private": true` is removed
(release candidates with `--tag next`). Publish it before `logquill` only if `logquill` ever depends on it; it does not
today.
**Verify:** `npm view @logquill/wasm version`; in a clean directory `npm i @logquill/wasm`, then the README's Deno or
Bun example with the import left as written. For the page example, serve `node_modules/@logquill/wasm/dist` and check
the page's console shows `"password":"***"`.
**Undo:** `npm deprecate @logquill/wasm@3.0.0 "message"`; unpublishing has the limits in section 7.

The tarball carries the module once (`dist/logquill_wasm_bg.wasm`); `./core` loads it from there by default, and `pack-smoke`
fails if a second copy ships.

## 8. JVM and Android — Maven Central

**Built and verified in Stage 8 (`bindings/jvm`):** a Gradle `java-library` using **JNA over the C ABI** (`logquill-ffi`), not
UniFFI (see `docs/stages/stage-8.md`, "Deviations"). `scripts/pack-smoke.sh` builds the jar carrying the native library under
JNA's `<os>-<arch>/` resource path (`darwin-aarch64/`, `linux-x86-64/`, ...), publishes it with `maven-publish` to a throwaway
local repository, checks the jar's contents and POM, and has a fresh Gradle app depend on `io.logquill:logquill` by coordinates
and run. **Not built:** the signed Central bundle (Javadoc jar, GPG, portal upload), the per-OS native matrix (one platform is
packed locally; CI packs each runner's), the Android AAR, and a claimed group ID: `io.logquill` is a placeholder.

**One-time**
1. Create an account at the Central Portal and **register and verify a namespace**
   (`io.github.<user>` is verified by creating a temporary public repository with the code the
   portal gives you; a domain is verified with a DNS TXT record).
2. Generate a **user token** in the portal (a username/password pair, separate from your
   login). Store them as `MAVEN_CENTRAL_USERNAME` and `MAVEN_CENTRAL_PASSWORD` in the
   `maven-central` environment.
3. Create a GPG key (RSA 4096), publish the public key to a keyserver Central can query, and
   store the ASCII-armoured private key and passphrase as `GPG_PRIVATE_KEY` / `GPG_PASSPHRASE`.

**Artifacts per component:** the library JAR, a sources JAR, a Javadoc JAR (Kotlin: Dokka),
the POM with the required metadata — name, description, URL, licence, developers and SCM —
and a `.asc` signature plus checksums for each file. Without them the deployment is rejected.
Native libraries are packed into the JAR per OS and CPU (loaded at runtime); for Android build
an AAR containing `jniLibs` for `arm64-v8a`, `armeabi-v7a` and `x86_64` (typically with
`cargo-ndk`).

**Publish:** use a Gradle or Maven plugin that targets the Central Portal's publishing API
(plugin name and configuration — **verify**), upload the signed bundle, and let the portal
validate it. Choose automatic release or release by hand in the portal. Rehearse with
`publishToMavenLocal` and a portal *validation only* run, because **a published version can
never be changed or deleted**.

**Verify:** the files appear under `repo1.maven.org/maven2/<group path>/logquill/3.0.0/`
(propagation can take a while); a fresh Gradle project resolves `<group>:logquill:3.0.0` and
runs a log call on Linux, macOS and Windows.
**Undo:** there is none. Publish a fixed version and mark the broken one deprecated in the
docs and the POM description.

## 9. Swift — Swift Package Manager

**Built and verified in Stage 8 (`bindings/swift`):** a Swift package over the C ABI. `scripts/build-xcframework.sh` makes
`CLogQuill.xcframework` (with `xcodebuild -create-xcframework` when full Xcode is present, otherwise the same directory layout by
hand, which is what was exercised locally, macOS arm64 only); `scripts/smoke.sh` runs the README example and builds a fresh
package that depends on this one by path. **Not built:** the multi-platform XCFramework (the iOS and Intel slices), the zip and
checksum, the mirror repository and tag flow below, and the `xcodebuild` path itself (needs Xcode; CI's macOS runner has it).

SwiftPM resolves versions from **git tags on a repository whose root holds `Package.swift`**,
and a `binaryTarget` needs the download URL and the zip's checksum written *in that tagged
`Package.swift`*. The checksum is only known after the XCFramework is built, so the lockstep
tag cannot contain it. Two workable routes:

- **Mirror repository (recommended):** `logquill-swift` holds only `Package.swift`, the Swift
  sources and no history of its own. The release job builds the XCFramework, uploads the zip to
  a release in the mirror, writes the URL and checksum into `Package.swift`, commits, and tags
  `X.Y.Z` there.
- **Release-commit flow in the monorepo:** a "prepare release" job builds, commits the
  checksum, and only then is the tag created. More moving parts and the build must be
  byte-reproducible.

**Build:** compile the static library for `aarch64-apple-darwin`, `x86_64-apple-darwin`,
`aarch64-apple-ios`, `aarch64-apple-ios-sim`, `x86_64-apple-ios`; combine with `xcodebuild
-create-xcframework`; zip it (`zip -r -X` with the `.xcframework` at the archive root);
`swift package compute-checksum LogQuill.xcframework.zip`. The SDK layer is normal Swift over the C header
(`logquill-ffi`); there is no UniFFI-generated file. Binary targets need swift-tools-version 5.3+.
**Publish:** push the tag to the mirror. Optionally submit the repository to the Swift Package
Index. Keep the asset repository **public**: private release assets fail to resolve.
**Verify:** in a new Xcode or `swift package init` project add the URL, resolve and build for
macOS and an iOS simulator.
**Undo:** tags are the release; deleting one breaks anyone pinned to it — ship a new version
instead.

## 10. Go — module proxy

**Built and verified in Stage 8 (`bindings/go`):** the module embeds `core.wasm` (`logquill-ffi` built for `wasm32-wasip1` with no
runtime, by `scripts/build-core.sh`) and runs it with wazero, no cgo. `scripts/consumer-smoke.sh` has a fresh module require this
one by import path (through a `replace`, since nothing is published) and run. **The tagged tree must contain `core.wasm`**, so it is
committed (rebuild it with `scripts/build-core.sh` whenever the core changes; a test fails if its version is stale); a
module-proxy fetch of a tag without it fails to build. The release workflow tags `bindings/go/<version>` itself.
**Not built:** the real proxy fetch (needs a tag), the `/v3` subdirectory-tag layout check.

A Go module is just a git tag, but two rules shape it:

- **Major versions ≥ 2 need a path suffix.** The module path must end in `/v3`:
  `github.com/<owner>/<repo>/bindings/go/v3`.
- **A module in a subdirectory is tagged with the directory prefix:** `bindings/go/v3.0.0`.

The WASM core the module embeds (`core.wasm`) must be **committed in the tagged tree**, because
the proxy serves repository contents. Same chicken-and-egg as Swift: use a mirror repository
`logquill-go` (module path `github.com/<owner>/logquill-go/v3`, plain `v3.0.0` tags) written by
the release job, or a release-commit flow that commits the built `core.wasm` before tagging.

**Publish:** push the tag, then prime the proxy and pkg.go.dev:
`GOPROXY=https://proxy.golang.org go list -m <module>@v3.0.0`.
**Verify:** `go get <module>@v3.0.0` in an empty module on each OS, run a log call; check
pkg.go.dev shows the docs (needs a recognised licence — §3).
**Undo:** add a `retract v3.0.0` line to `go.mod` in a later version; the bad version stays in
the proxy cache for ever.

## 11. .NET — NuGet

**Built and verified in Stage 8 (`bindings/dotnet`):** `P/Invoke` over the C ABI (source-generated `LibraryImport`).
`scripts/pack-smoke.sh` packs `LogQuill.nupkg` (assemblies for `net8.0` and `net10.0`, the native library under
`runtimes/<rid>/native/`), checks its contents, and installs it from a local feed into a new console app that runs with
`LOGQUILL_LIB` unset. **Not built:** the other RIDs (CI packs each runner's own), Windows, signing, and a `nuget.org` push.
The tests ran on `net10.0` only (the only runtime installed); `net8.0` is compile-checked here and run in CI.

**One-time**
1. Sign in to nuget.org; optionally request a package ID prefix reservation for `LogQuill.*`.
2. Create a **trusted publishing policy**: repository owner, repository, workflow file name
   (`release.yml`, no path), optional environment `nuget`. A policy for a private repository is
   temporary for seven days until its first successful login. Whether the first version of a
   package must be pushed with an API key before a policy can cover it — **verify**.
3. Do not keep an old `NUGET_API_KEY` secret: it can interfere with the OIDC login.

**Build:** a project that packs the managed SDK and `runtimes/<rid>/native/` for
`linux-x64`, `linux-arm64`, `linux-musl-x64`, `osx-x64`, `osx-arm64`, `win-x64`, `win-arm64`;
metadata (`PackageId`, `Authors`, `Description`, `PackageLicenseExpression` or
`PackageLicenseFile`, `RepositoryUrl`, `PackageReadmeFile`); symbols as `.snupkg`.
**Publish**
```yaml
publish-nuget:
  environment: nuget
  permissions: { id-token: write }
  steps:
    - uses: NuGet/login@<pin to commit SHA>
      id: login
      with: { user: ${{ secrets.NUGET_USER }} }      # the nuget.org profile name, not an email
    - run: dotnet nuget push "*.nupkg" --api-key "${{ steps.login.outputs.NUGET_API_KEY }}" --source https://api.nuget.org/v3/index.json --skip-duplicate
```
A staging site (`int.nugettest.org`) exists for rehearsals — **verify**.
**Verify:** `dotnet add package LogQuill --version 3.0.0` in a new console project on each OS.
**Undo:** unlist the version in the UI (it stays restorable and resolvable by exact version).

## 12. Ruby — RubyGems

**Built and verified in Stage 8 (`bindings/ruby`):** the `ffi` gem over the C ABI. `scripts/pack_smoke.rb` builds a **platform
gem** carrying the release library (`LOGQUILL_PLATFORM` set, e.g. `arm64-darwin-27` as `Gem::Platform.local` spells it here;
publish with the generic `arm64-darwin` / `x86_64-linux` names), checks its contents, installs it into an empty gem home and
uses it by name with `LOGQUILL_LIB` unset. **Not built:** the platform matrix, Windows, the source gem's behaviour on a
machine with no library (it raises with every place it looked), and a RubyGems push.

The binding uses the `ffi` gem over `logquill-ffi`, so no compiler is needed on the user's
machine: ship **platform gems** each containing the prebuilt shared library, plus a pure-Ruby
fallback gem that raises an actionable error on an unsupported platform.

**One-time:** create the account with MFA; add a **pending trusted publisher** for the new gem
name (repository, workflow `release.yml`, environment `rubygems`); the first successful push
converts it to a normal trusted publisher and makes you an owner. Set
`rubygems_mfa_required` in the gemspec metadata.
**Build:** one `.gem` per platform (`x86_64-linux`, `aarch64-linux`, `arm64-darwin`,
`x86_64-darwin`, `x64-mingw-ucrt`, and a musl variant — **verify** the exact platform strings)
by setting `spec.platform` and copying the matching library into `lib/`.
**Publish:** `rubygems/release-gem@<pin>` handles one gem; for several platform gems use
`rubygems/configure-rubygems-credentials@<pin>` and then `gem push` each file.
**Verify:** `gem install logquill -v 3.0.0` on each OS, then `ruby -e 'require "logquill"'`.
**Undo:** `gem yank logquill -v 3.0.0` (per platform with `--platform`).

## 13. Command-line binary

The `logquill` binary ships in four ways: `cargo install logquill-cli`; GitHub Release assets
per target (`logquill-<version>-<target>.tar.gz`, `.zip` on Windows) with a `SHA256SUMS` file and
a build-provenance attestation; inside the Python wheel and the npm package (so `logquill tail`
works after `pip install` / `npm i`); optionally a Homebrew tap. Release binaries are built in
the same matrix as the libraries and attached to a **draft** release that is published last.

## 14. Verification checklist (after every release)

| Check | How |
|---|---|
| Versions agree | every registry page shows `X.Y.Z` |
| Clean install works | one throwaway project per language × OS (Linux, macOS, Windows) |
| Smoke run | log a record, flush, read it back with `logquill verify` and `tail` |
| Fixtures | each package's installed copy passes `schema/golden_records.json` |
| Provenance | PyPI attestation, npm provenance, release-binary attestation present |
| Docs | docs.rs built; pkg.go.dev shows the module; READMEs render |
| CLI | `logquill --version` from `cargo install`, the wheel, npm and the release asset |

## 15. When something goes wrong

| Registry | Undo | Can a version be replaced? |
|---|---|---|
| crates.io | `cargo yank` | no |
| PyPI | yank in the UI | no (file names are permanent) |
| npm | `npm deprecate`; unpublish only within a short window | no |
| Maven Central | none | no |
| NuGet | unlist | no |
| RubyGems | `gem yank` | no |
| Go | `retract` in a later version | no (proxy caches) |
| Swift | ship a new version | deleting a tag breaks pinned users |

Partial release: re-run only the failed publish jobs for the same tag; every publish job must
be safe to re-run (skip a version that already exists: `--skip-duplicate` for NuGet, check
`npm view` and `cargo search` first, skip-existing behaviour for PyPI). If a registry already
has the version but its content is wrong, release the next patch version everywhere and yank or
deprecate the bad one; never try to overwrite.

## 16. Rehearsal plan

1. Dry runs on every push to `develop`: `cargo publish --dry-run`, `maturin build`, `npm pack`,
   `dotnet pack`, `gem build`, `./gradlew publishToMavenLocal`, XCFramework build — build and
   inspect, publish nothing.
2. Publish `3.0.0-rc.1` to TestPyPI and the other staging sites that exist; install from them.
3. Publish `3.0.0-rc.1` for real, with `next` / pre-release tags, and install it everywhere.
4. Only then tag `3.0.0`.
