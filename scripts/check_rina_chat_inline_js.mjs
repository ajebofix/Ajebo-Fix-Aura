// Browser parsing guard for Aura's Jinja inline Rina chat script.
// This catches syntax errors that Python unit tests cannot see.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Script, runInNewContext } from "node:vm";

const html = readFileSync("templates/components/rina_chat.html", "utf8");
const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)];
assert.equal(scripts.length, 1, "Expected exactly one inline Rina chat script");
const source = scripts[0][1];
assert.ok(!source.includes("{{") && !source.includes("{%"), "Unrendered Jinja in JS");
new Script(source, { filename: "rina_chat_inline.js" });

const patternLines = source.split("\n")
  .map((line) => line.trim())
  .filter((line) => line.startsWith("/^")
    && line.includes("Correct or supersede context notes in the advisor editor"));
assert.equal(patternLines.length, 1, "Missing editor link matcher");
const matcher = runInNewContext(patternLines[0], {}, { timeout: 1000 });
const value = "[Correct or supersede context notes in the advisor editor]"
  + "(/admin/cars/3/historical-reviews/533/context)";
const match = value.match(matcher);
assert.ok(match, "Trusted editor link must match");
assert.equal(match[1], "/admin/cars/3/historical-reviews/533/context");
assert.equal(match[2], "3");
assert.equal(match[3], "533");
assert.equal(("[Correct or supersede context notes in the advisor editor]"
  + "(https://example.com/bad)").match(matcher), null);
assert.equal(("[Correct or supersede context notes in the advisor editor]"
  + "(/admin/cars/3/historical-reviews/533/context?next=evil)").match(matcher), null);
console.log("Rina chat inline JavaScript and editor link: PASS");
