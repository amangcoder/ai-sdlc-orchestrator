import json, sys, re

result_file = '/Users/amangupta/.claude/projects/-Users-amangupta-Projects-orchestrator-for-mobile-ui/2be75ac3-4846-47a3-8b8f-07fa684ac8b4/tool-results/mcp-test-runner-run_single_test-1774964944800.txt'

with open(result_file) as f:
    raw = f.read()

data = json.loads(raw)
text = ''
for item in data:
    if isinstance(item, dict):
        text += item.get('text', '')

lines = text.split('\n')
print(f"Total output lines: {len(lines)}")
print()
print("=== FULL OUTPUT ===")
for line in lines:
    print(line)
