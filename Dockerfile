# gemini-image — MCP stdio server for Google's Gemini image models.
# Contains only the MCP server and its open-source deps (no Claude Code, no keys).
#
# Run with -i (stdio transport) and inherit the key from the environment:
#   docker run --rm -i -e GEMINI_API_KEY -v "$PWD/images:/images" tkhduracell/gemini-image:0.2.0
#
# `-e GEMINI_API_KEY` with no value is deliberate: Docker copies the variable in
# from the caller's environment, so the key never appears in an image, a manifest
# or git. Images are written to /images (IMAGE_OUTPUT_DIR); bind-mount a host
# directory there and set HOST_OUTPUT_DIR to that host path so the server can
# report paths the host can actually open.
FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/tkhduracell/claude-gemini-image-editing" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.title="gemini-image" \
      org.opencontainers.image.description="MCP stdio server for image generation and stateful editing with Google's gemini-3.1-flash-lite-image via the Interactions API."

# Dependency layer first so it caches independently of server.py changes.
COPY requirements.txt /opt/gemini-image/requirements.txt
RUN pip install --no-cache-dir -r /opt/gemini-image/requirements.txt

COPY server.py /opt/gemini-image/server.py

# Non-root so images written to a bind mount are not root-owned on the host.
# /images must exist and be owned by uid 1000 BEFORE the USER switch, otherwise
# a container run without a bind mount has nowhere writable to save to.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin gemini-image \
    && mkdir -p /images \
    && chown 1000:1000 /images

ENV PYTHONUNBUFFERED=1 \
    IMAGE_OUTPUT_DIR=/images

USER 1000
WORKDIR /images

ENTRYPOINT ["python", "/opt/gemini-image/server.py"]
