# Harbor, kubeadm Kubernetes, Helm Tutorial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create one Korean, beginner-oriented tutorial that starts by removing the existing k3s and ends with a Harbor-hosted FastAPI/PostgreSQL Helm release on a single-node kubeadm Kubernetes cluster.

**Architecture:** The tutorial is a single linear lab in `docs/harbor-kubernetes-helm-tutorial.md`. It separates artifact preparation on an internet-connected Ubuntu 20.04 host from execution on the air-gapped target, imports all bootstrap images into the `k8s.io` containerd namespace, then installs Harbor by its local Helm chart. Kubernetes YAML is introduced before the same resources are packaged as an application Helm Chart.

**Tech Stack:** Ubuntu 20.04, Docker 26, containerd CRI, Kubernetes kubeadm/kubelet/kubectl, Flannel CNI, Rancher local-path-provisioner, Harbor Helm Chart, Helm 3, PostgreSQL 15, FastAPI.

**Spec:** `docs/superpowers/specs/2026-09-11-harbor-k3s-helm-tutorial-design.md`

## Global Constraints

- Do not run k3s removal, package installation, containerd restart, or Kubernetes commands on the current server; write them as user-executed tutorial commands only.
- Start the destructive k3s removal section with state capture and an exact `RESET-K3S` typed confirmation gate.
- Use kubeadm, not k3s, for the replacement cluster.
- Use containerd's `k8s.io` namespace for Kubernetes image imports and `/run/containerd/containerd.sock` as the explicit CRI socket.
- Use Flannel with pod CIDR `10.244.0.0/16` and local-path-provisioner for this single-node lab.
- Use TLS NodePort `30443` and hostname `harbor.algo.local`; do not teach an insecure registry as the normal path.
- Preserve existing image names `offline-fastapi:1.0.0` and `postgres:15` before retagging them as `harbor.algo.local:30443/myapp/...`.
- Keep real passwords and robot tokens out of committed files; use lab-only values and tell the reader to replace them.
- Link each external behavior claim to an official Kubernetes, Harbor, Helm, Flannel, or local-path-provisioner source.

---

### Task 1: Create the lab framing, safety gate, and air-gap artifact preparation chapters

**Files:**
- Create: `docs/harbor-kubernetes-helm-tutorial.md`
- Reference: `docs/offline_part1.md`
- Reference: `docs/offline_part2.md`
- Reference: `docs/superpowers/specs/2026-09-11-harbor-k3s-helm-tutorial-design.md`

**Interfaces:**
- Consumes: Existing `offline-fastapi:1.0.0`, `postgres:15`, and Part 2 `images.tar`.
- Produces: A documented `offline-k8s-lab/` artifact layout containing package debs, control-plane/CNI/storage manifests and image tarballs, the Harbor Chart and image tarball, and application `images.tar`.

- [ ] **Step 1: Write the purpose, final architecture, scope, and vocabulary sections**

  Explain the roles of Docker, OCI image, containerd, CRI, CNI, kubeadm, control plane, worker node, StorageClass, PVC, Harbor, Kubernetes resource, and Helm Chart. Include a one-screen flow diagram from connected build host to air-gapped target and label which artifacts bootstrap Kubernetes before Harbor exists.

- [ ] **Step 2: Write the current-server preflight and destructive k3s reset section**

  Record Ubuntu version, architecture, disk, Docker, containerd, Helm, swap, port, and existing k3s state. Capture current k3s resources with `sudo k3s kubectl` before a shell confirmation that accepts only `RESET-K3S`, then call `sudo /usr/local/bin/k3s-uninstall.sh`. Explain the exact data loss and verify that k3s service and directories are gone without deleting unrelated Docker images.

- [ ] **Step 3: Write the connected-host artifact preparation section**

  Declare `KUBERNETES_MINOR`, exact package version, Flannel release, local-path-provisioner release, Harbor Chart version, hostname, and NodePort once. Download Kubernetes deb packages and their dependency closure from `pkgs.k8s.io` on a matching Ubuntu host. Build `kubernetes-bootstrap-images.tar` from the output of `kubeadm config images list`, Flannel manifest images, and local-path-provisioner images. Download the Harbor Chart, render it with the lab values, collect its image references, save them as `harbor-images.tar`, copy existing `images.tar`, and generate SHA-256 checksums.

