# Offline On-Premise Deployment Experiment

## 1. Goal

이 문서의 목적은 다음 전체 흐름을 직접 실험하는 것이다.

```text
AI로 예제 프로젝트 생성
    ↓
FastAPI + PostgreSQL
    ↓
Docker Compose로 로컬 실행
    ↓
Docker image build
    ↓
Offline bundle 생성
    ↓
네트워크 차단 환경으로 반입
    ↓
Docker image import
    ↓
Docker Compose로 실행
    ↓
완전 오프라인 설치 검증
    ↓
Harbor 확장
    ↓
Kubernetes 확장
    ↓
Helm 확장
```

최초 목표는 복잡한 Kubernetes 환경을 바로 구축하는 것이 아니다.

먼저 다음 조건을 만족하는 가장 작은 실험을 만든다.

```text
인터넷 연결 환경에서 빌드 완료
→ Docker image로 패키징
→ image를 bundle로 export
→ 인터넷이 없는 별도 환경으로 이동
→ bundle만으로 FastAPI + PostgreSQL 실행
```

성공 기준은 폐쇄망 환경에서 다음 외부 서비스에 접근하지 않고 애플리케이션이 정상 실행되는 것이다.

```text
PyPI
Docker Hub
GitHub
외부 package repository
```

---

# 2. AI에게 예제 프로젝트 생성시키기

가장 먼저 AI에게 아래 조건의 예제 프로젝트를 생성하도록 요청한다.

## 2.1 예제 프로젝트 요구사항

```text
Python 3.12
FastAPI
PostgreSQL
SQLAlchemy
psycopg
Docker
Docker Compose
```

애플리케이션 기능은 최소한으로 유지한다.

```text
GET /health
    → 서비스 상태 확인

POST /items?name=test
    → PostgreSQL에 데이터 저장

GET /items
    → PostgreSQL에 저장된 데이터 조회
```

## 2.2 AI에게 사용할 예시 프롬프트

```text
FastAPI + PostgreSQL 기반의 최소 예제 프로젝트를 만들어줘.

목적은 이후 Docker 기반 offline bundling과 on-premise 폐쇄망 배포를 실험하기 위함이야.

요구사항:
- Python 3.12
- FastAPI
- PostgreSQL
- SQLAlchemy
- psycopg
- Docker
- Docker Compose

API:
- GET /health
- POST /items?name={name}
- GET /items

조건:
- FastAPI와 PostgreSQL은 각각 Docker container로 실행
- FastAPI는 PostgreSQL service name으로 DB에 연결
- PostgreSQL healthcheck 이후 FastAPI가 시작되도록 구성
- Python dependency는 FastAPI Docker image build 시 모두 설치
- 실행 시 외부 PyPI 접근이 필요하지 않도록 image에 dependency가 포함되어야 함
- PostgreSQL 데이터는 volume으로 유지
- 환경 변수는 .env로 관리
- 코드와 Docker 설정을 실제 실행 가능한 수준으로 작성

프로젝트 구조, 각 파일의 전체 내용, 실행 방법까지 작성해줘.
```

---

# 3. 목표 프로젝트 구조

예제 프로젝트는 최소한 다음 구조를 가진다.

```text
offline-demo/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── database.py
│   └── models.py
│
├── postgres/
│   ├── Dockerfile
│   ├── postgresql.conf
│   └── init/
│       └── 001_init.sql
│
├── Dockerfile
├── requirements.txt
├── docker-compose.yml
├── .env
│
└── offline/
    ├── build-bundle.sh
    └── install.sh
```

처음에는 PostgreSQL custom 설정이 필요하지 않다면 `postgres/Dockerfile`, `postgresql.conf`, `init/`을 생략해도 된다.

Offline bundling 자체를 이해하는 것이 우선이다.

---

# 4. FastAPI 애플리케이션 구성

## 4.1 requirements.txt

```text
fastapi
uvicorn
sqlalchemy
psycopg[binary]
```

실제 프로젝트에서는 버전을 고정하는 것이 좋다.

예:

```text
fastapi==...
uvicorn==...
sqlalchemy==...
psycopg[binary]==...
```

Offline release에서는 dependency version이 고정되어야 같은 release를 다시 만들었을 때 동일한 결과를 얻기 쉽다.

## 4.2 database.py

```python
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()
```

## 4.3 models.py

```python
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
```

## 4.4 main.py

```python
from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine
from app.models import Item


app = FastAPI()


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/items")
def create_item(name: str, db: Session = Depends(get_db)):
    item = Item(name=name)

    db.add(item)
    db.commit()
    db.refresh(item)

    return item


@app.get("/items")
def list_items(db: Session = Depends(get_db)):
    return db.query(Item).all()
```

실제 서비스에서는 Alembic 등의 migration tool을 사용하는 것이 좋지만, 첫 실험에서는 `create_all()`로 단순화한다.

---

# 5. FastAPI Docker Image 만들기

Dockerfile:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install \
    --no-cache-dir \
    -r requirements.txt

COPY app ./app

CMD [
    "uvicorn",
    "app.main:app",
    "--host",
    "0.0.0.0",
    "--port",
    "8000"
]
```

여기서 중요한 점은 Python package가 Docker build 단계에서 설치된다는 것이다.

```text
PyPI
 ↓
docker build
 ↓
Python dependencies
 ↓
FastAPI source
 ↓
