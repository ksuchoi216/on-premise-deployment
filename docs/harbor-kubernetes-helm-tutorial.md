# Harbor + kubeadm Kubernetes + Helm 폐쇄망 실습

이 문서는 기존 [Part 1](offline_part1.md), [Part 2](offline_part2.md)에서 만든 offline-fastapi:1.0.0, postgres:15, images.tar를 이용한다. 기존 k3s를 제거하고 kubeadm 기반 표준 Kubernetes에 Harbor를 설치한 뒤, Kubernetes YAML과 Helm Chart로 FastAPI/PostgreSQL을 운영한다.

~~~
인터넷 연결 준비 서버                         폐쇄망 대상 서버
Kubernetes package + bootstrap image  ───→  kubeadm Kubernetes
Flannel + local storage image          ───→  CNI / PVC
Harbor Chart + Harbor image            ───→  Harbor
images.tar                             ───→  Harbor → Kubernetes → Helm
~~~

## 1. 핵심 개념과 범위

| 구성 요소 | 역할 | 실습 선택 |
| --- | --- | --- |
| Docker | image를 load, tag, push | 기존 Docker 26 |
| containerd | Kubernetes의 CRI runtime | Docker와 함께 설치된 containerd |
| kubeadm | Kubernetes control plane 초기화 | 단일 control-plane node |
| Flannel | Pod 네트워크(CNI) | Pod CIDR 10.244.0.0/16 |
| local-path-provisioner | 로컬 디스크 PVC 동적 생성 | local-path StorageClass |
| Harbor | 폐쇄망 OCI registry | TLS NodePort 30443 |
| Helm | YAML template/package | FastAPI/PostgreSQL Chart |

이 문서는 단일 서버 학습용이다. local-path PVC는 해당 서버 로컬 디스크에 있으므로 node 장애를 견디지 못한다. 실제 운영에는 공유 스토리지 또는 object storage, 조직 CA, DNS, backup, HA control plane이 필요하다.

## 2. 기존 k3s 제거

> 경고: 다음 단계는 기존 k3s의 Pod, local datastore, local-path PV 데이터를 삭제한다. 보존할 데이터가 있다면 먼저 backup한다. Docker image와 images.tar은 삭제하지 않는다.

먼저 현재 상태를 기록한다.

~~~bash
uname -a
cat /etc/os-release
df -h /
free -h
sudo docker version
containerd --version
helm version
sudo k3s --version
sudo k3s kubectl get nodes -o wide
sudo k3s kubectl get all -A
sudo k3s kubectl get pvc -A
~~~

정확히 RESET-K3S를 입력한 경우에만 제거한다.

~~~bash
read -r -p 'k3s의 Pod와 local PV 데이터가 삭제됩니다. RESET-K3S 입력: ' ANSWER
[ "$ANSWER" = 'RESET-K3S' ] || { echo '취소했습니다.'; exit 1; }

sudo /usr/local/bin/k3s-uninstall.sh

sudo systemctl status k3s --no-pager || true
sudo test ! -e /etc/rancher/k3s/k3s.yaml && echo 'k3s kubeconfig removed'
sudo test ! -d /var/lib/rancher/k3s && echo 'k3s data directory removed'
sudo docker images | rg 'offline-fastapi|postgres' || true
~~~