- [ ] **Step 4: Verify the written artifact contract**

  Run:

  ```bash
  rg -n 'k3s|kubeadm|kubernetes-bootstrap-images\.tar|harbor-images\.tar|images\.tar|RESET-K3S' docs/harbor-kubernetes-helm-tutorial.md
  ```

  Expected: k3s appears only in the removal section; every required artifact and the confirmation gate appears in the tutorial.

### Task 2: Create the kubeadm cluster, networking, storage, and Harbor chapters

**Files:**
- Modify: `docs/harbor-kubernetes-helm-tutorial.md`
- Reference: `docs/superpowers/specs/2026-09-11-harbor-k3s-helm-tutorial-design.md`

**Interfaces:**
- Consumes: The transferred artifact directory from Task 1 and an existing Docker/containerd installation.
- Produces: A Ready single-node Kubernetes cluster, a `local-path` StorageClass, and a TLS Harbor registry reachable as `harbor.algo.local:30443`.

- [ ] **Step 1: Write the container runtime and kernel preparation section**

  Back up `/etc/containerd/config.toml`, require a CRI v1-capable containerd socket, set the matching `SystemdCgroup = true` syntax for containerd 1.x or 2.x, ensure `cri` is enabled, restart containerd, disable swap, and persist `overlay`, `br_netfilter`, and bridge netfilter sysctls. State that restarting containerd can briefly interrupt Docker containers and show checks for the service and CRI socket.

- [ ] **Step 2: Write local package installation and kubeadm initialization commands**

  Install only transferred deb files, hold Kubernetes packages, import bootstrap images with `sudo ctr -n k8s.io images import`, run `sudo kubeadm init --cri-socket=unix:///run/containerd/containerd.sock --pod-network-cidr=10.244.0.0/16`, create `$HOME/.kube/config` with mode 600, and save the displayed `kubeadm join` command. Explain why CoreDNS stays Pending before the CNI exists.

- [ ] **Step 3: Write Flannel, local storage, and single-node scheduling sections**

  Import CNI/storage images before applying their local manifests, wait for CoreDNS and Flannel, install local-path-provisioner, mark `local-path` as the default StorageClass, and remove the control-plane taint. Verify Nodes, Pods, and StorageClasses with expected Ready/Running output.

- [ ] **Step 4: Write Harbor TLS, Helm installation, and client trust sections**

  Create a local CA and a server certificate for `harbor.algo.local`; create the Kubernetes TLS Secret; import Harbor images; install Harbor from the local Chart with NodePort HTTPS `30443`, internal persistence, and Trivy disabled; then configure Docker and containerd trust. Include `/etc/hosts` mapping, `hosts.toml`, `docker login`, browser or `curl --cacert` verification, private `myapp` project creation, and a least-privilege robot account.

- [ ] **Step 5: Verify the written cluster contract**

  Run:

  ```bash
  rg -n 'containerd|SystemdCgroup|kubeadm init|10\.244\.0\.0/16|Flannel|local-path|30443|hosts\.toml|imagePullSecret' docs/harbor-kubernetes-helm-tutorial.md
  ```

  Expected: all runtime, networking, storage, TLS, registry, and private image pull elements are present.

### Task 3: Create the application YAML, Harbor transfer, and Helm chapters

**Files:**
- Modify: `docs/harbor-kubernetes-helm-tutorial.md`
- Reference: `app/src/interface/api.py`
- Reference: `docker/docker-compose.yml`

**Interfaces:**
- Consumes: Running private Harbor project `myapp`, the two loaded source images, and a Ready Kubernetes cluster from Task 2.
- Produces: A verified raw-YAML `offline-demo` deployment, then an equivalent `offline-demo` Helm release with a recorded upgrade and rollback.

- [ ] **Step 1: Write image loading, retagging, pushing, and pull-secret commands**

  Load Part 2 `images.tar` into Docker, tag FastAPI and PostgreSQL as `harbor.algo.local:30443/myapp/offline-fastapi:1.0.0` and `harbor.algo.local:30443/myapp/postgres:15`, push both images, inspect repositories in Harbor, and create a Kubernetes `docker-registry` Secret using the robot account credentials.

