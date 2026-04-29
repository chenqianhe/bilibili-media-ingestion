import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const replacements = [
  ['from "zod"', 'from "zod/v3"'],
  ["from 'zod'", "from 'zod/v3'"],
  ['require("zod")', 'require("zod/v3")'],
  ["require('zod')", "require('zod/v3')"],
]

const targets = [
  "node_modules/@tanstack/router-generator/dist/esm/config.js",
  "node_modules/@tanstack/router-generator/dist/esm/filesystem/virtual/config.js",
  "node_modules/@tanstack/router-generator/dist/cjs/config.cjs",
  "node_modules/@tanstack/router-generator/dist/cjs/filesystem/virtual/config.cjs",
  "node_modules/@tanstack/router-plugin/dist/esm/core/config.js",
  "node_modules/@tanstack/router-plugin/dist/cjs/core/config.cjs",
]

let patchedFiles = 0
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")

for (const relativePath of targets) {
  const filePath = path.resolve(repoRoot, relativePath)

  if (!existsSync(filePath)) {
    continue
  }

  const original = readFileSync(filePath, "utf8")
  let patched = original

  for (const [searchValue, replaceValue] of replacements) {
    patched = patched.replace(searchValue, replaceValue)
  }

  if (patched === original) {
    continue
  }

  writeFileSync(filePath, patched)
  patchedFiles += 1
  console.log(`patched ${relativePath}`)
}

if (patchedFiles === 0) {
  console.log("tanstack zod compatibility patch already applied or packages missing")
}
