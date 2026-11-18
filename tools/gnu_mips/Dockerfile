# Copyright (c) 2026 nordseele. GPL-3.0-or-later with an additional permission: see LICENSE and LICENSE-EXCEPTION.md.
# Standard GNU cross toolchain for the patch (GCC and binutils from Debian),
# used instead of Microchip XC32 by `make_mpe_image --toolchain gnu`.
# Build: docker build -t p12-gnu-mips tools/gnu_mips
FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc-mipsel-linux-gnu binutils-mipsel-linux-gnu \
 && rm -rf /var/lib/apt/lists/*