공식 참고: [k3s 제거](https://docs.k3s.io/installation/uninstall)

## 3. 인터넷 연결 준비 서버: 반입 artifact 만들기

이 절은 대상과 같은 Ubuntu 20.04 x86_64 준비 서버에서 실행한다. 대상 폐쇄망 서버에서는 외부 APT repository, Docker Hub, Helm repository에 접근하지 않는다.

### 3.1 버전과 디렉터리 고정

아래 버전은 예시다. 시작 전에 실제 release 존재 여부를 확인하고, 준비 서버와 대상 서버에서 같은 값을 사용한다.

~~~bash
export KUBERNETES_MINOR='v1.37'
export KUBERNETES_VERSION='v1.37.0'
export FLANNEL_VERSION='v0.27.4'
export LOCAL_PATH_VERSION='v0.0.36'
export HARBOR_CHART_VERSION='1.18.0'
export HARBOR_HOST='harbor.algo.local'
export HARBOR_HTTPS_NODEPORT='30443'
export BUNDLE="$PWD/offline-k8s-lab"

mkdir -p "$BUNDLE"/{debs,images,charts,manifests,checksums}

cat > "$BUNDLE/versions.env" <<EOF
KUBERNETES_MINOR=$KUBERNETES_MINOR
KUBERNETES_VERSION=$KUBERNETES_VERSION
FLANNEL_VERSION=$FLANNEL_VERSION
LOCAL_PATH_VERSION=$LOCAL_PATH_VERSION
HARBOR_CHART_VERSION=$HARBOR_CHART_VERSION
HARBOR_HOST=$HARBOR_HOST
HARBOR_HTTPS_NODEPORT=$HARBOR_HTTPS_NODEPORT
EOF
~~~

### 3.2 Kubernetes package와 bootstrap image 준비

Kubernetes APT repository는 minor version별로 분리된다. KUBERNETES_MINOR와 URL을 맞춘다.

~~~bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL "https://pkgs.k8s.io/core:/stable:/$KUBERNETES_MINOR/deb/Release.key" \
  | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg

echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/$KUBERNETES_MINOR/deb/ /" \
  | sudo tee /etc/apt/sources.list.d/kubernetes.list

sudo apt-get update
sudo apt-get --download-only -y \
  -o "Dir::Cache::archives=$BUNDLE/debs" \
  install kubelet kubeadm kubectl kubernetes-cni cri-tools

sudo apt-get install -y kubeadm
kubeadm config images list --kubernetes-version "$KUBERNETES_VERSION" \
  | tee "$BUNDLE/images/kubeadm-images.txt"
~~~

다운로드 전용 설치는 준비 서버에 이미 설치된 의존 package를 다시 받지 않을 수 있다. 대상과 같은 새 Ubuntu VM에서 bundle만으로 설치되는지 반드시 사전 검증한다.

Flannel과 local storage manifest 및 image를 받는다.

~~~bash
curl -fL -o "$BUNDLE/manifests/kube-flannel.yml" \
  "https://github.com/flannel-io/flannel/releases/download/$FLANNEL_VERSION/kube-flannel.yml"

curl -fL -o "$BUNDLE/manifests/local-path-storage.yaml" \
  "https://raw.githubusercontent.com/rancher/local-path-provisioner/$LOCAL_PATH_VERSION/deploy/local-path-storage.yaml"

awk '/^[[:space:]]*image:/{gsub(/"/, "", $2); print $2}' \
  "$BUNDLE"/manifests/*.yaml | sort -u \
  | tee "$BUNDLE/images/addon-images.txt"

cat "$BUNDLE/images/kubeadm-images.txt" "$BUNDLE/images/addon-images.txt" \
  | sort -u > "$BUNDLE/images/kubernetes-bootstrap-images.txt"

while read -r image; do sudo docker pull "$image"; done \
  < "$BUNDLE/images/kubernetes-bootstrap-images.txt"

sudo docker save -o "$BUNDLE/images/kubernetes-bootstrap-images.tar" \
  $(cat "$BUNDLE/images/kubernetes-bootstrap-images.txt")
~~~

Flannel은 Pod network다. CNI 설치 전 CoreDNS가 Pending인 것은 정상이다. local-path-provisioner는 단일 node의 로컬 디스크에 PVC를 만든다.

### 3.3 Harbor Chart와 Harbor image 준비

Harbor가 아직 없으므로 Chart archive를 local file로 반입한다.

~~~bash
helm repo add harbor https://helm.goharbor.io
helm repo update
helm pull harbor/harbor --version "$HARBOR_CHART_VERSION" --destination "$BUNDLE/charts"

cat > "$BUNDLE/manifests/harbor-values.yaml" <<EOF
expose:
  type: nodePort
  tls:
    enabled: true
    certSource: secret
    secret:
      secretName: harbor-tls
  nodePort:
    ports:
      http:
        nodePort: 30080
      https:
        nodePort: $HARBOR_HTTPS_NODEPORT
externalURL: https://$HARBOR_HOST:$HARBOR_HTTPS_NODEPORT
persistence:
  enabled: true
  persistentVolumeClaim:
    registry:
      storageClass: local-path
    jobservice:
      storageClass: local-path
trivy:
  enabled: false
harborAdminPassword: Harbor12345
EOF

helm template harbor "$BUNDLE/charts/harbor-$HARBOR_CHART_VERSION.tgz" \
  --namespace harbor --values "$BUNDLE/manifests/harbor-values.yaml" \
  > "$BUNDLE/manifests/harbor-rendered.yaml"

awk '/^[[:space:]]*image:/{gsub(/"/, "", $2); print $2}' \
  "$BUNDLE/manifests/harbor-rendered.yaml" | sort -u \
  | tee "$BUNDLE/images/harbor-images.txt"

while read -r image; do sudo docker pull "$image"; done \
  < "$BUNDLE/images/harbor-images.txt"

sudo docker save -o "$BUNDLE/images/harbor-images.tar" \
  $(cat "$BUNDLE/images/harbor-images.txt")
~~~

Harbor12345는 실습용 초기 비밀번호다. 실제 운영에서는 Secret 또는 secret manager를 사용하고 설치 직후 변경한다. Trivy는 첫 설치의 image 수와 자원 사용량을 줄이기 위해 껐다.

### 3.4 application image와 checksum 추가

Part 2의 images.tar를 bundle에 복사한다.

~~~bash
cp /path/to/offline-demo-1.0.0/images.tar "$BUNDLE/images/images.tar"

(cd "$BUNDLE" && find . -type f -print0 | sort -z | xargs -0 sha256sum) \
  > "$BUNDLE/checksums/checksums.sha256"

tar -C "$(dirname "$BUNDLE")" -czf offline-k8s-lab.tar.gz "$(basename "$BUNDLE")"
~~~

offline-k8s-lab.tar.gz를 대상 서버에 반입한다.

## 4. 폐쇄망 대상 서버: kubeadm Kubernetes 설치

대상 서버에서 artifact를 풀고 먼저 무결성을 확인한다.

~~~bash
tar -xzf offline-k8s-lab.tar.gz
cd offline-k8s-lab
sha256sum -c checksums/checksums.sha256
source versions.env
~~~

### 4.1 containerd, swap, kernel 준비

Kubernetes 1.26 이상은 CRI v1 runtime을 요구한다. Linux containerd 기본 socket은 /run/containerd/containerd.sock이다.

> containerd 재시작은 Docker container에 잠시 영향을 줄 수 있다. 중요한 Docker workload가 있으면 maintenance window에서 진행한다.

~~~bash
sudo test -S /run/containerd/containerd.sock
containerd --version
sudo systemctl status containerd --no-pager

sudo install -d -m 0755 /etc/containerd
sudo cp -a /etc/containerd/config.toml \
  "/etc/containerd/config.toml.before-kubernetes.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
sudo sh -c 'containerd config default > /etc/containerd/config.toml'
sudo rg -n 'disabled_plugins|SystemdCgroup|config_path' /etc/containerd/config.toml
~~~

containerd 1.x에는 다음 설정이 있어야 한다.

~~~toml
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runc.options]
  SystemdCgroup = true

[plugins."io.containerd.grpc.v1.cri".registry]
  config_path = "/etc/containerd/certs.d"
~~~

containerd 2.x의 cgroup 설정 경로는 다음과 다르다.

~~~toml
[plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runc.options]
  SystemdCgroup = true
~~~

disabled_plugins에 cri가 있으면 제거한다. 수정 후 재시작하고 host networking 설정을 적용한다.

~~~bash
sudo systemctl restart containerd
sudo systemctl is-active containerd
sudo ctr version

swapon --show
sudo swapoff -a
sudo cp /etc/fstab "/etc/fstab.before-kubernetes.$(date +%Y%m%d%H%M%S)"
sudo sed -ri '/\sswap\s/s/^#?/#/' /etc/fstab

cat <<'EOF' | sudo tee /etc/modules-load.d/kubernetes.conf
overlay
br_netfilter
EOF
sudo modprobe overlay
sudo modprobe br_netfilter

cat <<'EOF' | sudo tee /etc/sysctl.d/kubernetes.conf
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward = 1
EOF
sudo sysctl --system
swapon --show
~~~

마지막 swapon 출력은 비어 있어야 한다.

### 4.2 반입 deb 설치와 control plane 초기화

~~~bash
sudo apt-get install -y --no-download ./debs/*.deb
sudo apt-mark hold kubelet kubeadm kubectl
sudo systemctl enable --now kubelet

sudo ctr -n k8s.io images import images/kubernetes-bootstrap-images.tar
sudo ctr -n k8s.io images list

SERVER_IP=$(hostname -I | awk '{print $1}')
sudo kubeadm init \
  --kubernetes-version "$KUBERNETES_VERSION" \
  --apiserver-advertise-address "$SERVER_IP" \
  --cri-socket unix:///run/containerd/containerd.sock \
  --pod-network-cidr 10.244.0.0/16
~~~

마지막에 출력되는 kubeadm join 명령은 worker node 추가에 필요하다. token은 cluster join 권한이므로 안전하게 보관한다.

~~~bash
mkdir -p "$HOME/.kube"
sudo cp /etc/kubernetes/admin.conf "$HOME/.kube/config"
sudo chown "$(id -u):$(id -g)" "$HOME/.kube/config"
chmod 600 "$HOME/.kube/config"

kubectl get nodes
kubectl get pods -A
~~~

이때 node NotReady, CoreDNS Pending은 CNI가 없기 때문에 정상이다.

### 4.3 Flannel, local storage, 단일 node scheduling

~~~bash
kubectl apply -f manifests/kube-flannel.yml
kubectl apply -f manifests/local-path-storage.yaml

kubectl -n kube-flannel get pods -w
kubectl -n local-path-storage get pods -w
~~~

Pod가 Running이 되면 Ctrl+C로 watch를 끝낸다. 단일 서버에도 Harbor와 application Pod를 배치하도록 control-plane taint를 제거한다.

~~~bash
kubectl taint nodes --all node-role.kubernetes.io/control-plane-
kubectl patch storageclass local-path \
  -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'

kubectl get nodes -o wide
kubectl get pods -A
kubectl get storageclass
~~~

성공 기준은 node Ready, CoreDNS/Flannel/local-path-provisioner Running, local-path StorageClass 존재다.

## 5. Kubernetes에 Harbor 설치

### 5.1 hostname과 TLS 인증서

모든 cluster node와 Docker 관리 host가 Harbor hostname을 해석해야 한다.

~~~bash
SERVER_IP=$(hostname -I | awk '{print $1}')
echo "$SERVER_IP harbor.algo.local" | sudo tee -a /etc/hosts
getent hosts harbor.algo.local

mkdir -p ~/offline-k8s-lab/certs
cd ~/offline-k8s-lab/certs

openssl genrsa -out harbor-ca.key 4096
openssl req -x509 -new -nodes -key harbor-ca.key -sha256 -days 3650 \
  -subj '/CN=offline-lab-harbor-ca' -out harbor-ca.crt

cat > harbor-server.cnf <<'EOF'
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no
[req_distinguished_name]
CN = harbor.algo.local
[v3_req]
subjectAltName = @alt_names
[alt_names]
DNS.1 = harbor.algo.local
EOF

openssl genrsa -out harbor.key 4096
openssl req -new -key harbor.key -out harbor.csr -config harbor-server.cnf
openssl x509 -req -in harbor.csr -CA harbor-ca.crt -CAkey harbor-ca.key \
  -CAcreateserial -out harbor.crt -days 825 -sha256 \
  -extensions v3_req -extfile harbor-server.cnf
~~~

harbor-ca.key는 CA private key다. 외부에 공유하지 않는다.

### 5.2 Harbor image import와 Helm 설치

~~~bash
cd ~/offline-k8s-lab
kubectl create namespace harbor
kubectl -n harbor create secret tls harbor-tls \
  --cert=certs/harbor.crt \
  --key=certs/harbor.key

sudo ctr -n k8s.io images import images/harbor-images.tar

helm install harbor charts/harbor-$HARBOR_CHART_VERSION.tgz \
  --namespace harbor \
  --values manifests/harbor-values.yaml \
  --wait --timeout 15m

kubectl -n harbor get pods
kubectl -n harbor get pvc
kubectl -n harbor get service
~~~

Harbor 첫 시작은 PVC provisioning과 database migration 때문에 시간이 걸릴 수 있다. Pod가 Running이 아니면 다음 순서로 확인한다.

~~~bash
kubectl -n harbor get events --sort-by=.lastTimestamp
kubectl -n harbor describe pod <POD_NAME>
kubectl -n harbor logs <POD_NAME> --all-containers --tail=100
~~~

### 5.3 Docker와 containerd에 Harbor CA 등록

Docker는 image push에, containerd는 Kubernetes Pod pull에 사용한다. 둘 다 CA를 신뢰해야 한다.

~~~bash
sudo install -d -m 0755 /etc/docker/certs.d/harbor.algo.local:30443
sudo install -m 0644 certs/harbor-ca.crt \
  /etc/docker/certs.d/harbor.algo.local:30443/ca.crt

sudo install -d -m 0755 /etc/containerd/certs.d/harbor.algo.local:30443
sudo install -m 0644 certs/harbor-ca.crt \
  /etc/containerd/certs.d/harbor.algo.local:30443/ca.crt

cat <<'EOF' | sudo tee /etc/containerd/certs.d/harbor.algo.local:30443/hosts.toml
server = "https://harbor.algo.local:30443"

[host."https://harbor.algo.local:30443"]
  capabilities = ["pull", "resolve", "push"]
  ca = "/etc/containerd/certs.d/harbor.algo.local:30443/ca.crt"
EOF

sudo systemctl restart containerd
curl --cacert certs/harbor-ca.crt -I https://harbor.algo.local:30443
~~~

브라우저에서 https://harbor.algo.local:30443를 연다. 초기 계정은 admin, 비밀번호는 Harbor12345다. 로그인 뒤 비밀번호를 바꾼다.

UI에서 private project myapp을 만들고, pull 권한만 가진 robot account를 만든다. application Pod의 image pull Secret에는 admin 계정 대신 이 robot account를 사용한다.

## 6. images.tar를 Harbor에 등록

~~~bash
cd ~/offline-k8s-lab
sudo docker load -i images/images.tar
sudo docker login harbor.algo.local:30443

sudo docker tag offline-fastapi:1.0.0 \
  harbor.algo.local:30443/myapp/offline-fastapi:1.0.0
sudo docker tag postgres:15 \
  harbor.algo.local:30443/myapp/postgres:15

sudo docker push harbor.algo.local:30443/myapp/offline-fastapi:1.0.0
sudo docker push harbor.algo.local:30443/myapp/postgres:15

kubectl create namespace offline-demo
kubectl -n offline-demo create secret docker-registry harbor-myapp-pull \
  --docker-server=harbor.algo.local:30443 \
  --docker-username='<ROBOT_NAME>' \
  --docker-password='<ROBOT_TOKEN>'
~~~

Harbor UI의 myapp project에 FastAPI와 PostgreSQL repository가 보이면 성공이다.

## 7. Helm 전: raw Kubernetes YAML 배포

먼저 YAML을 직접 적용해 Deployment, Service, StatefulSet, PVC, Secret의 역할을 확인한다.

~~~bash
mkdir -p ~/offline-demo-lab/manifests
cd ~/offline-demo-lab/manifests
~~~

~~~yaml
# database-secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: database
  namespace: offline-demo
type: Opaque
stringData:
  POSTGRES_USER: offlineadmin
  POSTGRES_PASSWORD: OfflinePass12345
  POSTGRES_DB: offline_db
~~~

~~~yaml
# postgres.yaml
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: offline-demo
spec:
  clusterIP: None
  selector:
    app: postgres
  ports:
    - name: postgres
      port: 5432
      targetPort: postgres
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: offline-demo
spec:
  serviceName: postgres
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      imagePullSecrets:
        - name: harbor-myapp-pull
      containers:
        - name: postgres
          image: harbor.algo.local:30443/myapp/postgres:15
          ports:
            - name: postgres
              containerPort: 5432
          envFrom:
            - secretRef:
                name: database
          readinessProbe:
            exec:
              command: ["sh", "-c", "pg_isready -U $POSTGRES_USER -d $POSTGRES_DB"]
            initialDelaySeconds: 5
            periodSeconds: 5
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        storageClassName: local-path
        resources:
          requests:
            storage: 5Gi
~~~

~~~yaml
# backend.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
  namespace: offline-demo
spec:
  replicas: 1
  selector:
    matchLabels:
      app: backend
  template:
    metadata:
      labels:
        app: backend
    spec:
      imagePullSecrets:
        - name: harbor-myapp-pull
      initContainers:
        - name: wait-for-postgres
          image: harbor.algo.local:30443/myapp/postgres:15
          command: ["sh", "-c", "until pg_isready -h postgres -U $POSTGRES_USER -d $POSTGRES_DB; do sleep 2; done"]
          envFrom:
            - secretRef:
                name: database
      containers:
        - name: backend
          image: harbor.algo.local:30443/myapp/offline-fastapi:1.0.0
          ports:
            - name: http
              containerPort: 8000
          env:
            - name: DATABASE_URL
              value: postgresql+psycopg://offlineadmin:OfflinePass12345@postgres:5432/offline_db
          readinessProbe:
            httpGet:
              path: /health
              port: http
            initialDelaySeconds: 3
            periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: backend
  namespace: offline-demo
spec:
  type: NodePort
  selector:
    app: backend
  ports:
    - name: http
      port: 8000
      targetPort: http
      nodePort: 30800
~~~

실습 비밀번호는 URL escape 문제가 없는 영숫자 값이다. 실제 비밀번호에 @, :, / 등이 있으면 DATABASE_URL에 URI encoding이 필요하다.

~~~bash
kubectl apply -f database-secret.yaml
kubectl apply -f postgres.yaml
kubectl apply -f backend.yaml

kubectl -n offline-demo rollout status statefulset/postgres --timeout=5m
kubectl -n offline-demo rollout status deployment/backend --timeout=5m
kubectl -n offline-demo get all,pvc

SERVER_IP=$(hostname -I | awk '{print $1}')
curl "http://$SERVER_IP:30800/health"
curl -X POST "http://$SERVER_IP:30800/items?name=offline-test"
curl "http://$SERVER_IP:30800/items"
~~~

PostgreSQL PVC 영속성을 확인한다.

~~~bash
kubectl -n offline-demo delete pod postgres-0
kubectl -n offline-demo wait --for=condition=Ready pod/postgres-0 --timeout=5m
curl "http://$SERVER_IP:30800/items"
~~~

offline-test가 남아 있으면 PVC가 재사용된 것이다.

## 8. Helm Chart로 전환

Helm은 Kubernetes를 대체하지 않는다. 검증한 YAML을 template과 values로 만들고 release history를 관리한다.

먼저 raw resource를 제거한다. PVC는 별도 삭제하지 않으므로 기존 data-postgres-0은 남을 수 있다. 다만 아래 Helm Chart는 release 이름이 포함된 새 StatefulSet/PVC를 만들므로, 이 실습의 Helm 설치는 새 database로 시작한다. raw YAML의 데이터를 Helm으로 이어야 한다면 release 이름과 PVC 이름을 맞추거나 PostgreSQL dump/restore migration을 별도로 설계한다.

~~~bash
kubectl delete -f ~/offline-demo-lab/manifests/backend.yaml
kubectl delete -f ~/offline-demo-lab/manifests/postgres.yaml
kubectl delete -f ~/offline-demo-lab/manifests/database-secret.yaml

mkdir -p ~/offline-demo-lab/helm/offline-demo/templates
cd ~/offline-demo-lab/helm/offline-demo
~~~

~~~yaml
# Chart.yaml
apiVersion: v2
name: offline-demo
description: FastAPI and PostgreSQL offline Kubernetes lab
type: application
version: 0.1.0
appVersion: "1.0.0"
~~~

~~~yaml
# values.yaml
harborPullSecret: harbor-myapp-pull
backend:
  image:
    repository: harbor.algo.local:30443/myapp/offline-fastapi
    tag: "1.0.0"
  nodePort: 30800
postgres:
  image:
    repository: harbor.algo.local:30443/myapp/postgres
    tag: "15"
  storageClass: local-path
  storage: 5Gi
~~~

~~~yaml
# values-lab.yaml - password를 Git에 commit하지 않는다.
database:
  user: offlineadmin
  password: OfflinePass12345
  name: offline_db
~~~

~~~gotemplate
{{/* templates/_helpers.tpl */}}
{{- define "offline-demo.fullname" -}}
{{- printf "%s-offline-demo" .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
~~~

~~~yaml
# templates/secrets.yaml
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "offline-demo.fullname" . }}-database
type: Opaque
stringData:
  POSTGRES_USER: {{ required "database.user is required" .Values.database.user | quote }}
  POSTGRES_PASSWORD: {{ required "database.password is required" .Values.database.password | quote }}
  POSTGRES_DB: {{ required "database.name is required" .Values.database.name | quote }}
