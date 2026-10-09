# syntax=docker/dockerfile:1.7
# Sandbox runtime for Java submissions (Temurin JDK 25 LTS).
#
# Startup tuning, measured on the DGX Spark (1 CPU quota):
#   * javac with C1 only + SerialGC + a JDK 25 AOT cache trained on typical
#     sources: ~0.33 s per compile instead of ~0.8 s.
#   * The test harness and run-mode launcher are precompiled here, so only the
#     candidate's class is compiled per execution; methods are invoked by
#     reflection with JSON arguments converted to the declared parameter types.
FROM eclipse-temurin:25-jdk-noble

LABEL org.opencontainers.image.title="interview-sandbox-java" \
      interview.sandbox.runtime="java" \
      interview.sandbox.languages="java" \
      interview.sandbox.protocol="1"

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/tmp \
    TMPDIR=/tmp \
    MALLOC_ARENA_MAX=2

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*

COPY SandboxJson.java SandboxHarness.java SandboxLauncher.java /opt/sandbox/java/src/
COPY training/Solution.java /opt/sandbox/java/training/Solution.java
COPY --chmod=0444 runner.py /opt/sandbox/runner.py

# The javac flags below must stay identical to JAVAC_JVM_FLAGS in
# sandbox_service/drivers/java.py (the AOT cache is only used when they match).
RUN set -eux; \
    cd /opt/sandbox/java; \
    javac -encoding UTF-8 -proc:none -d classes src/*.java; \
    jar --create --file harness.jar -C classes .; \
    rm -rf classes; \
    javac -J-XX:+UseSerialGC -J-XX:TieredStopAtLevel=1 -J-XX:-UsePerfData -J-Xss8m -J-Xmx256m \
          -J-XX:AOTCacheOutput=/opt/sandbox/java/javac.aot \
          -encoding UTF-8 -proc:none -g -nowarn -d /tmp/aot-train -cp harness.jar \
          training/Solution.java src/SandboxHarness.java src/SandboxJson.java src/SandboxLauncher.java; \
    rm -rf /tmp/aot-train /opt/sandbox/java/javac.aot.config; \
    mkdir -p /tmp/smoke && cd /tmp/smoke; \
    printf 'class Solution { public int[] twoSum(int[] a, int t) { return new int[]{a.length, t}; } }\n' > Solution.java; \
    javac -J-XX:+UseSerialGC -J-XX:TieredStopAtLevel=1 -J-XX:-UsePerfData -J-Xss8m -J-Xmx256m \
          -J-XX:AOTCache=/opt/sandbox/java/javac.aot -encoding UTF-8 -proc:none -g -nowarn -d classes Solution.java; \
    echo '{"class":"Solution","entry_candidates":["twoSum"],"params":[{"name":"a","type":"int[]"},{"name":"t","type":"int"}],"tests":[{"i":0,"args":[[1,2],5]}]}' \
      | java -XX:+UseSerialGC -cp /opt/sandbox/java/harness.jar:classes SandboxHarness 3>&1 | grep -q '"actual":\[2,5\]'; \
    cd / && rm -rf /tmp/smoke; \
    chmod -R a+rX /opt/sandbox

RUN groupadd --gid 10001 runner \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin runner \
 && find / -xdev -perm /6000 -type f -exec chmod a-s {} + \
 && rm -f /bin/sh /bin/bash /bin/dash /bin/rbash /usr/bin/sh /usr/bin/bash /usr/bin/dash /usr/bin/rbash

USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["/usr/bin/python3", "-I", "-B", "/opt/sandbox/runner.py"]
