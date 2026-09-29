FROM python:3.12-slim

# Create a non-root user to run the tool.
RUN useradd --create-home --uid 10001 spray
WORKDIR /home/spray/app

COPY --chown=spray:spray . .
RUN pip install --no-cache-dir .

USER spray
ENTRYPOINT ["ultimatespray"]
CMD ["--help"]
