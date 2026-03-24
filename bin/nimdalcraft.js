#!/usr/bin/env node

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const packageRoot = path.resolve(__dirname, "..");
const skillDir = path.join(packageRoot, "skills", "nimdalcraft");
const runScript = path.join(skillDir, "run.py");
const validateScript = path.join(skillDir, "scripts", "validate_starters.py");

function log(message = "") {
  process.stdout.write(`${message}\n`);
}

function fail(message, code = 1) {
  process.stderr.write(`${message}\n`);
  process.exit(code);
}

function commandExists(command, args = ["--version"]) {
  if (process.platform === "win32") {
    const where = spawnSync("where", [command], { stdio: "ignore", shell: false });
    if (where.status === 0) {
      return true;
    }
  }
  const candidates = process.platform === "win32" ? [command, `${command}.cmd`, `${command}.exe`] : [command];
  for (const candidate of candidates) {
    const result = spawnSync(candidate, args, { stdio: "ignore", shell: false });
    if (result.status === 0) {
      return true;
    }
  }
  return false;
}

function resolvePythonCommand() {
  const candidates = [
    ["python"],
    ["py", "-3"],
    ["py"]
  ];
  for (const candidate of candidates) {
    if (commandExists(candidate[0], candidate.slice(1).concat(["--version"]))) {
      return candidate;
    }
  }
  return null;
}

function slugify(text) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48) || "idea";
}

function timestamp() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return [
    now.getFullYear(),
    pad(now.getMonth() + 1),
    pad(now.getDate())
  ].join("") + "-" + [pad(now.getHours()), pad(now.getMinutes()), pad(now.getSeconds())].join("");
}

function defaultOutputDir(idea) {
  return path.join(process.cwd(), "nimdalcraft-output", `${slugify(idea)}-${timestamp()}`);
}

function codexHome() {
  return process.env.CODEX_HOME || path.join(os.homedir(), ".codex");
}

function claudeHome() {
  return process.env.CLAUDE_HOME || path.join(os.homedir(), ".claude");
}

function installCodexSkill() {
  const destination = path.join(codexHome(), "skills", "nimdalcraft");
  fs.rmSync(destination, { recursive: true, force: true });
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.cpSync(skillDir, destination, {
    recursive: true,
    force: true,
    filter: (source) => {
      const name = path.basename(source);
      return !["work", "__pycache__", "tmp-state.json", "tmp-state.out.json", "tmp-state.live.json"].includes(name);
    }
  });
  return destination;
}

function claudeCommandContents() {
  return `---
description: Turn an app idea into a nimdalcraft code retrieval plan
argument-hint: [product idea]
---

Use the nimdalcraft workflow for this idea:

$ARGUMENTS

Rules:
- Keep the normal Claude Code flow intact.
- Treat the argument text as the product idea to structure.
- Prefer a beginner-friendly SaaS MVP stack unless the user asks otherwise.
- If the nimdalcraft CLI is available, prefer running \`npx nimdalcraft "$ARGUMENTS"\` or \`nimdalcraft "$ARGUMENTS"\` and summarize the generated \`STARTER_README.md\`, \`DECISION_LOG.md\`, and \`NEXT_ACTION.md\`.
- If the CLI is not available, still apply the same nimdalcraft workflow manually: feature extraction, code retrieval, activity and credibility filtering, reconstruction plan, and runnable or handoff guidance.
- Keep the final answer focused on one startable path by default, but emphasize code-level patterns over repo branding.
`;
}

function installClaudeCommand() {
  const commandsDir = path.join(claudeHome(), "commands");
  const destination = path.join(commandsDir, "nimdalcraft.md");
  fs.mkdirSync(commandsDir, { recursive: true });
  fs.writeFileSync(destination, claudeCommandContents(), "utf8");
  return destination;
}

function runPythonScript(scriptPath, args) {
  const python = resolvePythonCommand();
  if (!python) {
    fail("Python was not found. Install Python 3 and ensure `python` or `py` is on PATH.");
  }
  const result = spawnSync(python[0], [...python.slice(1), scriptPath, ...args], {
    cwd: packageRoot,
    stdio: "inherit",
    shell: false
  });
  if (typeof result.status === "number") {
    process.exit(result.status);
  }
  process.exit(1);
}