- [ ] **Step 2: Write the raw Kubernetes YAML lab**

  Have the reader create `namespace.yaml`, `database-secret.yaml`, `postgres-service.yaml`, `postgres-statefulset.yaml`, `backend-deployment.yaml`, and `backend-service.yaml`. Use a headless PostgreSQL Service, one persistent claim, `postgres:15`, a FastAPI Deployment with `/health` readiness probe, a private registry `imagePullSecrets` reference, Compose-equivalent `DATABASE_URL`, and NodePort `30800`. Include `kubectl apply`, rollout waits, logs, health curl, item create/list requests, and a pod recreation check that proves PostgreSQL data survives.

- [ ] **Step 3: Write the Helm Chart conversion and lifecycle lab**

  Build `Chart.yaml`, `values.yaml`, `values-lab.yaml`, `_helpers.tpl`, `secrets.yaml`, PostgreSQL templates, FastAPI templates, and `NOTES.txt` beneath `~/offline-demo-lab/helm/offline-demo`. Keep real credentials in `values-lab.yaml`, not `values.yaml`. Show `helm lint`, `helm template`, `helm install --wait`, `helm upgrade --set backend.image.tag=1.0.1 --wait --rollback-on-failure`, `helm history`, `helm rollback`, and `helm uninstall`; include expected release revision changes.

- [ ] **Step 4: Write the final air-gap checklist and troubleshooting matrix**

  Cover checksum failure, containerd CRI error, swap/preflight error, CoreDNS Pending, CNI mismatch, PVC Pending, TLS unknown authority, image pull authentication failure, ImagePullBackOff, NodePort firewall, Harbor startup delay, and Helm rendering failure. End with a release checklist that explicitly prohibits network access on the target and identifies the correct log command for each layer.

- [ ] **Step 5: Verify application facts against the repository**

  Run:

  ```bash
  rg -n '@router\.(get|post)\("/(health|items)|image: offline-fastapi:1\.0\.0|image: postgres:15|DATABASE_URL' app/src/interface/api.py docker/docker-compose.yml docs/harbor-kubernetes-helm-tutorial.md
  ```

  Expected: tutorial image names, health route, item routes, and database connection model match the application and Compose configuration.

### Task 4: Perform documentation-wide validation

**Files:**
- Modify: `docs/harbor-kubernetes-helm-tutorial.md`
- Reference: `docs/superpowers/specs/2026-09-11-harbor-k3s-helm-tutorial-design.md`

**Interfaces:**
- Consumes: Completed tutorial from Tasks 1–3.
- Produces: A syntax-clean tutorial whose technical constraints can be reviewed without executing destructive commands.

- [ ] **Step 1: Check Markdown structure and code fences**

  Run:

  ```bash
  awk '/^```/{count++} END {exit count % 2}' docs/harbor-kubernetes-helm-tutorial.md
  git diff --check
  ```

  Expected: both commands exit with status 0.

- [ ] **Step 2: Check that the unsafe normal paths are absent**

  Run:

  ```bash
  rg -n 'insecure_skip_verify:\s*true|http://harbor\.algo\.local|curl .*\|.*sh' docs/harbor-kubernetes-helm-tutorial.md
  ```

  Expected: no matches, except an explanatory sentence that explicitly says it is not a lab command.

- [ ] **Step 3: Check scope and source coverage**

  Compare each decision in the spec with a tutorial heading. Confirm there are direct links to official Kubernetes kubeadm/container runtime documents, Harbor Helm/HTTPS documents, Helm commands, Flannel, and local-path-provisioner sources.

- [ ] **Step 4: Commit the documentation when Git metadata is writable**

  ```bash
  git add docs/harbor-kubernetes-helm-tutorial.md docs/superpowers/specs/2026-09-11-harbor-k3s-helm-tutorial-design.md docs/superpowers/plans/2026-09-12-harbor-kubeadm-helm-tutorial.md
  git commit -m "docs: add kubeadm harbor helm tutorial"
  ```

  Expected: the commit contains only the approved tutorial, design, and implementation-plan files. If the environment blocks `.git/index.lock`, report that fact and leave working files intact.