~~~

다음 두 template을 그대로 만든다. Secret 값은 values-lab.yaml에서만 제공한다.

~~~yaml
# templates/postgres.yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ include "offline-demo.fullname" . }}-postgres
spec:
  clusterIP: None
  selector:
    app: {{ include "offline-demo.fullname" . }}-postgres
  ports:
    - name: postgres
      port: 5432
      targetPort: postgres
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {{ include "offline-demo.fullname" . }}-postgres
spec:
  serviceName: {{ include "offline-demo.fullname" . }}-postgres
  replicas: 1
  selector:
    matchLabels:
      app: {{ include "offline-demo.fullname" . }}-postgres
  template:
    metadata:
      labels:
        app: {{ include "offline-demo.fullname" . }}-postgres
    spec:
      imagePullSecrets:
        - name: {{ .Values.harborPullSecret }}
      containers:
        - name: postgres
          image: "{{ .Values.postgres.image.repository }}:{{ .Values.postgres.image.tag }}"
          envFrom:
            - secretRef:
                name: {{ include "offline-demo.fullname" . }}-database
          readinessProbe:
            exec:
              command: ["sh", "-c", "pg_isready -U $POSTGRES_USER -d $POSTGRES_DB"]
            initialDelaySeconds: 5
            periodSeconds: 5
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        storageClassName: {{ .Values.postgres.storageClass }}
        resources:
          requests:
            storage: {{ .Values.postgres.storage }}
