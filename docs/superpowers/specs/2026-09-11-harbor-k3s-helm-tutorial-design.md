# Harbor, kubeadm Kubernetes, Helm 오프라인 실습 튜토리얼 설계

## 목적

기존 `docs/offline_part1.md`와 `docs/offline_part2.md`에서 만든 `offline-fastapi:1.0.0`, `postgres:15`, 그리고 `images.tar`을 바탕으로, 초심자가 현재 서버의 기존 k3s를 삭제하고 kubeadm 방식의 표준 Kubernetes를 설치한 뒤 Harbor, Kubernetes YAML, Helm Chart를 순서대로 운영하는 전 과정을 한 문서에서 재현할 수 있게 한다.

## 대상 환경과 전제

문서는 2026-09-11에 점검한 다음 환경을 기준으로 한다.

- Ubuntu 20.04.6 LTS, x86_64, 40 vCPU, 사용 가능 디스크 약 52 GiB
- Docker 26.1.3 및 Docker Compose v2.18.1
- 기존 k3s v1.34.5+k3s1과 Helm v3.20.1이 설치되어 있으나, 이 튜토리얼은 k3s를 제거한다
- kubeadm, kubelet, kubectl, containerd, Flannel CNI, local-path-provisioner를 설치하는 단일 control-plane 노드

실습 전용 환경이다. Harbor와 PostgreSQL의 PVC는 단일 노드 로컬 디스크에 생성되므로 노드 장애를 견디지 못한다. 운영 다중 노드 환경에는 공유 스토리지 또는 오브젝트 스토리지, DNS, 조직 CA, 백업 및 고가용성 설계가 필요하다. Docker 자체와 기존 `images.tar`은 제거 대상이 아니다. Docker가 사용하는 containerd 설정 변경·재시작은 Docker 컨테이너에 짧은 중단을 일으킬 수 있다.

## 학습 경계

새 튜토리얼은 `docs/harbor-kubernetes-helm-tutorial.md` 하나만 만든다. 기존 Part 1/2와 애플리케이션 코드는 바꾸지 않는다. 문서에 명시된 명령을 사용자가 콘솔에서 실행하는 방식이며, 이 작업은 Harbor나 k3s에 실제 리소스를 설치하거나 기존 배포를 변경하지 않는다.

튜토리얼은 다음 흐름을 고정한다.

```text
인터넷 연결 준비 환경                                현재 on-prem 서버
kubernetes deb + control-plane/CNI/Storage 이미지      ->  artifact 반입
Harbor Chart + Harbor 이미지 + images.tar              ->  기존 k3s 제거 및 삭제 범위 확인
                                                        ->  containerd·kernel module·sysctl·swap 준비
                                                        ->  kubeadm으로 control plane 초기화
                                                        ->  Flannel CNI와 local-path-provisioner 설치
                                                        ->  containerd에 Harbor bootstrap 이미지 import
                                                        ->  Kubernetes의 Harbor 설치
                                                        ->  images.tar를 Docker에 load하고 Harbor에 push
                                                        ->  Kubernetes YAML 적용
                                                        ->  자체 Helm Chart 설치·업그레이드·롤백
```

## 설계 결정

### 기존 k3s 제거와 kubeadm Kubernetes 설치

튜토리얼의 첫 실행 장은 "현재 k3s를 삭제해도 되는가"를 확인하는 단계다. `sudo k3s kubectl get all -A`, `sudo k3s kubectl get pvc -A`, `sudo k3s kubectl get nodes -o wide`로 현 상태를 기록한 뒤에만 제거 명령을 제시한다. 사용자가 `RESET-K3S`를 직접 입력해야 다음 명령이 실행되도록 하고, 서버 노드에서는 공식 제거 스크립트인 `sudo /usr/local/bin/k3s-uninstall.sh`를 사용한다.

문서는 이 스크립트가 모든 Pod, local cluster datastore, local-path PV 데이터, node 설정, k3s CLI와 스크립트를 삭제한다는 것을 경고한다. Docker image와 외부 스토리지 데이터는 이 스크립트의 삭제 범위가 아니며, 이전 Harbor/애플리케이션 데이터가 필요하면 이 튜토리얼을 시작하면 안 된다. 제거 후 `systemctl`, `/etc/rancher/k3s`, `/var/lib/rancher/k3s` 상태를 읽기 전용으로 확인한다.

