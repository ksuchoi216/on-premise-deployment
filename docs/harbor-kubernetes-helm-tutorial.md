# kubeadm 클러스터 구성 → Harbor → 매니페스트 기반 온프레미스 배포

[공식 kubeadm 설치 가이드](https://kubernetes.io/ko/docs/setup/production-environment/tools/kubeadm/install-kubeadm/)로 **kubelet, kubeadm, kubectl을 설치한 다음 단계**부터 진행한다. [공식 클러스터 구성 가이드](https://kubernetes.io/ko/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/)의 순서에 맞춰 클러스터를 만들고, Harbor에 이미지를 등록한 뒤 FastAPI/PostgreSQL을 YAML로 배포한다.

온프레미스는 자체 서버에 배포한다는 뜻이며 반드시 폐쇄망을 뜻하지 않는다. 본문은 설치 시 외부 저장소에 접근할 수 있는 서버를 기준으로 한다. 외부 접속이 차단된 경우에는 **부록 A의 사전 반입을 먼저 수행**하고 본문의 다운로드 대신 반입 파일을 사용한다. 이미 설치한 Kubernetes 패키지를 다시 다운로드하거나 k3s를 삭제하는 절차는 필수가 아니다.

Harbor는 공식 Chart를 `helm template`으로 YAML로 변환한 뒤 `kubectl apply`로 설치한다. Helm은 매니페스트 생성에만 사용하며, 애플리케이션은 직접 작성한 Deployment/StatefulSet/Service/Secret 매니페스트로 관리한다. 파일명은 기존 링크 호환성을 위해 유지한다.

## 1. 설치 상태 확인

### 1.1 이 작업 환경에서 확인한 결과 (2026-09-14)

| 항목 | 관찰 결과 | 판단 / 다음 작업 |
| --- | --- | --- |
| OS | Ubuntu 20.04.6 LTS | 운영 OS 지원과 runtime 호환성은 별도 검토 |
| kubeadm / kubelet / kubectl | 모두 `/usr/bin`, v1.37.0, 패키지 1.37.0-1.1 | 세 도구 설치 및 실행 확인 |
| 패키지 버전 고정 | 세 패키지 모두 hold | 자동 변경 방지 설정 확인 |
| kubelet | enabled, inactive (dead) | 서비스 시작 및 init 후 상태 확인 필요 |
| containerd | 1.7.24, active | 프로세스는 실행 중 |
| containerd 설정 | `disabled_plugins = ["cri"]` | Kubernetes용 CRI 활성화 필요 |
| swap | `/dev/dm-1`, 976 MiB 활성 | 본 실습에서는 비활성화 필요 |
| 현재 사용자 kubectl | kubeconfig 디렉터리 없음, localhost:8080 연결 거부 | 클러스터 접속 설정 없음; 클러스터 존재 여부 자체는 단정 불가 |
| CRI 실제 응답 | sudo 암호 필요로 미확인 | 관리자 터미널에서 아래 crictl 실행 필요 |

**바이너리 설치는 확인했지만 클러스터 실행 준비는 완료되지 않았다.** 이번 문서 수정 과정에서는 서비스 재시작, swap 변경, `kubeadm init`을 실행하지 않았다.

### 1.2 각 노드에서 재확인할 명령

아래 명령 예시는 Bash 기준이다. worker에서도 패키지/runtime/네트워크 준비를 반복한다.

~~~bash
command -v kubeadm kubelet kubectl
kubeadm version -o short
kubelet --version
kubectl version --client
dpkg-query -W kubeadm kubelet kubectl
apt-mark showhold
systemctl is-active kubelet containerd
sudo journalctl -u kubelet -n 50 --no-pager
sudo crictl --runtime-endpoint unix:///run/containerd/containerd.sock info
swapon --show
sudo ls -la /etc/kubernetes/manifests
sudo test -f /etc/kubernetes/admin.conf && echo '기존 클러스터 설정 발견'
~~~

`kubectl version --client`는 클러스터 연결을 검사하지 않는다. `kubeadm init` 전 kubelet은 설정을 기다리며 재시작할 수 있으므로 이 현상만으로 패키지 설치 실패라고 판단하지 않는다. 다만 현재 관찰된 `inactive`는 실행 중 상태가 아니다. 기존 admin.conf 또는 static Pod 파일이 있으면 기존 클러스터 상태부터 확인하고 init을 반복하지 않는다.

세 도구가 없거나 버전이 다른 경우에만 공식 설치 가이드의 **같은 minor 버전 저장소**를 사용해 원하는 패키지 버전을 선택한다. 현재 설치는 재설치할 필요가 없다.

~~~bash
# 복구가 필요한 경우에만: 저장소 설정 후 제공 버전을 먼저 확인
apt-cache madison kubeadm kubelet kubectl
# 아래 값은 현재 설치된 버전이며 저장소에 실제 존재하는지 먼저 확인한다.
PACKAGE_VERSION='1.37.0-1.1'
sudo apt-mark unhold kubeadm kubelet kubectl
sudo apt-get install -y kubeadm="$PACKAGE_VERSION" kubelet="$PACKAGE_VERSION" kubectl="$PACKAGE_VERSION"
sudo apt-mark hold kubeadm kubelet kubectl
~~~

## 2. 클러스터 사전 준비 (모든 노드)

### 2.1 토폴로지와 네트워크

현재 서버를 중앙 control-plane으로 사용하고, 추가 Linux PC를 worker로 연결한다. control-plane의 기본 NoSchedule taint를 유지하고 Harbor/PostgreSQL/FastAPI는 worker에 배치한다. 최소 한 대의 worker가 Ready가 된 뒤 storage와 Harbor 설치를 진행한다. PC가 Windows라면 이 Linux/containerd/Flannel 절차를 그대로 적용할 수 없으므로 Linux 설치 또는 브리지 네트워크의 Linux VM을 준비한다. local-path는 노드 로컬 디스크이므로 노드 장애 시 데이터 가용성을 제공하지 않는다. 운영에는 HA control-plane, CSI 스토리지, 백업/복구, 조직 DNS/CA가 필요하다.

구체적인 서버 역할은 다음과 같다. SSH alias는 접속 편의 기능이며 Kubernetes 노드 이름을 설정하지 않는다. 두 서버의 실제 hostname이 서로 다른지 확인한다.

| 역할 | 접속 | IP | 실행할 작업 |
| --- | --- | --- | --- |
| control-plane | `ssh99` 또는 `ssh ksuchoi216@192.168.0.99` | 192.168.0.99 | init, kubeconfig, CNI/스토리지/Harbor/앱 apply |
| worker | `ssh98` 또는 `ssh ksuchoi216@192.168.0.98` | 192.168.0.98 | 패키지/runtime 준비, join, Harbor DNS/CA 등록 |

192.168.0.98의 설치/서비스 상태는 아직 점검하지 않았다. 1절의 실제 점검 결과는 현재 접속한 192.168.0.99에 대한 것이다. 아래 1–2절 명령은 `ssh98`로 접속한 터미널에서도 실행하되 `kubeadm init`은 192.168.0.99에서만 실행한다.

각 노드의 hostname/MAC/product UUID는 고유해야 한다. 고정 IP, 시간 동기화, CPU/메모리/디스크 여유를 확인한다. 특히 Harbor와 DB를 함께 실행할 자원과 이미지/PVC 저장 공간이 필요하다.

| 통신 | 필요한 범위 |
| --- | --- |
| Kubernetes API TCP 6443 | 관리 PC와 노드 → control-plane |
| etcd TCP 2379–2380 | control-plane 내부/상호 통신 |
| kubelet TCP 10250 | control-plane → 노드 |
| controller/scheduler TCP 10257/10259 | control-plane 내부 |
| Flannel VXLAN UDP 8472 | 노드 사이, 외부 공개 금지 |
| Harbor TCP 30443 / 앱 TCP 30800 | 허용된 사내 클라이언트 → 노드 |

Pod CIDR `10.244.0.0/16`, Service CIDR `10.96.0.0/12`가 사내 LAN/VPN과 겹치지 않는지 확인한다. 겹치면 init 설정과 Flannel의 Network를 함께 변경한다. 방화벽 전체를 끄지 않고 필요한 통신만 허용한다.

~~~bash
ip -br address
ip route
hostnamectl
free -h
df -h
# 실제 control-plane의 고정 LAN IP로 반드시 바꾼다.
export SERVER_IP='192.168.0.99'
export LAB="$HOME/kubeadm-onprem-lab"
mkdir -p "$LAB"/{manifests,charts,images,certs,app}
export KUBERNETES_VERSION="$(kubeadm version -o short)"
export FLANNEL_VERSION='v0.27.4'
export LOCAL_PATH_VERSION='v0.0.36'
export HARBOR_CHART_VERSION='1.18.0'
export HARBOR_HOST='harbor.algo.local'
export HARBOR_HTTPS_NODEPORT='30443'
cd "$LAB"
~~~

변수는 같은 Bash 세션에서 사용한다. 새 터미널에서는 다시 설정한다. `k8s/versions.env`는 기존 준비 자료이며 이 문서는 실제 설치된 kubeadm 버전을 기준으로 한다. 애드온/Chart 버전은 고정 예시로, 사용 중인 Kubernetes와의 조합은 실제 배포 검증이 필요하다.

### 2.2 containerd, swap, kernel 준비

Kubernetes 1.26 이상은 CRI v1 runtime을 요구한다. Linux containerd 기본 socket은 /run/containerd/containerd.sock이다.

> containerd 재시작은 Docker container에 잠시 영향을 줄 수 있다. 중요한 Docker workload가 있으면 maintenance window에서 진행한다.

~~~bash
sudo test -S /run/containerd/containerd.sock
containerd --version
sudo systemctl status containerd --no-pager

sudo install -d -m 0755 /etc/containerd
sudo cp -a /etc/containerd/config.toml \
  "/etc/containerd/config.toml.before-kubernetes.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
containerd config default > /tmp/containerd-default.toml
# 기본 설정은 참고용이다. 기존 config.toml의 필요한 항목만 sudoedit로 수정한다.
sudoedit /etc/containerd/config.toml
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

현재 서버는 containerd 1.7.24이며 `disabled_plugins = ["cri"]`가 설정되어 있다. 이를 `disabled_plugins = []`로 바꾸고 위 1.x 테이블을 추가한다. 2.x에서는 registry 경로도 `[plugins."io.containerd.cri.v1.images".registry]`로 바뀌므로 해당 버전 설정을 사용한다. 수정 후 재시작하고 host networking 설정을 적용한다.

~~~bash
sudo systemctl restart containerd
sudo systemctl is-active containerd
sudo ctr version
sudo crictl --runtime-endpoint unix:///run/containerd/containerd.sock info

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


## 3. kubeadm으로 클러스터 생성

### 3.1 control-plane에서 한 번만 초기화

CRI 응답이 정상이고 swap이 꺼진 뒤 실행한다. Kubernetes 패키지 설치와 control-plane 컨테이너 이미지 준비는 별개다. `images pull`은 Kubernetes 구성 요소 이미지를 runtime에 받는 명령이다.

~~~bash
sudo systemctl enable --now kubelet
kubeadm config images list --kubernetes-version "$KUBERNETES_VERSION"
# 인터넷 접근 가능할 때만. 폐쇄망은 부록 A의 image import로 대체한다.
sudo kubeadm config images pull \
  --kubernetes-version "$KUBERNETES_VERSION" \
  --cri-socket unix:///run/containerd/containerd.sock

sudo kubeadm init \
  --kubernetes-version "$KUBERNETES_VERSION" \
  --apiserver-advertise-address "$SERVER_IP" \
  --cri-socket unix:///run/containerd/containerd.sock \
  --pod-network-cidr 10.244.0.0/16 \
  --service-cidr 10.96.0.0/12

mkdir -p "$HOME/.kube"
sudo cp -i /etc/kubernetes/admin.conf "$HOME/.kube/config"
sudo chown "$(id -u):$(id -g)" "$HOME/.kube/config"
chmod 600 "$HOME/.kube/config"
kubectl get nodes -o wide
~~~

HA로 확장할 계획이면 첫 init부터 `--control-plane-endpoint <공유-DNS>:6443`를 추가하고 접근 가능한 공유 엔드포인트/LB를 먼저 준비한다. admin.conf는 관리자 인증 정보다. 기존 kubeconfig를 덮어쓰지 않도록 확인한다.

### 3.2 CNI 설치

CNI는 한 종류만 설치한다. init 직후 NotReady/CoreDNS Pending이면 네트워크 설치를 진행한다.

~~~bash
cd "$LAB"
curl -fL -o manifests/kube-flannel.yml \
  "https://raw.githubusercontent.com/flannel-io/flannel/$FLANNEL_VERSION/Documentation/kube-flannel.yml"
kubectl apply -f manifests/kube-flannel.yml
~~~

### 3.3 추가 PC를 worker로 연결

추가 PC마다 고유한 hostname과 고정 IP를 설정하고 1–2절을 수행한 뒤 init이 출력한 join 명령을 실행한다. 토큰이 만료되었으면 control-plane에서 다음 명령으로 새 join 명령을 받는다. 출력되는 토큰은 공유하지 않는다.

~~~bash
# control-plane
sudo kubeadm token create --print-join-command
# worker: 실제 출력값으로 치환해 실행
sudo kubeadm join 192.168.0.99:6443 --token <TOKEN> \
  --discovery-token-ca-cert-hash sha256:<CA_HASH> \
  --cri-socket unix:///run/containerd/containerd.sock
~~~

현재 구성에서는 control-plane taint를 제거하지 않는다. join 명령은 추가 PC에서, 이후 kubectl 명령은 control-plane의 일반 사용자 계정에서 실행한다. worker의 ROLES 열이 `<none>`이어도 정상이며 Ready 상태로 판단한다. 추가 PC에도 swap/CRI/cgroup/CNI 사전 준비와 Harbor DNS/CA 등록이 필요하다.

~~~bash
kubectl -n kube-flannel rollout status daemonset/kube-flannel-ds --timeout=5m
kubectl wait --for=condition=Ready nodes --all --timeout=5m
kubectl -n kube-system rollout status deployment/coredns --timeout=5m
kubectl get nodes -o wide
kubectl get pods -A
~~~

### 3.4 PVC용 StorageClass 설치

~~~bash
cd "$LAB"
curl -fL -o manifests/local-path-storage.yaml \
  "https://raw.githubusercontent.com/rancher/local-path-provisioner/$LOCAL_PATH_VERSION/deploy/local-path-storage.yaml"
# helper Pod 이미지도 고정한다.
sed -i 's|image: docker.io/library/busybox$|image: docker.io/library/busybox:1.37.0|' manifests/local-path-storage.yaml
kubectl apply -f manifests/local-path-storage.yaml
kubectl -n local-path-storage rollout status deployment/local-path-provisioner --timeout=5m
kubectl get storageclass
~~~

모든 PVC에 `storageClassName: local-path`를 명시하므로 기본 StorageClass 변경은 필요 없다. `WaitForFirstConsumer` 방식에서는 사용하는 Pod가 생기기 전 PVC Pending이 정상이다. 기본 reclaimPolicy는 Delete이므로 PVC를 지우면 데이터도 삭제될 수 있다. [local-path 공식 매니페스트](https://github.com/rancher/local-path-provisioner/blob/v0.0.36/deploy/local-path-storage.yaml)를 참고한다.

## 4. Harbor 매니페스트 준비

인터넷 연결 준비 환경에서 다음을 실행한다. 폐쇄망이면 생성된 Chart/YAML과 이미지를 반입한다. Harbor 자체 이미지와 클러스터 bootstrap 이미지는 아직 존재하지 않는 Harbor에 의존해서는 안 된다.

~~~bash
cd "$LAB"
helm repo add harbor https://helm.goharbor.io
helm repo update
helm pull harbor/harbor --version "$HARBOR_CHART_VERSION" --destination charts

cat > manifests/harbor-values.yaml <<EOF
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
    database:
      storageClass: local-path
    redis:
      storageClass: local-path
updateStrategy:
  type: Recreate
trivy:
  enabled: false
existingSecretAdminPassword: harbor-admin
existingSecretAdminPasswordKey: HARBOR_ADMIN_PASSWORD
EOF

umask 077
helm template harbor "charts/harbor-$HARBOR_CHART_VERSION.tgz" \
  --namespace harbor --values manifests/harbor-values.yaml \
  > manifests/harbor-rendered.yaml
~~~

[Harbor Chart 1.18.0 설정](https://github.com/goharbor/harbor-helm/blob/v1.18.0/values.yaml)에 맞춘 예시다. 생성 YAML에는 Secret이 포함되므로 Git에 올리지 않는다. 첫 생성 파일을 안전하게 보존해 재사용한다. 재렌더링하면 자동 생성된 자격 증명이 달라질 수 있다. 이 방식에는 Helm release 이력/rollback/hook 실행이 없으므로 업그레이드는 별도 검토한다. Trivy는 이 실습에서 비활성화한다.

## 5. Kubernetes에 Harbor 설치

### 5.1 hostname과 TLS 인증서

모든 cluster node와 Docker 관리 host가 Harbor hostname을 해석해야 한다. 아래 `SERVER_IP`는 현재 control-plane의 고정 IP다. NodePort와 kube-proxy가 정상인 이 구성에서는 control-plane IP로 들어온 요청도 worker의 Harbor Pod로 전달된다. 방화벽에서 30443 접근을 허용한다.

~~~bash

echo "$SERVER_IP $HARBOR_HOST" | sudo tee -a /etc/hosts
getent hosts harbor.algo.local

cd "$LAB/certs"
umask 077

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

### 5.2 Secret 생성 및 YAML 적용

~~~bash
cd "$LAB"
kubectl create namespace harbor --dry-run=client -o yaml | kubectl apply -f -
kubectl -n harbor create secret tls harbor-tls \
  --cert=certs/harbor.crt --key=certs/harbor.key \
  --dry-run=client -o yaml | kubectl apply -f -
read -r -s -p 'Harbor 초기 관리자 비밀번호: ' HARBOR_ADMIN_PASSWORD
printf '\n'
kubectl -n harbor create secret generic harbor-admin \
  --from-literal=HARBOR_ADMIN_PASSWORD="$HARBOR_ADMIN_PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -
unset HARBOR_ADMIN_PASSWORD

kubectl apply -f manifests/harbor-rendered.yaml
for resource in $(kubectl -n harbor get deployment,statefulset -o name); do
  kubectl -n harbor rollout status "$resource" --timeout=15m || break
done
kubectl -n harbor get pods,pvc,service
~~~

Harbor 첫 시작은 PVC provisioning과 database migration 때문에 시간이 걸릴 수 있다. Pod가 Running이 아니면 다음 순서로 확인한다.

~~~bash
kubectl -n harbor get events --sort-by=.lastTimestamp
kubectl -n harbor describe pod <POD_NAME>
kubectl -n harbor logs <POD_NAME> --all-containers --tail=100
~~~

### 5.3 Docker와 containerd에 Harbor CA 등록

Docker는 image push에, containerd는 Kubernetes Pod pull에 사용한다. Docker 관리 호스트와 **모든 Kubernetes 노드**에 각각 CA를 등록한다. 앞서 registry.config_path를 설정했다면 certs.d 파일 변경은 containerd 재시작 없이 반영된다. DNS/hosts도 각 호스트에 설정한다. 브라우저를 사용하는 PC에는 CA 인증서를 신뢰 저장소에 등록한다.

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

curl --cacert certs/harbor-ca.crt -I https://harbor.algo.local:30443
~~~

브라우저에서 https://harbor.algo.local:30443를 연다. 초기 계정은 admin, 비밀번호는 앞에서 입력한 값이다. 로그인 뒤 비밀번호를 바꾼다.

UI에서 private project myapp을 만들고, pull 권한만 가진 robot account를 만든다. application Pod의 image pull Secret에는 admin 계정 대신 이 robot account를 사용한다.

## 6. images.tar를 Harbor에 등록

~~~bash
cd "$LAB"
# Part 1/2에서 만든 images.tar를 먼저 $LAB/images/images.tar에 복사한다.
sudo docker load -i images/images.tar
sudo docker login harbor.algo.local:30443

sudo docker tag offline-fastapi:1.0.0 \
  harbor.algo.local:30443/myapp/offline-fastapi:1.0.0
sudo docker tag postgres:15 \
  harbor.algo.local:30443/myapp/postgres:15

sudo docker push harbor.algo.local:30443/myapp/offline-fastapi:1.0.0
sudo docker push harbor.algo.local:30443/myapp/postgres:15

kubectl create namespace offline-demo --dry-run=client -o yaml | kubectl apply -f -
kubectl -n offline-demo create secret docker-registry harbor-myapp-pull \
  --docker-server=harbor.algo.local:30443 \
  --docker-username='<ROBOT_NAME>' \
  --docker-password='<ROBOT_TOKEN>'
~~~

Harbor UI의 myapp project에 FastAPI와 PostgreSQL repository가 보이면 성공이다.

## 7. Kubernetes 매니페스트(YAML) 애플리케이션 배포

직접 YAML 매니페스트를 작성하여 Deployment, Service, StatefulSet, PVC, Secret 등을 통해 애플리케이션을 배포한다.

~~~bash
cd "$LAB/app"
~~~

각 YAML 블록을 주석의 파일명으로 저장한다. Secret 파일은 커밋하지 않는다. 예시 비밀번호는 배포 전에 바꾸고 DB와 URL의 값을 일치시킨다.

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
  DATABASE_URL: postgresql+psycopg://offlineadmin:OfflinePass12345@postgres:5432/offline_db
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
              valueFrom:
                secretKeyRef:
                  name: database
                  key: DATABASE_URL
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

curl "http://$SERVER_IP:30800/health"
curl -X POST "http://$SERVER_IP:30800/items?name=offline-test"
curl "http://$SERVER_IP:30800/items"
~~~

PostgreSQL 초기화 변수는 빈 데이터 디렉터리에서만 적용된다. 기존 PVC의 DB 비밀번호는 Secret 수정만으로 바뀌지 않는다.

PostgreSQL PVC 영속성을 확인한다. Pod 삭제는 DB 연결을 잠시 끊으므로 실습에서 실행한다.

~~~bash
kubectl -n offline-demo delete pod postgres-0
kubectl -n offline-demo rollout status statefulset/postgres --timeout=5m
curl "http://$SERVER_IP:30800/items"
~~~

offline-test가 남아 있으면 PVC가 재사용된 것이다.

## 8. 운영 확인과 문제 해결

~~~bash
kubectl get nodes -o wide
kubectl get pods -A -o wide
kubectl -n offline-demo get pvc
kubectl -n offline-demo get events --sort-by=.lastTimestamp
# 실제 Pod 이름으로 치환
kubectl -n offline-demo describe pod <POD_NAME>
kubectl -n offline-demo logs <POD_NAME> --all-containers --tail=100
~~~

| 증상 | 점검할 내용 |
| --- | --- |
| kubelet inactive / init 실패 | journalctl, swap, CRI 활성화, systemd cgroup 설정 |
| localhost:8080 연결 거부 | 현재 사용자 kubeconfig와 context; 패키지 재설치로 해결되지 않음 |
| worker join 실패 | API 6443 접근, token 만료, CA hash, hostname 중복, runtime |
| NotReady / CoreDNS Pending | CNI 이미지, Pod CIDR, Flannel UDP 8472, 노드 간 통신 |
| 모든 업무 Pod Pending | Ready worker 존재 여부, control-plane taint, 자원 부족 |
| PVC Pending | StorageClass, WaitForFirstConsumer, helper 이미지, 노드 디스크 |
| ImagePullBackOff | 각 노드 DNS/CA, Harbor 주소, image tag, robot 권한, Secret namespace |
| Harbor x509 오류 | 인증서 SAN과 접속 이름, containerd registry.config_path, CA 경로 |
| 앱 DB 접속 오류 | database Secret과 기존 PVC의 실제 DB 계정 일치 여부 |

배포 성공 기준은 모든 노드 Ready, CNI/CoreDNS 정상, Harbor Pod 준비 및 PVC Bound, 앱 rollout 완료, `/health` 응답과 item 생성/조회 성공이다. Pod 재생성 후 item이 유지되는지도 확인한다.

현재 구성은 control-plane 하나이므로 그 서버가 중단되면 클러스터 관리가 중단된다. local-path 데이터는 해당 worker에 귀속되므로 다른 PC로 Pod를 옮겨도 데이터가 자동 복제되지 않는다. 운영 전 etcd/DB/Harbor 데이터 백업과 복원, 인증서 갱신, 자원 제한, 접근 제어를 준비한다. 기본 Flannel 구성만으로 NetworkPolicy를 집행할 수 있다고 가정하지 않는다.

## 부록 A. 인터넷이 차단된 경우에만: 이미지/매니페스트 반입

본문 3절 전에 준비한다. 준비 서버는 대상과 동일한 CPU 아키텍처를 사용하고 같은 kubeadm 버전 및 본문의 변수를 설정한다. 본문 3.2/3.4의 파일 다운로드와 helper 태그 고정, 4절의 Chart 렌더링을 **준비 서버에서** 먼저 실행하되 `kubectl apply`는 대상 클러스터에서 수행한다. 다운로드 명령과 적용 명령은 나눠 실행한다.

~~~bash
cd "$LAB"
kubeadm config images list --kubernetes-version "$KUBERNETES_VERSION" > images/kubeadm-images.txt
# .yml, .yaml 모두 포함. local-path ConfigMap 안의 helper 이미지도 포함된다.
awk '/^[[:space:]]*image:/{gsub(/"/, "", $2); print $2}' \
  manifests/kube-flannel.yml manifests/local-path-storage.yaml manifests/harbor-rendered.yaml \
  > images/addon-harbor-images.txt
cat images/kubeadm-images.txt images/addon-harbor-images.txt | sort -u > images/all-images.txt
while IFS= read -r image; do
  sudo docker pull "$image" || exit 1
done < images/all-images.txt
mapfile -t IMAGES < images/all-images.txt
sudo docker save -o images/bootstrap.tar "${IMAGES[@]}"
sha256sum images/bootstrap.tar > images/bootstrap.tar.sha256
~~~

containerd의 sandbox(pause) 이미지 참조도 확인한다. `kubeadm config images list`의 pause와 runtime의 sandbox_image가 다르면 버전을 일치시키거나 runtime이 요구하는 이미지도 추가로 반입한다. CNI 실행 바이너리(`/opt/cni/bin`)와 CRI runtime은 이미지 tar와 별개이며 각 worker에 설치되어 있어야 한다.

`$LAB`의 manifests/charts/images와 checksum을 대상에 복사한다. 준비 단계에서 생성한 Secret 포함 YAML은 안전하게 전달한다. 대상에서는 다음을 **모든 노드에서** 실행한 뒤 본문의 로컬 파일 apply 절차를 진행한다.

~~~bash
cd "$LAB"
sha256sum -c images/bootstrap.tar.sha256
sudo ctr -n k8s.io images import images/bootstrap.tar
sudo ctr -n k8s.io images list
~~~

Docker load는 Kubernetes의 containerd 이미지 저장소를 채우지 않는다. `ctr -n k8s.io`로 가져와야 한다. 이미지가 들어온 뒤에는 `kubeadm config images pull` 및 외부 curl/helm repo 명령을 생략한다. 추가 PC도 join **전에** bootstrap/CNI 이미지를 반입한다. Harbor 장애 시 자체 이미지를 다시 받을 수 있도록 이 파일을 보관한다.

현재 서버의 세 패키지는 이미 설치되어 있다. 신규 폐쇄망 worker에는 동일 OS/아키텍처용 kubelet/kubeadm 및 containerd, CNI 바이너리, cri-tools와 모든 의존 패키지를 따로 반입해야 한다. 패키지 다운로드 캐시는 준비 서버에 이미 설치된 의존성을 누락할 수 있으므로 네트워크를 끈 동일 OS VM에서 설치를 검증한다. kubectl은 관리 PC/control-plane에 필요하며 worker 관리에는 필수가 아니다.

## 참고 문서

- [kubeadm 설치하기](https://kubernetes.io/ko/docs/setup/production-environment/tools/kubeadm/install-kubeadm/)
- [kubeadm으로 클러스터 생성하기](https://kubernetes.io/ko/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/)
- [컨테이너 런타임과 cgroup 설정](https://kubernetes.io/docs/setup/production-environment/container-runtimes/)
- [Flannel](https://github.com/flannel-io/flannel)
- [Harbor Chart](https://github.com/goharbor/harbor-helm)
