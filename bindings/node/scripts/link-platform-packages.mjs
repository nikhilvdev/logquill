// Run by the release workflow after `napi create-npm-dirs` and `napi artifacts`: checks that every platform package under npm/
// really holds its .node file, and lists them in the main package's optionalDependencies at the main package's own version,
// so `npm install logquill` pulls exactly the one for the machine it runs on. It writes package.json in place (the workflow's
// checkout, never a committed file) and prints what it linked.
//
//   node scripts/link-platform-packages.mjs [package-dir]
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const root = resolve(process.argv[2] ?? ".");
const manifestPath = join(root, "package.json");
const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
const npmDir = join(root, "npm");
if (!existsSync(npmDir)) {
  throw new Error(`link-platform-packages: ${npmDir} does not exist; run \`napi create-npm-dirs\` first`);
}

const linked = {};
for (const dir of readdirSync(npmDir).sort()) {
  const platform = JSON.parse(readFileSync(join(npmDir, dir, "package.json"), "utf8"));
  if (!existsSync(join(npmDir, dir, platform.main))) {
    throw new Error(
      `link-platform-packages: ${platform.name} has no ${platform.main}; its build job did not upload an artifact (napi artifacts copies them in)`,
    );
  }
  if (platform.version !== manifest.version) {
    throw new Error(`link-platform-packages: ${platform.name} is ${platform.version} but ${manifest.name} is ${manifest.version}`);
  }
  linked[platform.name] = platform.version;
}
if (Object.keys(linked).length === 0) {
  throw new Error(`link-platform-packages: ${npmDir} holds no platform packages`);
}

manifest.optionalDependencies = { ...manifest.optionalDependencies, ...linked };
writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
console.log(`linked ${Object.keys(linked).length} platform packages into ${manifest.name}@${manifest.version}:\n  ${Object.keys(linked).join("\n  ")}`);
