# Context MUST be a new directory containing only this compiled Linux binary.
# No repository, source snapshots, configuration, credentials or session blobs.
FROM scratch
COPY --chmod=0555 whatsapp-session-check /whatsapp-session-check
USER 65532:65532
ENV TMPDIR=/tmp/private
ENTRYPOINT ["/whatsapp-session-check"]
CMD ["--session-dir", "/input"]
