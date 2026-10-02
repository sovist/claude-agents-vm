---
description: Set up claude-agents-vm step by step, from a new VM to the first agent and the MCP server
---

Set up claude-agents-vm for the person you're working with, following the setup checklist in CLAUDE.md.

1. Read CLAUDE.md (roles and the checklist) and README.md. Ask which machine they're on (Windows, macOS, Linux),
   which hypervisor they'll use, the SSH alias and VM user they want (default `agents`), and which repository the
   agents should work on.
2. Find out what's already done, using the checks listed with each step, and tell them where they are.
3. Go through the remaining steps one at a time. For a step only they can do, give the exact command or
   setting from the matching doc (docs/vm-setup.md, docs/ssh-setup.md, docs/usage.md), wait until they say it's
   done, then run the step's check before moving on. For the other steps, do them over SSH and show the result.
4. Never ask for, read, or print a token. Tokens are saved by them with save-secret.sh at its own prompt.
5. Finish with a working round trip: agent-1 is `ready`, the MCP server shows as connected, and an agent answered
   a small read-only request. Then summarize what's set up, where things are, and the optional extras (Rider,
   notifications, queue mode).

$ARGUMENTS
