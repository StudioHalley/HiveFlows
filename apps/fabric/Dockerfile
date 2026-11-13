FROM kayvan/fabric:latest

# Install Python 3 + pip (handles Debian and Alpine bases)
RUN if command -v apt-get > /dev/null 2>&1; then \
        apt-get update && \
        apt-get install -y --no-install-recommends python3 python3-pip && \
        rm -rf /var/lib/apt/lists/*; \
    else \
        apk add --no-cache python3 py3-pip; \
    fi

# Bake patterns & strategies into the image from the build context.
# To update patterns: re-run fabric/update-patterns.sh locally, then rebuild.
RUN mkdir -p /root/.config/fabric
COPY patterns/   /root/.config/fabric/patterns/
COPY strategies/ /root/.config/fabric/strategies/

WORKDIR /app
COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
COPY app.py .

EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
