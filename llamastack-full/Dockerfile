FROM quay.io/savitaashture/distribution-starter:llamastack

# Only install necessary Python packages
RUN pip install --no-cache-dir transformers

# Copy custom FAISS-only config
COPY config.yaml /app/config.yaml

# Expose server port
EXPOSE 8321

# Start the server using the copied config
CMD ["--config", "/app/config.yaml"]