~~~

~~~yaml
# templates/backend.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "offline-demo.fullname" . }}-backend
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {{ include "offline-demo.fullname" . }}-backend
  template:
    metadata:
      labels:
        app: {{ include "offline-demo.fullname" . }}-backend
    spec:
      imagePullSecrets:
        - name: {{ .Values.harborPullSecret }}
      initContainers:
        - name: wait-for-postgres
          image: "{{ .Values.postgres.image.repository }}:{{ .Values.postgres.image.tag }}"
          command: ['sh', '-c', 'until pg_isready -h {{ include "offline-demo.fullname" . }}-postgres -U $POSTGRES_USER -d $POSTGRES_DB; do sleep 2; done']
          envFrom:
            - secretRef:
                name: {{ include "offline-demo.fullname" . }}-database
      containers:
        - name: backend
          image: "{{ .Values.backend.image.repository }}:{{ .Values.backend.image.tag }}"
          ports:
            - name: http
              containerPort: 8000
          env:
            - name: DATABASE_URL
              value: 'postgresql+psycopg://{{ .Values.database.user }}:{{ .Values.database.password }}@{{ include "offline-demo.fullname" . }}-postgres:5432/{{ .Values.database.name }}'
          readinessProbe:
            httpGet:
              path: /health
              port: http
            initialDelaySeconds: 3
            periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: {{ include "offline-demo.fullname" . }}-backend