새 클러스터는 `kubeadm`, `kubelet`, `kubectl`과 Docker가 설치한 containerd를 CRI runtime으로 사용한다. tutorial은 CRI v1 socket `/run/containerd/containerd.sock`, containerd의 `SystemdCgroup = true`, kubelet의 systemd cgroup driver를 명시적으로 검증한다. Docker가 사용하는 `/etc/containerd/config.toml`은 먼저 백업하고, `cri` plugin이 disabled 되어 있지 않은 것을 확인한 다음에만 수정·재시작한다. swap은 비활성화하고, `overlay`와 `br_netfilter` module 및 Kubernetes network sysctl을 영구 설정한다.

새 설치는 target에서 원격 apt repository나 `curl | sh`를 실행하지 않는다. 인터넷 연결 준비 환경에서 같은 Ubuntu release와 `KUBERNETES_MINOR`를 기준으로 Kubernetes APT repository의 `kubeadm`, `kubelet`, `kubectl`, `kubernetes-cni`, `cri-tools`와 의존 deb를 내려받아 반입한다. target에서는 local package bundle로 설치하고 package version hold를 설정한다. `kubeadm init --cri-socket=unix:///run/containerd/containerd.sock --pod-network-cidr=10.244.0.0/16`으로 control plane을 초기화하고 현재 사용자의 `$HOME/.kube/config`에 admin kubeconfig를 안전한 권한으로 복사한다.

Flannel을 단일 노드 실습용 CNI로 설치한다. CNI는 CoreDNS가 시작되기 전에 필요하므로, Flannel manifest 및 해당 image를 artifact에 포함해 control plane 직후 적용한다. 이어서 local-path-provisioner와 image를 설치해 `local-path` StorageClass를 만들고, 단일 control-plane node taint를 해제해 Harbor와 애플리케이션 Pod가 이 노드에 배치되게 한다. Node Ready, CoreDNS Running, Flannel Running, StorageClass 존재가 Kubernetes 설치의 성공 기준이다. Helm은 현재 서버에 이미 존재하므로 preflight에서 버전을 확인하고, 진짜 신규 서버를 위한 보충 절에는 Helm binary 반입 방법을 포함한다.

### Harbor 설치 위치

Harbor는 Docker Compose가 아니라 새 kubeadm Kubernetes 클러스터의 `harbor` Namespace에 공식 Harbor Helm Chart로 설치한다. Harbor Chart를 사용하는 것은 애플리케이션 Chart를 작성하는 Helm 실습과 구분한다. 전자는 검증된 외부 패키지의 설치이고, 후자는 실습자가 자신의 YAML을 template으로 전환하는 과정이다.

### 외부 주소와 TLS

실습 기본 주소는 `harbor.algo.local:30443`이다. 실제 서버 IP는 `hostname -I`로 확인한 뒤, 해당 서버와 이후 worker 노드의 `/etc/hosts`에 같은 매핑을 추가한다. 단일 노드의 불필요한 ingress controller 의존성을 피하기 위해 Harbor Chart의 TLS NodePort를 사용한다. 문서는 로컬 CA와 `harbor.algo.local`을 SAN으로 포함하는 서버 인증서를 생성하고, Harbor TLS Secret, Docker의 `certs.d`, containerd의 `certs.d/hosts.toml`에 같은 CA를 사용한다.

`insecure_skip_verify`나 HTTP registry는 소개하되 실습 경로로 사용하지 않는다. 인증서 갱신·다중 노드 신뢰 배포는 운영 전환 체크리스트에 둔다.

### Harbor 저장소와 초기 설정

단일 노드 실습에서는 Chart의 내부 PostgreSQL·Redis와 local-path-provisioner가 제공한 `local-path` StorageClass, 영속 PVC를 사용한다. Trivy 취약점 스캐닝은 이미지와 자원이 추가되므로 최초 설치에서는 비활성화하고, Harbor 기능 소개와 후속 활성화 방법을 별도 안내한다. Harbor 관리 화면에서 private `myapp` 프로젝트를 만들고, 관리자가 아닌 robot account를 애플리케이션 pull 자격 증명으로 사용한다.

### 폐쇄망 bootstrap artifact

