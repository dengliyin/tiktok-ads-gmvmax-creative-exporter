const fs = require('fs');
const os = require('os');
const path = require('path');

const projectRoot = path.resolve(__dirname, '..');

function loadConfig() {
  const configPath = path.join(projectRoot, 'config.json');
  const examplePath = path.join(projectRoot, 'config.example.json');

  if (!fs.existsSync(configPath)) {
    throw new Error(`Cannot find config.json. Copy ${examplePath} to ${configPath} first.`);
  }

  return JSON.parse(fs.readFileSync(configPath, 'utf8'));
}

function resolveFromProject(value) {
  if (!value) return projectRoot;
  const expanded = expandHome(value);
  return path.isAbsolute(expanded) ? expanded : path.resolve(projectRoot, expanded);
}

function expandHome(value) {
  if (typeof value !== 'string') return value;
  if (value === '~') return os.homedir();
  if (value.startsWith(`~${path.sep}`) || value.startsWith('~/')) {
    return path.join(os.homedir(), value.slice(2));
  }
  return value;
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function yesterday() {
  const date = new Date();
  date.setDate(date.getDate() - 1);
  date.setHours(0, 0, 0, 0);
  return date;
}

function formatDate(date, format = 'yyyy-MM-dd') {
  const yyyy = String(date.getFullYear());
  const MM = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');

  return format.replace('yyyy', yyyy).replace('MM', MM).replace('dd', dd);
}

function safeFilenamePart(value) {
  return String(value)
    .replace(/[\\/:*?"<>|]/g, '-')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 90);
}

function timestampForFilename(date = new Date()) {
  const HH = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  const ss = String(date.getSeconds()).padStart(2, '0');
  return `${HH}${mm}${ss}`;
}

module.exports = {
  projectRoot,
  loadConfig,
  resolveFromProject,
  ensureDir,
  yesterday,
  formatDate,
  safeFilenamePart,
  timestampForFilename
};
