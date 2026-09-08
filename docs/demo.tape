# Terminal demo for the README. Record with `make demo` (needs vhs and GCP credentials).
Output docs/demo.gif
Set Shell "bash"
Set FontSize 15
Set Width 1200
Set Height 640
Set Padding 16
Set TypingSpeed 40ms
Set Theme "Catppuccin Mocha"
Env FF_GCP_PROJECT "filing-facts-gb"

Type "make search Q='furniture maker with no employees'"
Enter
Sleep 9s
Type "clear"
Enter
Type "uv run python -m filing_facts.mcp --ask 'Which dormant companies have called-up share capital under 10 pounds?'"
Enter
Sleep 14s
