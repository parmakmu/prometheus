import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

// File and folder icons as VS Code's Material Icon Theme draws them (material-icon-theme, MIT), served from the
// package: GET /file-icon?name=<file name> (folder=1 for a folder, open=1 for an open one). The page asks by name
// and the theme's own manifest picks the icon, so its hundreds of types never reach the browser as code.
type Manifest = {
  iconDefinitions: Record<string, { iconPath: string }>;
  fileExtensions: Record<string, string>;
  fileNames: Record<string, string>;
  languageIds: Record<string, string>;
  folderNames: Record<string, string>;
  folderNamesExpanded: Record<string, string>;
  file: string;
  folder: string;
  folderExpanded: string;
};

// extensions VS Code knows by language rather than by extension in the manifest
const LANGUAGES: Record<string, string> = { ts: "typescript", tsx: "typescriptreact", js: "javascript", mjs: "javascript", jsx: "javascriptreact", py: "python", sh: "shellscript", css: "css", sql: "sql", xml: "xml" };

let loaded: { root: string; manifest: Manifest } | null = null;
async function theme() {
  if (!loaded) {
    const root = path.dirname(createRequire(path.join(process.cwd(), "package.json")).resolve("material-icon-theme/package.json"));
    loaded = { root, manifest: JSON.parse(await readFile(path.join(root, "dist", "material-icons.json"), "utf8")) };
  }
  return loaded;
}

function iconFor(m: Manifest, name: string, folder: boolean, open: boolean) {
  const lower = name.toLowerCase();
  if (folder) return (open ? m.folderNamesExpanded[lower] : m.folderNames[lower]) ?? (open ? m.folderExpanded : m.folder);
  if (m.fileNames[lower]) return m.fileNames[lower];
  const parts = lower.split(".");
  for (let i = 1; i < parts.length; i++) {
    const ext = parts.slice(i).join("."); // longest first: "d.ts" before "ts"
    if (m.fileExtensions[ext]) return m.fileExtensions[ext];
    const language = LANGUAGES[ext] ?? ext;
    if (i === parts.length - 1 && m.languageIds[language]) return m.languageIds[language];
  }
  return m.file;
}

export async function GET(req: Request) {
  const q = new URL(req.url).searchParams;
  const { root, manifest } = await theme();
  const icon = iconFor(manifest, path.basename(q.get("name") ?? ""), q.get("folder") === "1", q.get("open") === "1");
  const file = path.resolve(path.dirname(path.join(root, "dist", "material-icons.json")), manifest.iconDefinitions[icon]?.iconPath ?? "");
  if (!file.startsWith(path.join(root, "icons") + path.sep)) return new Response("no icon", { status: 404 });
  return new Response(await readFile(file), { headers: { "Content-Type": "image/svg+xml", "Cache-Control": "public, max-age=86400" } });
}
