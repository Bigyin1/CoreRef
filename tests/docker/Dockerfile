FROM nixos/nix@sha256:746280551ddc1c72177d1a597c05eacac86dfaa18d5d649bf86411278bcd5470 AS nix

FROM ubuntu:24.04
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends ca-certificates curl environment-modules \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --user-group --shell /bin/bash flow \
    && install -d -o flow -g flow /cache /etc/coreref/eda
COPY --from=nix --chown=flow:flow /nix /nix
COPY nix.conf /etc/nix/nix.conf
ENV PATH="/nix/var/nix/profiles/default/bin:${PATH}"
USER flow