spec:
  type: NodePort
  selector:
    app: {{ include "offline-demo.fullname" . }}-backend
  ports:
    - name: http
      port: 8000
      targetPort: http
      nodePort: {{ .Values.backend.nodePort }}
~~~

먼저 lint와 render를 수행한다.

~~~bash
helm lint . --values values-lab.yaml
helm template offline-demo . --namespace offline-demo --values values-lab.yaml > rendered.yaml
kubectl apply --dry-run=client -f rendered.yaml

helm install offline-demo . \
  --namespace offline-demo \
  --create-namespace \
  --values values-lab.yaml \
  --wait --timeout 5m

helm list -n offline-demo
kubectl -n offline-demo get all,pvc
~~~

upgrade와 rollback을 확인하려고 같은 FastAPI image에 실습용 tag를 push한다.

~~~bash
sudo docker tag harbor.algo.local:30443/myapp/offline-fastapi:1.0.0 \
  harbor.algo.local:30443/myapp/offline-fastapi:1.0.1
sudo docker push harbor.algo.local:30443/myapp/offline-fastapi:1.0.1

helm upgrade offline-demo . \
  --namespace offline-demo \
  --values values-lab.yaml \
  --set backend.image.tag=1.0.1 \
  --wait --rollback-on-failure --timeout 5m

