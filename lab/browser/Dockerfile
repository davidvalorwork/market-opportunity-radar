FROM public.ecr.aws/lambda/nodejs:24@sha256:b5f6c6f20c76dd64924c4d09b1e3eadd91ea59eb3279d63eb99bc9102c61f58f AS build

# Root build context, deliberately copy only this synthetic laboratory.
WORKDIR ${LAMBDA_TASK_ROOT}/lab/browser
COPY lab/browser/package.json lab/browser/package-lock.json ./
# OpenCLI lifecycle scripts fetch unpinned adapters; disable them, use shipped CLI.
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund && npm cache clean --force
COPY lab/browser/ ./
RUN node build-assets.mjs

# Do not copy the build's /tmp tree: only completed assets enter the runtime.
FROM public.ecr.aws/lambda/nodejs:24@sha256:b5f6c6f20c76dd64924c4d09b1e3eadd91ea59eb3279d63eb99bc9102c61f58f
COPY --from=build /var/task/lab/browser/ /var/task/lab/browser/
COPY --from=build /opt/radar-chromium/ /opt/radar-chromium/
COPY --from=build /opt/fonts/ /opt/fonts/
COPY --from=build /opt/al2023/ /opt/al2023/
WORKDIR ${LAMBDA_TASK_ROOT}
ENV CI=1 PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 FONTCONFIG_PATH=/opt/fonts
ENV LD_LIBRARY_PATH=/opt/al2023/lib:/opt/radar-chromium:${LD_LIBRARY_PATH}
# AWS base image's official entrypoint selects RIE locally and Runtime API on AWS.
CMD ["lab/browser/handler.handler"]
