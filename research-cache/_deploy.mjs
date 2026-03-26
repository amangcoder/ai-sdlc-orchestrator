#!/usr/bin/env node
// Deploy script: copies research-cache server source files to ../sdlc-mcp-servers/research-cache/
import { mkdirSync, copyFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const srcDir = __dirname;
const targetDir = join(__dirname, '..', '..', 'sdlc-mcp-servers', 'research-cache');

mkdirSync(targetDir, { recursive: true });

const files = ['package.json', 'tsconfig.json', 'index.ts', 'README.md'];
for (const f of files) {
  copyFileSync(join(srcDir, f), join(targetDir, f));
  console.log(`Copied: ${f}`);
}

console.log(`\nFiles deployed to: ${targetDir}`);
console.log('Run: cd ' + targetDir + ' && npm install && npm run build');
