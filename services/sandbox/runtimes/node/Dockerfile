# syntax=docker/dockerfile:1.7
# Sandbox runtime for JavaScript and TypeScript submissions.
#
# Node 24 LTS: TypeScript is executed through Node's built-in type stripping
# (module.stripTypeScriptTypes, "transform" mode for enums/namespaces), so a
# TypeScript run costs one extra ~50 ms node process instead of a tsc compile.
# Types are not checked. python3 is only used by the job runner.
FROM node:24-trixie-slim

LABEL org.opencontainers.image.title="interview-sandbox-node" \
      interview.sandbox.runtime="node" \
      interview.sandbox.languages="javascript,typescript" \
      interview.sandbox.protocol="1"

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/tmp \
    TMPDIR=/tmp \
    NODE_ENV=production

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/* \
 && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
           /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack

COPY --chmod=0444 runner.py compile.js harness.js /opt/sandbox/

# Smoke-test the TypeScript path at build time, then lock the image down.
RUN printf 'enum C { A, B }\nfunction twoSum(n: number[], t: number): number[] { return [C.B, t]; }\n' > /tmp/s.ts \
 && printf ';globalThis.__sandboxEntry = (a) => twoSum(...a);\n' > /tmp/f.js \
 && node --experimental-vm-modules --no-warnings /opt/sandbox/compile.js ts /tmp/s.ts /tmp/m.js /tmp/f.js /opt/sandbox/harness.js \
 && echo '{"tests":[{"i":0,"args":[[1],5]}]}' | node /tmp/m.js 3>&1 | grep -q '"actual":\[1,5\]' \
 && rm -f /tmp/s.ts /tmp/f.js /tmp/m.js \
 && chmod 0755 /opt/sandbox && chmod 0444 /opt/sandbox/* \
 && groupadd --gid 10001 runner \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin runner \
 && find / -xdev -perm /6000 -type f -exec chmod a-s {} + \
 && rm -f /bin/sh /bin/bash /bin/dash /bin/rbash /usr/bin/sh /usr/bin/bash /usr/bin/dash /usr/bin/rbash

USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["/usr/bin/python3", "-I", "-B", "/opt/sandbox/runner.py"]