helm history offline-demo -n offline-demo
helm rollback offline-demo 1 --namespace offline-demo --wait --timeout 5m
helm history offline-demo -n offline-demo
~~~

history에는 install revision 1, upgrade revision 2, rollback revision 3이 보인다. Harbor 자체 upgrade는 database migration을 수반할 수 있으므로 application release처럼 무조건 rollback하면 안 된다.

## 9. 문제 해결

| 증상 | 먼저 실행할 명령 | 흔한 원인 |
| --- | --- | --- |
| kubeadm init swap 오류 | swapon --show | swap 비활성화 실패 |
| node NotReady / CoreDNS Pending | kubectl -n kube-system get pods | Flannel 미설치 또는 image 없음 |
| Flannel CrashLoop | kubectl -n kube-flannel logs POD | Pod CIDR, kernel module, sysctl 오류 |
| PVC Pending | kubectl describe pvc NAME | local-path-provisioner/StorageClass 문제 |
| x509 unknown authority | containerd certs.d 확인 | CA 경로, hostname, restart 누락 |
| ImagePullBackOff | kubectl describe pod POD | image tag, robot 권한, pull Secret |
| Harbor Pod Pending | kubectl -n harbor get pvc | local-path provisioning 실패 |
| Helm render 오류 | helm lint, helm template | template 들여쓰기 또는 value 누락 |

