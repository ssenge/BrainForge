# Test plan

A step-by-step test of every BrainForge feature for a human tester who has never seen it: setup on Mac, Windows (WSL)
or Linux, the command line, Claude Code and Claude Desktop through the skill and MCP, Obsidian, GitHub Actions, and a
team of two. About six hours.

Download [brainforge-test-plan.html](brainforge-test-plan.html) and open it in a browser. Mark each test Pass, Fail,
Blocked or Skip; results stay in that browser, and **Copy results as text** gives you a report to send.

The tests live in `content.py`. After changing them, rebuild the page:

```bash
python3 testplan/gen.py --standalone > testplan/brainforge-test-plan.html
```
