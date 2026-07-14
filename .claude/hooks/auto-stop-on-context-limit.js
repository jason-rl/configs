#!/usr/bin/env node
'use strict';

// Auto-stop hook: halts the session when auto-compaction is disabled
// (autoCompactEnabled: false) and measured context reaches
// CLAUDE_CODE_AUTO_COMPACT_WINDOW tokens. Without auto-compact, nothing
// summarizes the transcript as it grows, so a long session can silently
// balloon toward the model's hard context ceiling and error out mid-turn.
// This is a safety net, not a tool call blocker: it must never throw and
// must never halt spuriously, so every step below fails open (no output,
// exit 0) on any error or ambiguity.
//
// Registered on PostToolUse and UserPromptSubmit in settings.json.

const fs = require('fs');
const os = require('os');
const path = require('path');

const DEFAULT_WINDOW = 200000; // documented Claude Code default

function readStdin() {
  try {
    return fs.readFileSync(0, 'utf8');
  } catch {
    return '';
  }
}

// Tolerant extraction of a top-level boolean field from a settings.json-like
// file. Deliberately not a full JSON parse: some of these files carry
// trailing commas / comments in practice, and a strict parse failure would
// silently disable the whole check. Last match wins (mirrors JSON's
// last-key-wins on duplicate keys).
function extractBoolField(text, field) {
  const re = new RegExp(`"${field}"\\s*:\\s*(true|false)`, 'g');
  let match;
  let last = null;
  while ((match = re.exec(text)) !== null) {
    last = match[1] === 'true';
  }
  return last;
}

function readAutoCompactEnabled(cwd) {
  const candidates = [
    path.join(os.homedir(), '.claude', 'settings.json'),
    cwd ? path.join(cwd, '.claude', 'settings.json') : null,
    cwd ? path.join(cwd, '.claude', 'settings.local.json') : null,
  ].filter(Boolean);

  // Precedence low -> high: later files override earlier ones, matching
  // Claude Code's own settings precedence (user < project < project-local).
  let effective = true; // native default when unset anywhere
  for (const file of candidates) {
    try {
      const text = fs.readFileSync(file, 'utf8');
      const value = extractBoolField(text, 'autoCompactEnabled');
      if (value !== null) effective = value;
    } catch {
      // file missing/unreadable: ignore, keep prior value
    }
  }
  return effective;
}

function readThreshold() {
  const raw = process.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW;
  const parsed = parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_WINDOW;
}

// Sum usage from the most recent completed, non-subagent assistant message
// in the transcript JSONL. Returns null if none found / unreadable.
function readCurrentContextTokens(transcriptPath) {
  if (!transcriptPath) return null;
  let text;
  try {
    text = fs.readFileSync(transcriptPath, 'utf8');
  } catch {
    return null;
  }

  const lines = text.split('\n');
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (!line) continue;
    let entry;
    try {
      entry = JSON.parse(line);
    } catch {
      continue;
    }
    if (entry.isSidechain) continue; // skip subagent turns
    const msg = entry.message;
    const isAssistant = entry.type === 'assistant' || (msg && msg.role === 'assistant');
    if (!isAssistant || !msg || !msg.usage) continue;

    const u = msg.usage;
    const total =
      (u.input_tokens || 0) +
      (u.cache_read_input_tokens || 0) +
      (u.cache_creation_input_tokens || 0) +
      (u.output_tokens || 0);
    if (total > 0) return total;
    // usage present but all-zero: keep scanning further back for a real one
  }
  return null;
}

function formatNumber(n) {
  return n.toLocaleString('en-US');
}

function main() {
  const raw = readStdin();
  if (!raw) return; // nothing to do

  let input;
  try {
    input = JSON.parse(raw);
  } catch {
    return;
  }

  const cwd = input.cwd;
  const transcriptPath = input.transcript_path;
  const hookEvent = input.hook_event_name;

  if (readAutoCompactEnabled(cwd) !== false) return; // only act when explicitly disabled

  const threshold = readThreshold();
  const currentTokens = readCurrentContextTokens(transcriptPath);
  if (currentTokens === null) return;
  if (currentTokens < threshold) return;

  const reason =
    `🛑 Context ≈${formatNumber(currentTokens)} tokens ≥ ` +
    `CLAUDE_CODE_AUTO_COMPACT_WINDOW (${formatNumber(threshold)}) and autoCompactEnabled ` +
    `is false — session halted to avoid overflow. Run /compact or /clear to continue.`;

  const output = {
    continue: false,
    stopReason: reason,
  };
  if (hookEvent === 'UserPromptSubmit') {
    output.decision = 'block';
    output.reason = reason;
  }

  process.stdout.write(JSON.stringify(output));
}

try {
  main();
} catch {
  // fail open: never let this hook break the session
}
