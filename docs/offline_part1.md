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

