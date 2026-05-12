import { execFileSync } from "node:child_process"
import { readFileSync } from "node:fs"
import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import { tanstackRouter } from "@tanstack/router-plugin/vite"
import react from "@vitejs/plugin-react-swc"
import { defineConfig } from "vite"

const packageJson = JSON.parse(
  readFileSync(new URL("./package.json", import.meta.url), "utf-8"),
) as { version?: string }

function readGitValue(args: string[]) {
  try {
    return execFileSync("git", args, {
      cwd: path.resolve(__dirname, ".."),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      timeout: 2000,
    }).trim()
  } catch {
    return ""
  }
}

// https://vitejs.dev/config/
export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  define: {
    __APP_VERSION__: JSON.stringify(
      process.env.VITE_APP_VERSION || packageJson.version || "unknown",
    ),
    __GIT_COMMIT__: JSON.stringify(
      process.env.VITE_GIT_COMMIT || readGitValue(["rev-parse", "HEAD"]),
    ),
    __GIT_BRANCH__: JSON.stringify(
      process.env.VITE_GIT_BRANCH ||
        readGitValue(["rev-parse", "--abbrev-ref", "HEAD"]),
    ),
    __BUILD_TIME__: JSON.stringify(
      process.env.VITE_BUILD_TIME || new Date().toISOString(),
    ),
  },
  plugins: [
    tanstackRouter({
      target: "react",
      autoCodeSplitting: true,
    }),
    react(),
    tailwindcss(),
  ],
})
