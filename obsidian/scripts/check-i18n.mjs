/**
 * 词表自检。`npm test` 跑它。
 *
 * 插件没有测试框架，也不值得为这点事引一个。但语言解析有几个只在真实边界上
 * 才暴露的坑（Obsidian 选英文时是 removeItem、老版本没有 getLanguage、
 * 用户加载自定义翻译时 language 存的是一个绝对路径），必须有东西守着。
 */
import { build } from "esbuild";
import { createRequire } from "node:module";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const out = join(mkdtempSync(join(tmpdir(), "sp-i18n-")), "i18n.cjs");
await build({
  entryPoints: ["src/i18n/index.ts"],
  bundle: true,
  format: "cjs",
  external: ["obsidian"],
  outfile: out,
  logLevel: "error",
});

// 用一个可控的 obsidian 桩注入 getLanguage 的各种返回
let fakeLang;
let store = {};
const require = createRequire(import.meta.url);
const Module = require("node:module");
const origLoad = Module._load;
Module._load = (req, parent, isMain) =>
  req === "obsidian" ? { getLanguage: () => fakeLang } : origLoad(req, parent, isMain);
globalThis.window = { localStorage: { getItem: (k) => (k in store ? store[k] : null) } };
globalThis.document = { documentElement: { lang: "" } };

const warnings = [];
const origWarn = console.warn;
console.warn = (...a) => warnings.push(a.join(" "));
const i18n = require(out);
console.warn = origWarn;

const checks = [];
const check = (name, pass) => checks.push([name, pass]);

// 语言探测的边界
fakeLang = ""; store = {};
check("Obsidian 选英文（localStorage 被 removeItem）→ en", i18n.obsidianLang() === "en");
fakeLang = "zh";
check("中文 → zh", i18n.obsidianLang() === "zh");
fakeLang = "zh-TW";
check("繁体走简体表（比退回英文更近）", i18n.obsidianLang() === "zh");
fakeLang = undefined; store = { language: "zh" };
check("Obsidian < 1.8.7 没有 getLanguage → 回落 localStorage", i18n.obsidianLang() === "zh");
store = { language: "/Users/x/custom-lang.json" };
check("自定义翻译文件（language 存的是路径）不崩", i18n.obsidianLang() === "en");
store = {}; fakeLang = "";

check("coerceLangPref 非法值 → auto", i18n.coerceLangPref("bogus") === "auto");
check("coerceLangPref 保留 en", i18n.coerceLangPref("en") === "en");
check("coerceLangPref 保留 auto", i18n.coerceLangPref("auto") === "auto");

// 切表
i18n.setLang("zh");
const zhAsk = i18n.t().btnAsk;
i18n.setLang("en");
check("t() 随 setLang 换表", zhAsk === "问" && i18n.t().btnAsk === "Ask");
check("currentLang 跟着走", i18n.currentLang() === "en");

// 后端下发值的 fallback
check("chipLabel 认识的 id → 本地化", i18n.chipLabel("socratic", "先别揭晓，问我一个问题") !== "先别揭晓，问我一个问题");
check("chipLabel 未知 id → 照抄后端", i18n.chipLabel("brand_new", "后端新芯片") === "后端新芯片");
check("chipHint 未知 id → 照抄后端", i18n.chipHint("brand_new", "新提示") === "新提示");
check("phaseText 认识的 phase → 本地化", i18n.phaseText("reading", "在翻手册…") !== "在翻手册…");
check("phaseText 未知 phase → 照抄后端", i18n.phaseText("brand_new", "新状态") === "新状态");
check("phaseText 未知且无 fallback → 空串", i18n.phaseText("x", "") === "");

// 模块加载时的 arity 自检不该报警（报了说明英文表漏用了占位符）
check("中英表函数形参个数一致（无 arity 警告）", warnings.length === 0);
if (warnings.length) warnings.forEach((w) => console.error("   " + w));

let bad = 0;
for (const [name, pass] of checks) {
  if (!pass) bad++;
  console.log(`${pass ? "  ok  " : "  FAIL"} ${name}`);
}
console.log(bad ? `\n${bad}/${checks.length} 项失败` : `\n${checks.length} 项全部通过`);
process.exit(bad ? 1 : 0);