인터넷 연결 준비 환경에서 `KUBERNETES_MINOR`와 exact package version을 한 번 정하고, 같은 Ubuntu release에서 kubeadm/kubelet/kubectl 및 의존 deb를 내려받는다. Kubernetes control-plane image 목록은 `kubeadm config images list --kubernetes-version`으로 확정하고, Flannel 및 local-path-provisioner manifest가 참조하는 image와 함께 `kubernetes-bootstrap-images.tar`로 저장한다. 이어서 Harbor Chart 버전을 명시해 내려받고, `helm template` 결과에서 Harbor 구성 이미지 목록을 추출해 `harbor-images.tar`로 저장한다. deb bundle, Kubernetes bootstrap image tar, CNI 및 storage manifest, Chart 아카이브, Harbor 이미지 tar, 체크섬, 기존 애플리케이션 `images.tar`을 하나의 반입 디렉터리에 배치한다. target에서는 containerd `k8s.io` namespace에 bootstrap image tar를 import한 뒤 `kubeadm init`을 실행하고, cluster가 Ready가 된 뒤 Harbor image를 같은 namespace에 import해 Chicken-and-egg 문제를 피한다.

튜토리얼은 "현재 서버가 인터넷에 연결된 실습"과 "진짜 폐쇄망 설치"를 구분한다. 전자는 Chart 저장소에서 내려받을 수 있으나, 후자는 원격 repository·Docker Hub에 접근하는 명령을 실행하지 않는다.

### Kubernetes 애플리케이션 구성

`offline-demo` Namespace에 아래 raw YAML을 작성·적용한다.

- `Secret`: PostgreSQL 사용자, 실습용 영숫자 비밀번호, DB 이름
- `Secret`: private Harbor project용 `imagePullSecret`
- `Service`(headless) + `StatefulSet` + `PersistentVolumeClaim`: PostgreSQL
- `Deployment` + `Service`(NodePort): FastAPI

FastAPI에는 Compose와 동등한 `postgresql+psycopg://...@postgres:5432/...` 연결 주소를 주며, PostgreSQL Service 이름을 `postgres`로 고정한다. readiness probe는 `/health`, PostgreSQL은 `pg_isready`를 사용한다. NodePort는 30000–32767 범위에서 충돌 가능성이 낮은 `30800`을 기본값으로 쓴다. 실습용 비밀번호는 URL escape 문제가 없는 영숫자만 허용하고, 운영 환경에서는 Secret 관리와 URL encoding 검토를 별도 경고한다.

### Helm 애플리케이션 Chart

raw YAML을 `~/offline-demo-lab/helm/offline-demo` Chart로 전환한다. `Chart.yaml`, `values.yaml`, `_helpers.tpl`, `secrets.yaml`, `postgres-service.yaml`, `postgres-statefulset.yaml`, `backend-service.yaml`, `backend-deployment.yaml`, `NOTES.txt`를 만든다. Secret 값은 기본 `values.yaml`에 쓰지 않고 별도 `values-lab.yaml`에만 두며, 이 파일을 Git에 보관하지 말라고 명시한다.

Chart는 Harbor repository/tag, pull-secret 이름, DB 값, PVC 크기, NodePort를 values로 받는다. 예제는 `helm lint`, `helm template`, `helm install --wait`, 태그 변경 `helm upgrade --wait --rollback-on-failure`, `helm history`, `helm rollback`, `helm uninstall` 순으로 검증한다. Harbor 자체의 database schema upgrade에는 `helm rollback`을 적용할 수 없다는 점을 애플리케이션 rollback과 분명히 구분한다.

## 문서 형식과 검증

모든 장은 "왜 필요한가", "명령", "기대 결과", "실패 시 점검"을 갖는다. root 권한이 필요한 명령은 `sudo`로 표시한다. 값이 환경에 따라 달라지는 호스트명, IP, Chart 버전은 시작 단계에서 단 한 번 변수로 선언하고 이후 그 변수를 재사용한다.

문서 자체의 검증은 다음을 포함한다.

- Markdown heading/코드 펜스의 균형 확인
- 문서의 image 이름과 기존 Compose image 이름의 일치 확인
- `helm lint` 및 `helm template` 명령을 문서의 Chart 내용에 맞게 검토
- 모든 외부 기술 사실은 Harbor, Kubernetes, Helm의 공식 문서 링크로 근거 제공

## 출처

- [Harbor Helm 설치](https://goharbor.io/docs/main/install-config/harbor-ha-helm/)
- [Harbor HTTPS 구성](https://goharbor.io/docs/main/install-config/configure-https/)
- [k3s 제거](https://docs.k3s.io/installation/uninstall)
- [kubeadm 설치](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/install-kubeadm/)
- [kubeadm cluster 생성](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/)
- [containerd CRI 구성](https://kubernetes.io/docs/setup/production-environment/container-runtimes/)
- [Helm 명령어](https://helm.sh/docs/helm/)
