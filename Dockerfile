# One image, three tiers (Week-10 security split):
#   [3] jarvis llm-core   127.0.0.1:8901   `python -m jarvis.serve`      (provider calls + cost)
#   [2] guarded gateway   0.0.0.0:$PORT    `python -m flat_hunter.gateway`  (guards + audit + GET /healthz)
#   [1] telegram bot      long-poll        `python -m flat_hunter bot`   (allow-list + per-user cap)
#
# The bot polls Telegram (outbound only), so nothing here needs a public inbound
# endpoint for the bot itself. The gateway binds $PORT purely so a platform health
# check (Cloud Run) has something to hit; keep it private (ingress=internal).
#
# jarvis-cli's serve/ tier is unreleased, so we install the LOCAL checkout, not git.
# Build with the PARENT directory as context so both sibling repos are visible:
#   docker build -f flat-hunter/Dockerfile -t flat-hunter-secure ~/PycharmProjects

FROM python:3.12-slim

WORKDIR /app

# Copy ONLY each project's package + build metadata — never .env, *.db, .venv, .git or
# tests. This keeps secrets structurally out of the image regardless of build context.
# Install jarvis first: flat-hunter's [ai] extra requires the `jarvis-cli` distribution,
# and installing the local checkout satisfies that name with the serve/ tier included.
COPY jarvis-cli/jarvis /app/jarvis-cli/jarvis
COPY jarvis-cli/setup.cfg jarvis-cli/setup.py /app/jarvis-cli/
COPY flat-hunter/flat_hunter /app/flat-hunter/flat_hunter
COPY flat-hunter/pyproject.toml /app/flat-hunter/
RUN pip install --no-cache-dir ./jarvis-cli \
 && pip install --no-cache-dir "./flat-hunter[ai]"

COPY flat-hunter/docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# The platform injects $PORT; the gateway binds it and answers /healthz.
ENV PORT=8900
EXPOSE 8900
ENTRYPOINT ["/app/docker-entrypoint.sh"]
