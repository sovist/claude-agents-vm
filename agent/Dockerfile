# Agent image: Claude Code on Ubuntu 24.04, running as a non-root user. A project picks its own base image
# with the AGENT_BASE_IMAGE build argument (e.g. a language SDK image built on Ubuntu noble) and adds
# build steps through project/image.sh; see docs/project-hooks.md.
ARG AGENT_BASE_IMAGE=ubuntu:24.04
FROM ${AGENT_BASE_IMAGE}

ARG USERNAME=agent
ARG USER_UID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    DISABLE_AUTOUPDATER=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       git curl ca-certificates jq ripgrep less procps tmux nano unzip zip \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu images ship an "ubuntu" user with UID 1000. Replace it with the agent user, so files
# in bind-mounted workspaces are owned by the VM's first user (UID 1000) on the VM side.
# The directories below are created here so named volumes mounted over them inherit this owner.
RUN userdel -r ubuntu 2>/dev/null || true \
    && useradd --uid ${USER_UID} --create-home --shell /bin/bash ${USERNAME} \
    && mkdir -p /workspace /home/${USERNAME}/.claude \
    && chown -R ${USERNAME}:${USERNAME} /workspace /home/${USERNAME}

# Git over HTTPS to GIT_HOST (set from config.env) authenticates with the token in /run/secrets/git-token
# (agent.sh mounts secrets/ there); the token is read on each use, never stored. Other hosts get nothing.
RUN git config --system credential.helper \
      '!f() { test "$1" = get || return 0; host=$(sed -n "s/^host=//p"); test -n "$GIT_HOST" && test "$host" = "$GIT_HOST" || return 0; echo "username=${GIT_HOST_USERNAME:-x-access-token}"; echo "password=$(cat /run/secrets/git-token)"; }; f'

USER ${USERNAME}
ENV PATH=/opt/project/bin:/home/${USERNAME}/.local/bin:${PATH} \
    CLAUDE_CONFIG_DIR=/home/${USERNAME}/.claude

# Claude Code's native installer; the image is rebuilt to update it, so the auto-updater is off.
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && claude --version

USER root

# SSH for Rider's remote development: sshd serves one connection at a time over
# "docker exec -i <agent> agent-sshd", and nothing listens on a port. The package's own host keys are
# removed; each agent's host key is mounted from the VM by agent.sh.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-server \
    && rm -rf /var/lib/apt/lists/* /etc/ssh/ssh_host_*
COPY agent_sshd_config /etc/ssh/agent_sshd_config
COPY --chmod=755 agent-sshd /usr/local/bin/agent-sshd
COPY --chmod=755 rider-idle-stop /usr/local/bin/rider-idle-stop
# Rider keeps its backend, caches and settings in these folders. agent.sh mounts a per-agent volume at
# ~/.jetbrains, and a volume shared by all agents at its RemoteDev/dist, where the backend binaries go.
RUN mkdir -p /home/${USERNAME}/.jetbrains/cache/RemoteDev/dist /home/${USERNAME}/.jetbrains/config \
             /home/${USERNAME}/.jetbrains/data /home/${USERNAME}/.cache /home/${USERNAME}/.config \
             /home/${USERNAME}/.local/share \
    && ln -s ../.jetbrains/cache /home/${USERNAME}/.cache/JetBrains \
    && ln -s ../.jetbrains/config /home/${USERNAME}/.config/JetBrains \
    && ln -s ../../.jetbrains/data /home/${USERNAME}/.local/share/JetBrains \
    && chown -R ${USERNAME}:${USERNAME} /home/${USERNAME}/.jetbrains /home/${USERNAME}/.cache \
             /home/${USERNAME}/.config /home/${USERNAME}/.local

# Environment notes every agent reads (Claude Code's organization-wide CLAUDE.md location on Linux).
COPY CLAUDE.md /etc/claude-code/CLAUDE.md
# Organization-wide settings: hooks that report each agent's state to "agents.sh list" via agent-status, and
# /run/agent-status as a working directory, so reading a prompt left there (agents.sh send) asks nothing.
COPY managed-settings.json /etc/claude-code/managed-settings.json
COPY --chmod=755 agent-status /usr/local/bin/agent-status
# Lets an agent report on a task from the developer's task board (the agents MCP server on the VM).
COPY --chmod=755 agent-task /usr/local/bin/agent-task
# Lets interactive Claude Code use the saved token instead of starting a browser sign-in.
COPY --chmod=755 agent-entrypoint /usr/local/bin/agent-entrypoint
COPY --chmod=755 atlassian /usr/local/bin/atlassian
RUN ln -s atlassian /usr/local/bin/jira && ln -s atlassian /usr/local/bin/confluence

# The project's own part (project/ in the repo, a build context of its own): its notes are appended to the
# agents' CLAUDE.md, and project/image.sh runs here as root, for packages and tools the project needs.
# compose.yaml also mounts project/ at /opt/project at run time, so its bin/ and data stay current.
COPY --from=project . /opt/project/
RUN if [ -f /opt/project/CLAUDE.md ]; then printf '\n' >> /etc/claude-code/CLAUDE.md \
        && cat /opt/project/CLAUDE.md >> /etc/claude-code/CLAUDE.md; fi \
    && if [ -f /opt/project/image.sh ]; then USERNAME=${USERNAME} bash -euo pipefail /opt/project/image.sh; fi
USER ${USERNAME}

WORKDIR /workspace
ENTRYPOINT ["agent-entrypoint"]
CMD ["bash"]
