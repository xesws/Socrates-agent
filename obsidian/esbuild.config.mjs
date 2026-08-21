import process from "node:process";

console.error(
  "Plugin source moved to ../socrates-pen.\n" +
    "Do not build from this directory — it would overwrite the vault with a stale copy.\n" +
    "  cd ../socrates-pen\n" +
    "  export VAULT_PLUGIN_DIR=/path/to/vault/.obsidian/plugins/socrates-pen\n" +
    "  npm run dev",
);
process.exit(1);
