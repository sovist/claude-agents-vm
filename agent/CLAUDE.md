# Agent container environment

You are running in an isolated Linux container on a VM, not on the developer's own machine.
Where these notes differ from project documentation, these notes describe what is actually available here.

## Network
- Only hosts on the proxy allow-list are reachable: Anthropic, and the hosts the developer added for this project.
- Any other host fails (the proxy refuses it, or the name does not resolve). Don't retry or look for a way around it;
  tell the developer which host you needed and why, so they can decide whether to allow it.
- There is no sudo and you can't install system packages.

## Git
- `origin` is reached over HTTPS; credentials are provided automatically. Don't print or copy them.
- Your commits are authored with the name and email the developer configured; don't change `user.name` or `user.email`.
- Never push to the repository's default or release branches (`main`, `master`, `develop`), and never force-push.

## Jira and Confluence
If the developer configured them, the read-only `jira` and `confluence` commands call the REST API and print JSON.
Atlassian MCP tools named in project docs are not available here; use these commands instead.

- Issue with description and comments as plain text (API v2 returns wiki markup rather than Atlassian Document Format):
  `jira '/rest/api/2/issue/KEY-123?fields=summary,description,status,comment,issuelinks,parent'`
- Search: `jira /rest/api/3/search/jql -G --data-urlencode 'jql=project = KEY AND status = "In Progress"' --data-urlencode 'fields=summary,status'`
- Confluence page: `confluence '/wiki/api/v2/pages/<id>?body-format=storage'`
- Confluence search: `confluence /wiki/rest/api/search -G --data-urlencode 'cql=text ~ "outline"'`

Pipe the output through `jq` to pick out fields. The tokens are read-only: you can't comment, create or edit.
When something should be posted to Jira or Confluence, write it out for the developer instead.
If a command says Jira or Confluence isn't configured, they aren't available here.

## Prompts and tasks from the developer
- A prompt may arrive as "Read /run/agent-status/prompt.md and do what it says": that file is the actual request.
  Read it and work from it, as if the developer had typed it.
- A task from the developer's task board ends with reporting instructions. When the work is finished, run
  `agent-task done "<one line: what you did, and the PR or branch>"`; when you are blocked or need a decision,
  run `agent-task needs-you "<what you need>"`. Report once, at the end; the developer reads it from the board.
