# 10. Offline Bundle 만들기

이제 실제 offline bundling을 시작한다.

현재 필요한 image:

```text
offline-fastapi:1.0.0
postgres:15
```

확인:

```bash
docker images
```

Docker image를 archive로 저장한다.

```bash
docker save \
  offline-fastapi:1.0.0 \
  postgres:15 \
  -o images.tar
```

결과:

```text
images.tar
```

이 파일 안에 FastAPI와 PostgreSQL image가 포함된다.

---

# 11. Offline Bundle 디렉터리 만들기

최초 버전은 단순하게 유지한다.

```text
offline-demo-1.0.0/
├── images.tar
├── docker-compose.yml
├── .env.example
├── install.sh
├── VERSION
└── checksums.sha256
```

`VERSION`

```text
1.0.0
```

`.env.example`

```env
POSTGRES_USER=admin
POSTGRES_PASSWORD=your_password_here
POSTGRES_DB=offline_db
POSTGRES_PORT=5432
API_PORT=8000
```

---

# 12. Checksum 생성

반입 과정에서 파일이 손상되거나 변경되지 않았는지 확인할 수 있도록 checksum을 생성한다.

예:

```bash
sha256sum \
  images.tar \
  docker-compose.yml \
  install.sh \
  > checksums.sha256
```

macOS에서는 다음 명령을 사용할 수도 있다.

```bash
shasum -a 256 \
  images.tar \
  docker-compose.yml \
  install.sh \
  > checksums.sha256
```

---

# 13. install.sh 만들기

최초 버전은 단순하게 작성한다.

```bash
#!/usr/bin/env bash

set -euo pipefail


echo "[1/4] Verifying files"

if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c checksums.sha256
fi


echo "[2/4] Loading Docker images"

docker load \
    -i images.tar


echo "[3/4] Starting services"

docker compose \
    up \
    -d


echo "[4/4] Checking services"

docker compose ps
```

실제 production version에서는 다음 검증을 추가할 수 있다.

```text
Docker 설치 여부
Docker Compose 설치 여부
CPU architecture
Disk 용량
Memory
Port 충돌
기존 설치 버전
환경 변수 파일 존재
Checksum
Health check
```

---

# 14. 최종 Bundle 압축

```bash
tar -czf \
  deployment/offline-demo-1.0.0.tar.gz \
  -C deployment offline-demo-1.0.0
```

또는 zstd를 사용할 수 있다.

```bash
tar -I zstd \
  -cf deployment/offline-demo-1.0.0.tar.zst \
  -C deployment offline-demo-1.0.0
```

최종 산출물:

```text
deployment/offline-demo-1.0.0.tar.gz
```

이 파일이 고객사 또는 폐쇄망으로 전달되는 release artifact가 된다.

---

# 15. 폐쇄망 설치 실험

가장 중요한 단계다.

실험용 VM 또는 별도 서버를 준비한다.

조건:

```text
인터넷 연결 없음
Docker image 없음
소스코드 없음
PyPI 접근 불가
Docker Hub 접근 불가
```

제공되는 것은 오직 다음 파일뿐이다.

```text
offline-demo-1.0.0.tar.zst
```

압축 해제:

```bash
tar -I zstd \
  -xf offline-demo-1.0.0.tar.zst
```

디렉터리 이동:

```bash
cd offline-demo-1.0.0
```

환경 변수 준비:

```bash
cp .env.example .env
```

필요한 값을 수정한다.

설치:

```bash
./install.sh
```

---

# 16. 폐쇄망 설치 성공 검증

Docker image 확인:

```bash
docker images
```

Container 확인:

```bash
docker compose ps
```

FastAPI 확인:

```bash
curl http://localhost:8000/health
```

데이터 생성:

```bash
curl -X POST \
  "http://localhost:8000/items?name=offline-test"
```

데이터 조회:

```bash
curl http://localhost:8000/items
```

성공 기준:

```text
FastAPI 정상 실행
PostgreSQL 정상 실행
FastAPI → PostgreSQL 연결 성공
데이터 insert 성공
데이터 조회 성공
Docker Hub 접근 없음
PyPI 접근 없음
외부 인터넷 접근 없음
```

이 조건을 만족해야 실제 offline-installable release라고 볼 수 있다.

---

# 17. deploy.sh 자동화

수동 작업이 성공하면 bundle 생성 과정을 script로 만든다.

예:

```bash
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

echo "[5/5] Creating final archive (.tar.gz)..."
# 압축 파일 내부에 deployment/ 폴더가 포함되지 않고 알맹이만 나오도록 -C 옵션 사용
tar -czf "deployment/${BUNDLE_NAME}.tar.gz" -C deployment "${BUNDLE_NAME}"

# (선택) 포장 후 남은 찌꺼기 폴더 삭제
rm -rf "${BUNDLE_DIR}"

echo "✅ Bundle successfully created: $(pwd)/deployment/${BUNDLE_NAME}.tar.gz"
```

최종적으로 release build는 다음 명령 하나로 수행하는 것을 목표로 한다.

```bash
./deploy.sh
```

---

# 18. Harbor를 추가하는 이유

현재 구조는 다음과 같다.

```text
images.tar
    ↓
docker load
    ↓
Docker Compose
```

서버가 한 대라면 충분할 수 있다.

하지만 on-prem 서버가 여러 대거나 Kubernetes cluster를 운영하면 각 node에 image를 직접 관리하는 방식이 불편해진다.

그래서 폐쇄망 내부에 Harbor를 둔다.

```text
Offline Bundle
      ↓
폐쇄망 반입
      ↓
Harbor
      ↓
Docker / Kubernetes
```

