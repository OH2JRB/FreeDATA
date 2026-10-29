################################################################################
# FreeDATA server with the web GUI, built from this repository's own source.
# codec2 and Hamlib are built for the image's architecture (amd64 or arm64).
################################################################################

################################################################################
# Build frontend
################################################################################
FROM node:22-alpine AS frontend

WORKDIR /src

COPY freedata_gui/package.json freedata_gui/package-lock.json ./

RUN npm ci

COPY freedata_gui ./

RUN npm run build

################################################################################
# Build server
################################################################################
FROM python:3.13-slim-trixie AS server

ARG HAMLIB_VERSION=4.7.2

RUN apt-get update && \
  apt-get install -y --no-install-recommends build-essential cmake git portaudio19-dev && \
  rm -rf /var/lib/apt/lists/*

# Hamlib (rigctld)
WORKDIR /src

ADD https://github.com/Hamlib/Hamlib/releases/download/${HAMLIB_VERSION}/hamlib-${HAMLIB_VERSION}.tar.gz ./hamlib.tar.gz

RUN tar -xf hamlib.tar.gz && \
  cd hamlib-${HAMLIB_VERSION} && \
  ./configure --prefix=/app/FreeDATA-hamlib --disable-static && \
  make -j"$(nproc)" && \
  make install

# codec2
RUN git clone --depth 1 https://github.com/drowe67/codec2.git /src/codec2 && \
  cmake -S /src/codec2 -B /src/codec2/build_linux && \
  make -C /src/codec2/build_linux codec2 -j"$(nproc)"

# FreeDATA, installed into a virtual environment after codec2 and the GUI are
# in place, so the installed package holds both
WORKDIR /app/FreeDATA

COPY . ./

COPY --from=frontend /src/dist ./freedata_gui/dist

# replace the bundled x86-64 library with the one built for this architecture
RUN cp -L /src/codec2/build_linux/src/libcodec2.so freedata_server/lib/codec2/libcodec2.so

RUN python3 -m venv /app/venv && \
  /app/venv/bin/pip install --no-cache-dir --upgrade pip && \
  /app/venv/bin/pip install --no-cache-dir .

################################################################################
# Final image
################################################################################
FROM python:3.13-slim-trixie

ENV PATH="/app/FreeDATA-hamlib/bin:/app/venv/bin:$PATH"

ENV FREEDATA_CONFIG=/data/config.ini
ENV FREEDATA_DATABASE=/data/freedata-messages.db
ENV HOME=/home/freedata

RUN apt-get update && \
  apt-get install -y --no-install-recommends \
    libportaudio2 \
    alsa-utils \
    libasound2-plugins \
    pulseaudio \
    pulseaudio-utils && \
  rm -rf /var/lib/apt/lists/*

COPY --from=server /app/venv /app/venv
COPY --from=server /app/FreeDATA-hamlib /app/FreeDATA-hamlib
COPY entrypoint.sh /entrypoint.sh

RUN useradd --create-home --home-dir $HOME freedata && \
  usermod -aG audio,pulse,pulse-access freedata && \
  mkdir -p /data && \
  chown freedata:freedata /data && \
  chmod +x /entrypoint.sh

USER freedata

EXPOSE 5000

ENTRYPOINT [ "/entrypoint.sh" ]