진단은 host → cluster → storage → registry → application → release 순서로 한다.

## 10. 완료 체크리스트

- [ ] 기존 k3s resource와 PVC 상태를 기록하고 제거했다.
- [ ] 대상 서버에서 외부 APT, Docker Hub, Helm repository에 접근하지 않았다.
- [ ] 반입 artifact checksum을 검증했다.
- [ ] kubeadm node, CoreDNS, Flannel, local-path-provisioner가 Ready다.
- [ ] Harbor가 https://harbor.algo.local:30443에서 TLS로 열린다.
- [ ] Docker와 containerd가 Harbor CA를 신뢰한다.
- [ ] myapp private project에 두 application image가 있다.
- [ ] raw YAML로 health, item 생성, item 조회가 된다.
- [ ] PostgreSQL Pod 재생성 뒤에도 item이 남는다.
- [ ] Helm lint/template/install/upgrade/rollback을 수행했다.

## 공식 문서

- [k3s Uninstall](https://docs.k3s.io/installation/uninstall)
- [Installing kubeadm](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/install-kubeadm/)
- [Creating a cluster with kubeadm](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/)
- [Container runtimes](https://kubernetes.io/docs/setup/production-environment/container-runtimes/)
- [Flannel](https://github.com/flannel-io/flannel)
- [local-path-provisioner](https://github.com/rancher/local-path-provisioner)
- [Harbor Helm deployment](https://goharbor.io/docs/main/install-config/harbor-ha-helm/)
- [Harbor Chart values](https://github.com/goharbor/harbor-helm/blob/main/values.yaml)
- [Helm commands](https://helm.sh/docs/helm/)