Harbor는 폐쇄망 내부의 private OCI registry 역할을 한다.

예:

```text
harbor.internal/myapp/offline-fastapi:1.0.0
harbor.internal/infra/postgres:15
```

역할:

```text
Docker/OCI image 저장
Version 관리
Push/Pull
RBAC
Image scanning
Audit
Retention
```

Offline Bundle과 Harbor는 서로 대체 관계가 아니다.

```text
Offline Bundle
= 외부에서 폐쇄망으로 artifact 운반

Harbor
= 폐쇄망 내부에서 artifact 저장 및 배포
```

---

# 19. Kubernetes 단계로 확장

Docker Compose 단계가 완료되면 같은 application을 Kubernetes로 옮긴다.

현재:

```text
FastAPI
PostgreSQL
    ↓
Docker Compose
```

다음:

```text
FastAPI
PostgreSQL
    ↓
Kubernetes
```

필요한 Kubernetes resource 예:

```text
Deployment
Service
StatefulSet
PersistentVolumeClaim
ConfigMap
Secret
```

FastAPI:

```text
Deployment
Service
```

PostgreSQL:

```text
StatefulSet
Service
PersistentVolumeClaim
```

로 시작할 수 있다.

---

# 20. Helm 단계로 확장

Kubernetes YAML이 많아지면 Helm Chart로 패키지화한다.

예:

```text
helm/
└── offline-demo/
    ├── Chart.yaml
    ├── values.yaml
    └── templates/
        ├── backend-deployment.yaml
        ├── backend-service.yaml
        ├── postgres-statefulset.yaml
        ├── postgres-service.yaml
        └── postgres-pvc.yaml
```

Helm의 역할:

```text
여러 Kubernetes YAML
        ↓
하나의 Chart
        ↓
설정 template
        ↓
install / upgrade / uninstall
```

설치:

```bash
helm install \
  offline-demo \
  ./helm/offline-demo
```

업데이트:

```bash
helm upgrade \
  offline-demo \
  ./helm/offline-demo
```

---

# 21. Harbor + Kubernetes + Helm 최종 구조

최종적으로 다음 구조로 발전시킨다.

```text
[인터넷 가능 환경]

Python source
      ↓
Docker build
      ↓
Application image
      ↓
External dependency images 수집
      ↓
Offline Bundle
      ↓
══════════ AIR GAP ══════════
      ↓
[On-Prem]

Offline Bundle
      ↓
Harbor
      ↓
Helm
      ↓
Kubernetes
      ↓

┌─────────────────┐
│ FastAPI         │
├─────────────────┤
│ PostgreSQL      │
└─────────────────┘
```

Kubernetes에서는 Docker Hub가 아니라 Harbor image를 사용한다.

예:

```yaml
image:
  repository: harbor.internal/myapp/offline-fastapi
  tag: "1.0.0"
```

PostgreSQL:

```yaml
image:
  repository: harbor.internal/infra/postgres
  tag: "15"
```

---

# 22. 전체 실험 순서

다음 순서대로 진행한다.

## V1. Example Application

```text
FastAPI
+
PostgreSQL
```

목표:

```text
기능 자체가 정상적으로 동작
```

## V2. Docker Compose

```text
FastAPI container
+
PostgreSQL container
```

목표:

```text
Docker Compose만으로 전체 서비스 실행
```

## V3. Offline Docker Bundle

```text
docker save
docker load
```

목표:

```text
images.tar만으로 image 복원
```

## V4. Full Offline Installation

```text
images.tar
docker-compose.yml
install.sh
```

목표:

```text
인터넷이 완전히 차단된 VM에서 설치
```

## V5. Harbor

```text
Offline Bundle
    ↓
Harbor
    ↓
Application
```

목표:

```text
폐쇄망 내부 registry 구축
```

## V6. Kubernetes

```text
Harbor
    ↓
Kubernetes
```

목표:

```text
Container runtime orchestration
```

## V7. Helm

```text
Harbor
    ↓
Helm
    ↓
Kubernetes
```

목표:

```text
Kubernetes application 설치/업데이트 패키지화
```

---

# 23. 핵심 개념 정리

## Docker

```text
Python application을 실행 가능한 container image로 만든다.
```

## Offline Bundle

```text
폐쇄망에서 필요한 artifact를 하나의 release package로 만든다.
```

## Harbor

```text
폐쇄망 내부에서 Docker/OCI image를 저장하고 관리한다.
```

## Kubernetes

```text
Container를 실제로 실행하고 운영한다.
```

## Helm

```text
Kubernetes application을 패키지화하고 설치/업데이트한다.
```

전체 관계:

```text
Python Code
    ↓
Docker
    ↓
OCI Image
    ↓
Offline Bundle
    ↓
════════ AIR GAP ════════
    ↓
Harbor
    ↓
Helm
    ↓
Kubernetes
```

---

# 24. 최종 목표

최종적으로 외부 환경에서:

```bash
./deploy.sh
```

을 실행하면:

```text
deployment/offline-demo-1.0.0.tar.gz
```

가 생성되어야 한다.

폐쇄망에서는:

```bash
./install.sh
```

만 실행하면:

```text
FastAPI
PostgreSQL
```

이 실행되어야 한다.

이후 동일한 release workflow를:

```text
Docker Compose
    ↓
Harbor
    ↓
Kubernetes
    ↓
Helm
```

으로 단계적으로 확장한다.

가장 중요한 검증 원칙은 다음과 같다.

> 폐쇄망에 release bundle 하나만 제공했을 때 외부 네트워크 접근 없이 전체 시스템을 처음부터 설치하고 실행할 수 있어야 한다.
