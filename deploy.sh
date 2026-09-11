#!/usr/bin/env bash
set -euo pipefail

# 스크립트가 위치한 디렉터리(demo) 기준
cd "$(dirname "$0")"

VERSION="1.0.0"
BUNDLE_NAME="offline-demo-${VERSION}"
BUNDLE_DIR="deployment/${BUNDLE_NAME}"

echo "[1/5] Building images..."
# docker 폴더 안의 compose 파일을 사용하여 빌드
docker compose -f docker/docker-compose.yml build

echo "[2/5] Preparing bundle directory..."
rm -rf "${BUNDLE_DIR}"
mkdir -p "${BUNDLE_DIR}"

echo "[3/5] Exporting Docker images to tar..."
docker save \
  offline-fastapi:${VERSION} \
  postgres:15 \
  -o "${BUNDLE_DIR}/images.tar"

echo "[4/5] Copying deployment files..."
# docker 폴더 안에 있는 파일들을 번들 폴더로 복사
cp docker/docker-compose.yml "${BUNDLE_DIR}/"
cp docker/install.sh "${BUNDLE_DIR}/"
cp docker/.env.example "${BUNDLE_DIR}/"
echo "${VERSION}" > "${BUNDLE_DIR}/VERSION"

echo "[4.5/5] Generating checksums..."
# BUNDLE_DIR 안에서 생성해야 압축을 풀었을 때 올바른 상대 경로로 체크섬이 매칭됩니다.
(cd "${BUNDLE_DIR}" && sha256sum images.tar docker-compose.yml install.sh > checksums.sha256)

echo "[5/5] Creating final archive (.tar.gz)..."
# 압축 파일 내부에 deployment/ 폴더가 포함되지 않고 알맹이만 나오도록 -C 옵션 사용
# 실험용 .env 파일이 실수로 들어가는 것을 방지하기 위해 명시적으로 --exclude 추가
tar --exclude='.env' -czf "deployment/${BUNDLE_NAME}.tar.gz" -C deployment "${BUNDLE_NAME}"

# (선택) 포장 후 남은 찌꺼기 폴더 삭제
rm -rf "${BUNDLE_DIR}"

echo "✅ Bundle successfully created: $(pwd)/deployment/${BUNDLE_NAME}.tar.gz"
