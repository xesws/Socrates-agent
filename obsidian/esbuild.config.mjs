import esbuild from "esbuild";
import process from "node:process";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const prod = process.argv[2] === "production";
const outdir =
  process.env.VAULT_PLUGIN_DIR ||
  "/Users/tangyiq/dev/socrates-pen-vault/.obsidian/plugins/socrates-pen";

mkdirSync(outdir, { recursive: true });

function copyStatics() {
  copyFileSync(join(root, "manifest.json"), join(outdir, "manifest.json"));
  copyFileSync(join(root, "styles.css"), join(outdir, "styles.css"));
  writeFileSync(join(outdir, ".hotreload"), "");
}

const context = await esbuild.context({
  entryPoints: [join(root, "src/main.ts")],
  bundle: true,
  external: [
    "obsidian",
    "electron",
    "@codemirror/autocomplete",
    "@codemirror/collab",
    "@codemirror/commands",
    "@codemirror/language",
    "@codemirror/lint",
    "@codemirror/search",
    "@codemirror/state",
    "@codemirror/view",
    "@lezer/common",
    "@lezer/highlight",
    "@lezer/lr",
  ],
  format: "cjs",
  target: "es2018",
  logLevel: "info",
  sourcemap: prod ? false : "inline",
  treeShaking: true,
  outfile: join(outdir, "main.js"),
});

copyStatics();
if (prod) {
  await context.rebuild();
  await context.dispose();
} else {
  await context.watch();
}
