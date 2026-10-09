# syntax=docker/dockerfile:1.7
# Sandbox runtime for C++ submissions (GCC 14, -std=c++17 -O2).
#
# sandbox_prelude.hpp (<bits/stdc++.h> + harness declarations/templates) is
# precompiled with exactly the flags the sandbox uses and the non-template
# harness code is prebuilt into sandbox_harness.o, cutting a typical compile
# from ~0.95 s (plain) / ~1.1 s (with an inline harness) to ~0.4 s (measured).
# The build fails if GCC would not use the PCH (-Werror=invalid-pch).
FROM debian:trixie-slim

LABEL org.opencontainers.image.title="interview-sandbox-cpp" \
      interview.sandbox.runtime="cpp" \
      interview.sandbox.languages="cpp" \
      interview.sandbox.protocol="1"

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/tmp \
    TMPDIR=/tmp

RUN apt-get update \
 && apt-get install -y --no-install-recommends g++ libc6-dev python3 \
 && rm -rf /var/lib/apt/lists/*

COPY --chmod=0444 runner.py /opt/sandbox/runner.py
COPY sandbox_prelude.hpp sandbox_harness.cpp /opt/sandbox/cpp/

# CXXFLAGS must stay identical to CXX_FLAGS in sandbox_service/drivers/cpp.py.
ARG CXXFLAGS="-std=c++17 -O2 -pipe -fmax-errors=20 -fdiagnostics-color=never"
RUN set -eux; \
    cd /opt/sandbox/cpp; \
    g++ ${CXXFLAGS} -x c++-header sandbox_prelude.hpp -o sandbox_prelude.hpp.gch; \
    g++ ${CXXFLAGS} -Werror=invalid-pch -Winvalid-pch -c sandbox_harness.cpp -o sandbox_harness.o; \
    mkdir -p /tmp/smoke && cd /tmp/smoke; \
    printf 'class Solution { public: vector<int> twoSum(vector<int>& a, int t) { return {(int)a.size(), t}; } };\nsigned main() { sbx::Harness h; if (!h.load()) return 1; h.ready(); for (const auto& t : h.tests) { h.begin(t); auto a0 = sbx::from_json<std::vector<int>>(t.args[0]); auto a1 = sbx::from_json<int>(t.args[1]); auto s = std::make_unique<Solution>(); h.result(t, sbx::to_json(s->twoSum(a0, a1)), 0.0); } h.done(); }\n' > main.cpp; \
    g++ ${CXXFLAGS} -Werror=invalid-pch -Winvalid-pch -I/opt/sandbox/cpp -include sandbox_prelude.hpp -o prog main.cpp /opt/sandbox/cpp/sandbox_harness.o; \
    echo '{"tests":[{"i":0,"args":[[1,2],5]}]}' | ./prog 3>&1 | grep -q '"actual":\[2,5\]'; \
    cd / && rm -rf /tmp/smoke; \
    chmod -R a+rX /opt/sandbox

RUN groupadd --gid 10001 runner \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin runner \
 && find / -xdev -perm /6000 -type f -exec chmod a-s {} + \
 && rm -f /bin/sh /bin/bash /bin/dash /bin/rbash /usr/bin/sh /usr/bin/bash /usr/bin/dash /usr/bin/rbash

USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["/usr/bin/python3", "-I", "-B", "/opt/sandbox/runner.py"]