Docker image
```

Docker image가 완성된 이후 production on-prem 서버에서는 `pip install`을 실행할 필요가 없다.

---

# 6. PostgreSQL 구성

기존에 사용하던 custom PostgreSQL image 방식도 사용할 수 있다.

예:

```dockerfile
ARG POSTGRES_IMAGE=postgres:17

FROM ${POSTGRES_IMAGE}
```

필요하면 추가 설정을 넣는다.

```dockerfile
ARG POSTGRES_IMAGE=postgres:17

FROM ${POSTGRES_IMAGE}

COPY postgresql.conf /etc/postgresql/postgresql.conf
```

하지만 첫 번째 offline bundling 실험에서는 가능하면 공식 PostgreSQL image를 그대로 사용하는 것이 단순하다.

```yaml
image: postgres:17
```

Offline bundling 검증이 끝난 이후 custom PostgreSQL image로 확장한다.

---

# 7. Docker Compose 작성

예:

```yaml
services:

  postgres:
    image: postgres:17

    container_name: offline-demo-postgres

    restart: unless-stopped

    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}

    volumes:
      - postgres_data:/var/lib/postgresql/data

    healthcheck:
      test:
        [
          "CMD-SHELL",
          "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"
        ]
      interval: 5s
      timeout: 5s
      retries: 12
      start_period: 10s


  backend:
    build:
      context: .
      dockerfile: Dockerfile

    image: offline-demo-backend:1.0.0

    container_name: offline-demo-backend

    restart: unless-stopped

    environment:
      DATABASE_URL: >-
        postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}

    ports:
      - "8000:8000"

    depends_on:
      postgres:
        condition: service_healthy


volumes:
  postgres_data:
```

Docker Compose network 안에서는 PostgreSQL service 이름인 `postgres`가 hostname 역할을 한다.

```text
FastAPI
   │
   │ postgres:5432
   ▼
PostgreSQL
```

따라서 container IP를 직접 지정하지 않는다.

---

# 8. 환경 변수

`.env`

```env
POSTGRES_USER=app
POSTGRES_PASSWORD=app-password
POSTGRES_DB=app
```

실제 production에서는 password를 repository에 commit하지 않는다.

이 실험에서는 구조 확인을 위해 단순화한다.

---

# 9. 인터넷이 있는 환경에서 정상 동작 검증

먼저 Docker image를 build한다.

```bash
docker compose build
```

실행한다.

```bash
docker compose up -d
```

상태 확인:

```bash
docker compose ps
```

FastAPI health check:

```bash
curl http://localhost:8000/health
```

예상 결과:

```json
{"status":"ok"}
```

데이터 생성:

```bash
curl -X POST \
  "http://localhost:8000/items?name=test"
```

데이터 조회:

```bash
curl http://localhost:8000/items
```

이 단계에서는 반드시 FastAPI와 PostgreSQL 간 연결이 정상인지 확인한다.

---

# 10. Offline Bundle 만들기

이제 실제 offline bundling을 시작한다.

현재 필요한 image:

```text
offline-demo-backend:1.0.0
postgres:17
```

확인:

```bash
docker images
```

Docker image를 archive로 저장한다.

```bash
docker save \
  offline-demo-backend:1.0.0 \
  postgres:17 \
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
POSTGRES_USER=app
POSTGRES_PASSWORD=change-me
POSTGRES_DB=app
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
  offline-demo-1.0.0.tar.gz \
  offline-demo-1.0.0/
```

또는 zstd를 사용할 수 있다.

```bash
tar -I zstd \
  -cf offline-demo-1.0.0.tar.zst \
  offline-demo-1.0.0/
```

최종 산출물:

```text
offline-demo-1.0.0.tar.zst
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

# 17. build-bundle.sh 자동화

수동 작업이 성공하면 bundle 생성 과정을 script로 만든다.

예:

```bash
#!/usr/bin/env bash

set -euo pipefail


VERSION="1.0.0"
BUNDLE_DIR="offline-demo-${VERSION}"


echo "[1/5] Building images"

docker compose build


echo "[2/5] Preparing bundle"

rm -rf "${BUNDLE_DIR}"
mkdir -p "${BUNDLE_DIR}"


echo "[3/5] Exporting images"

docker save \
  offline-demo-backend:${VERSION} \
  postgres:17 \
  -o "${BUNDLE_DIR}/images.tar"


echo "[4/5] Copying deployment files"

cp docker-compose.yml "${BUNDLE_DIR}/"
cp offline/install.sh "${BUNDLE_DIR}/"
cp .env.example "${BUNDLE_DIR}/"

echo "${VERSION}" > "${BUNDLE_DIR}/VERSION"


echo "[5/5] Creating archive"

tar -I zstd \
  -cf "${BUNDLE_DIR}.tar.zst" \
  "${BUNDLE_DIR}"


echo "Bundle created: ${BUNDLE_DIR}.tar.zst"
```

최종적으로 release build는 다음 명령 하나로 수행하는 것을 목표로 한다.

```bash
./offline/build-bundle.sh
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
harbor.internal/myapp/backend:1.0.0
harbor.internal/infra/postgres:17
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
  repository: harbor.internal/myapp/backend
  tag: "1.0.0"
```

PostgreSQL:

```yaml
image:
  repository: harbor.internal/infra/postgres
  tag: "17"
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
./offline/build-bundle.sh
```

을 실행하면:

```text
offline-demo-1.0.0.tar.zst
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
