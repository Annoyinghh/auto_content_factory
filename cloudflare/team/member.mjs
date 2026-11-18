import { spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const [action, input, ...args] = process.argv.slice(2);
const name = input?.trim();
let options;
try {
  ({ values: options } = parseArgs({ args, options: {
    remote: { type: "boolean" },
    config: { type: "string" },
  } }));
} catch {
  // Use the same usage message for invalid options and invalid member names.
}
if (!["create", "revoke"].includes(action) || !name || name.length > 80 || /[\p{Cc}\p{Cs}]/u.test(name) ||
    !options || options.config === "") {
  console.error("Usage: node member.mjs create|revoke NAME [--remote] [--config PATH]");
  process.exit(2);
}

const directory = fileURLToPath(new URL(".", import.meta.url));
const config = options.config
  ? resolve(options.config)
  : join(directory, options.remote ? "wrangler.production.jsonc" : "wrangler.jsonc");
const label = "'" + name.replaceAll("'", "''") + "'";
const token = action === "create" ? randomBytes(32).toString("base64url") : null;
const digest = token ? createHash("sha256").update(token).digest("hex") : null;
const statement = token
  ? `INSERT INTO members(name, token_hash) VALUES (${label}, '${digest}')
     ON CONFLICT(name) DO UPDATE SET token_hash = excluded.token_hash WHERE members.token_hash IS NULL
     RETURNING name;`
  : `UPDATE members SET token_hash = NULL WHERE name = ${label} AND token_hash IS NOT NULL RETURNING name;`;
try {
  const result = spawnSync(process.execPath, [
    join(directory, "node_modules", "wrangler", "bin", "wrangler.js"),
    "d1", "execute", "DB", options.remote ? "--remote" : "--local", "--config", config,
    "--command", statement, "--json",
  ], { cwd: dirname(config), encoding: "utf8" });
  if (result.error || result.status !== 0) {
    throw new Error("Wrangler could not update the member. Check the database and Cloudflare login.");
  }
  if (JSON.parse(result.stdout)[0]?.results?.[0]?.name !== name) {
    throw new Error(token ? "This member already has an active code." : "This member has no active code.");
  }
  console.log(token || "Member access revoked.");
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
