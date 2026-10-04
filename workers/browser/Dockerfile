FROM public.ecr.aws/lambda/nodejs:24@sha256:b5f6c6f20c76dd64924c4d09b1e3eadd91ea59eb3279d63eb99bc9102c61f58f AS build
WORKDIR ${LAMBDA_TASK_ROOT}/workers/browser
COPY workers/browser/package.json workers/browser/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund && npm cache clean --force
COPY workers/browser/ ./
RUN node build-assets.mjs

FROM public.ecr.aws/lambda/nodejs:24@sha256:b5f6c6f20c76dd64924c4d09b1e3eadd91ea59eb3279d63eb99bc9102c61f58f
COPY --from=build /var/task/workers/browser/ /var/task/workers/browser/
COPY --from=build /opt/radar-chromium/ /opt/radar-chromium/
COPY --from=build /opt/fonts/ /opt/fonts/
COPY --from=build /opt/al2023/ /opt/al2023/
COPY src/radar/schemas/ /var/task/src/radar/schemas/
COPY contracts/examples/ /var/task/contracts/examples/
WORKDIR ${LAMBDA_TASK_ROOT}
ENV CI=1 PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 FONTCONFIG_PATH=/opt/fonts
ENV LD_LIBRARY_PATH=/opt/al2023/lib:/opt/radar-chromium:${LD_LIBRARY_PATH}
CMD ["workers/browser/handler.handler"]
