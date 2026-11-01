FROM ubuntu:20.04
ENV DEBIAN_FRONTEND=noninteractive
RUN dpkg --add-architecture arm64 \
    && sed -i -E 's#^deb (http://(archive|security)\.ubuntu\.com/ubuntu/)#deb [arch=amd64] \1#' /etc/apt/sources.list \
    && printf '%s\n' \
       'deb [arch=arm64] http://ports.ubuntu.com/ubuntu-ports focal main universe' \
       'deb [arch=arm64] http://ports.ubuntu.com/ubuntu-ports focal-updates main universe' \
       'deb [arch=arm64] http://ports.ubuntu.com/ubuntu-ports focal-security main universe' \
       > /etc/apt/sources.list.d/arm64.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    build-essential clang python3 python3-dev python3-pip ninja-build \
    gcc-aarch64-linux-gnu g++-aarch64-linux-gnu libc6-dev-arm64-cross \
    libstdc++6-arm64-cross zlib1g:arm64 libffi7:arm64 libsystemd0:arm64 \
    libpcre3:arm64 libcom-err2:arm64 libgcrypt20:arm64 libgpg-error0:arm64 \
    liblzma5:arm64 liblz4-1:arm64 \
    qtbase5-dev qtbase5-private-dev qttools5-dev-tools libqt5svg5-dev \
    libssl-dev libzmq3-dev libcapnp-dev capnproto libeigen3-dev \
    ocl-icd-opencl-dev libgles2-mesa-dev libegl1-mesa-dev libwayland-dev \
    xvfb xauth git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /opt/sysroot/usr/lib/aarch64-linux-gnu /opt/sysroot/lib/aarch64-linux-gnu \
    && cp -a /usr/aarch64-linux-gnu/lib/libstdc++.so.6* /opt/sysroot/usr/lib/aarch64-linux-gnu/ \
    && cp -a /usr/aarch64-linux-gnu/lib/libgcc_s.so.1 /opt/sysroot/usr/lib/aarch64-linux-gnu/ \
    && cp -a /usr/aarch64-linux-gnu/lib/libz.so.1* /opt/sysroot/usr/lib/aarch64-linux-gnu/ \
    && ln -sfn libstdc++.so.6 /opt/sysroot/usr/lib/aarch64-linux-gnu/libstdc++.so \
    && ln -sfn libgcc_s.so.1 /opt/sysroot/usr/lib/aarch64-linux-gnu/libgcc_s.so \
    && ln -sfn libz.so.1.2.11 /opt/sysroot/usr/lib/aarch64-linux-gnu/libz.so.1 \
    && ln -sfn libz.so.1 /opt/sysroot/usr/lib/aarch64-linux-gnu/libz.so
WORKDIR /work
ENTRYPOINT ["python3", "tools/ui_build/build.py"]
