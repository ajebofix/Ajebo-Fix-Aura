"""Fail CI when Rina's inline JavaScript is invalid or its link matcher drifts.

The chat is rendered as an inline script in a Jinja template. Python unit tests
can pass while the entire browser chat fails to load on a JavaScript parse error.
Node is available on GitHub Actions runners; no npm dependencies are required.
"""

from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates/components/rina_chat.html"


def main() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?:\\s[^>]*)?>(.*?)</script>", html, flags=re.S)
    if len(scripts) != 1:
        raise SystemExit(f"Expected one Rina inline script, got {len(scripts)}")
    script = scripts[0]
    if "{{" in script or "{%" in script:
        raise SystemExit("Inline script contains unrendered Jinja syntax")

    matcher_lines = [
        line.strip()
        for line in script.splitlines()
        if "Correct or supersede context notes in the advisor editor" in line
        and line.strip().startswith("/^")
    ]
    if len(matcher_lines) != 1:
        raise SystemExit("Missing or ambiguous context editor URL matcher")
    pattern = matcher_lines[0]

    samples = (
        "const input = "
        + repr("[Correct or supersede context notes in the advisor editor]"
               "(/admin/cars/3/historical-reviews/533/context)")
        + ";\n"
        + f"const match = input.match({pattern});\n"
        + "if (!match || match[1] !== '/admin/cars/3/historical-reviews/533/context' "
        + "|| match[2] !== '3' || match[3] !== '533') "
        + "throw new Error('historical context editor link is not matched');\n"
        + "if ('[Correct or supersede context notes in the advisor editor]"
        + "(https://example.com/bad)'.match(" + pattern + ")) "
        + "throw new Error('external link was accepted');\n"
    )
    with tempfile.TemporaryDirectory(prefix="aura-rina-js-") as tmp:
        script_file = Path(tmp) / "rina_chat.js"
        script_file.write_text(script, encoding="utf-8")
        sample_file = Path(tmp) / "rina_editor_match.js"
        sample_file.write_text(samples, encoding="utf-8")
        for command in (
            ["node", "--check", str(script_file)],
            ["node", str(sample_file)],
        ):
            subprocess.run(command, check=True)
    print("Rina inline script syntax and trusted editor link matcher: PASS")


if __name__ == "__main__":
    main()