function doctor() {
  const python = resolvePythonCommand();
  const checks = [
    ["node", process.version],
    ["npm", commandExists("npm") ? "ok" : "missing"],
    ["python", python ? python.join(" ") : "missing"],
    ["git", commandExists("git") ? "ok" : "missing"],
    ["GITHUB_TOKEN", process.env.GITHUB_TOKEN ? "set" : "missing"],
    ["CODEX_HOME", codexHome()],
    ["CLAUDE_HOME", claudeHome()],
    ["skill_dir", skillDir]
  ];
  log("Nimdalcraft Doctor");
  log("");
  for (const [label, value] of checks) {
    log(`${label}: ${value}`);
  }
}

function initCommand() {
  doctor();
  log("");
  const codexDestination = installCodexSkill();
  const claudeDestination = installClaudeCommand();
  log(`Installed Codex skill to: ${codexDestination}`);
  log(`Installed Claude Code command to: ${claudeDestination}`);
  log("");
  log("Next:");
  log("1. Run `npx nimdalcraft \"your idea\"`");
  log("2. In Codex, use `$nimdalcraft your idea`");
  log("3. In Claude Code, use `/nimdalcraft your idea`");
}

function installCommand(target) {
  if (!target || target === "all") {
    const codexDestination = installCodexSkill();
    const claudeDestination = installClaudeCommand();
    log(`Installed Codex skill to: ${codexDestination}`);
    log(`Installed Claude Code command to: ${claudeDestination}`);
    return;
  }
  if (target === "codex") {
    const destination = installCodexSkill();
    log(`Installed Codex skill to: ${destination}`);
    return;
  }
  if (target === "claude") {
    const destination = installClaudeCommand();
    log(`Installed Claude Code command to: ${destination}`);
    return;
  }
  fail("Supported targets: `codex`, `claude`, `all`.");
}

function runCommand(args) {
  if (!args.length) {
    fail("Usage: nimdalcraft run \"your idea\" [extra flags]");
  }
  let idea = "";
  const extra = [];
  if (args[0] === "--idea") {
    idea = args[1] || "";
    extra.push(...args.slice(2));
  } else {
    idea = args[0];
    extra.push(...args.slice(1));
  }
  if (!idea.trim()) {
    fail("An idea string is required.");
  }
  const hasOutputDir = extra.includes("--output-dir");
  const finalArgs = ["--idea", idea];
  if (!hasOutputDir) {
    finalArgs.push("--output-dir", defaultOutputDir(idea));
  }
  finalArgs.push(...extra);
  runPythonScript(runScript, finalArgs);
}

function validateCommand(args) {
  const hasWorkDir = args.includes("--work-dir");
  const finalArgs = args.length ? [...args] : ["--all", "--update-status"];
  if (!hasWorkDir) {
    finalArgs.push("--work-dir", path.join(process.cwd(), "nimdalcraft-output", `starter-validation-${timestamp()}`));
  }
  runPythonScript(validateScript, finalArgs);
}

function help() {
  log("nimdalcraft");
  log("");
  log("Commands:");
  log("  nimdalcraft setup");
  log("  nimdalcraft init");
  log("  nimdalcraft doctor");
  log("  nimdalcraft install codex");
  log("  nimdalcraft install claude");
  log("  nimdalcraft install all");
  log("  nimdalcraft \"your idea\"");
  log("  nimdalcraft run \"your idea\" [flags]");
  log("  nimdalcraft validate [flags]");
}

function main() {
  const [command, ...rest] = process.argv.slice(2);
  if (!command || command === "--help" || command === "-h") {
    help();
    return;
  }
  if (command === "setup" || command === "init") {
    initCommand();
    return;
  }
  if (command === "doctor") {
    doctor();
    return;
  }
  if (command === "install") {
    installCommand(rest[0]);
    return;
  }
  if (command === "run") {
    runCommand(rest);
    return;
  }
  if (command === "validate") {
    validateCommand(rest);
    return;
  }
  runCommand([command, ...rest]);
}

main();
