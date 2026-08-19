import { App, FileSystemAdapter, MarkdownView, TFile } from "obsidian";

export type EditorPick = {
  text: string;
  startLine: number;
  endLine: number;
  file: TFile;
  absPath: string;
};

export function vaultRoot(app: App): string {
  const ad = app.vault.adapter;
  if (ad instanceof FileSystemAdapter) return ad.getBasePath();
  throw new Error("需要桌面库（FileSystemAdapter）");
}

export function handbookIdFromPath(absPath: string): string {
  let h = 0;
  for (let i = 0; i < absPath.length; i++) {
    h = (Math.imul(h, 31) + absPath.charCodeAt(i)) >>> 0;
  }
  const stem = absPath.split("/").pop()?.replace(/\.md$/i, "") || "note";
  const slug =
    stem
      .toLowerCase()
      .replace(/[^a-z0-9._-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "note";
  const hex = h.toString(16);
  const short = slug.slice(0, 48).replace(/-+$/g, "") || "note";
  return `${short}-${hex}`;
}

export function readEditorPick(app: App): EditorPick | null {
  const view = app.workspace.getActiveViewOfType(MarkdownView);
  if (!view?.file) return null;
  const editor = view.editor;
  const text = (editor.getSelection() || "").replace(/\s+/g, " ").trim();
  if (text.length < 4) return null;
  const from = editor.getCursor("from");
  const to = editor.getCursor("to");
  const absPath = `${vaultRoot(app)}/${view.file.path}`;
  return {
    text,
    startLine: from.line + 1,
    endLine: to.line + 1,
    file: view.file,
    absPath,
  };
}
