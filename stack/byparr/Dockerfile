FROM ghcr.io/thephaseless/byparr:latest

# Byparr accepts a `cookies` parameter but never applies it. Cookie-auth
# trackers need it, so patch the handler at build time. See the script header.
USER root
COPY add_cookie_support.py /tmp/add_cookie_support.py
RUN /app/.venv/bin/python /tmp/add_cookie_support.py && rm /tmp/add_cookie_support.py
USER 1000
