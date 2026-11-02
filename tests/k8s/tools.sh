#!/usr/bin/env bash
# Laedt die fuer den kind-Test (docs/KUBERNETES.md, „Test auf kind“) noetigen Werkzeuge als
# versionierte Einzel-Binaries mit sha256-Pruefung in ein Benutzerverzeichnis (kein
# Systeminstall, kein sudo):
#
#   tests/k8s/tools.sh [zielverzeichnis]     (Default: ~/npt-k8s-tools)
#   export PATH="$HOME/npt-k8s-tools/bin:$PATH"
#
# Die Pruefsummen stehen fest in diesem Skript (abgeglichen mit den vom Projekt
# veroeffentlichten Pruefsummen-Dateien); eine Abweichung bricht ab.
set -euo pipefail

DEST="${1:-$HOME/npt-k8s-tools}"
ARCH="linux-amd64"
[[ "$(uname -m)" == "x86_64" ]] || { echo "nur x86_64 vorgesehen" >&2; exit 1; }

KIND_VERSION=v0.33.0
KIND_SHA256=aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d
HELM_VERSION=v4.3.1
HELM_SHA256=0403a88ae20913d11b10a2467b50087721fea7581736da6b9d3992f5357fe54b
HELM3_VERSION=v3.22.1
HELM3_SHA256=896cf63684a42a723d61c980369d6e588330927deb13676ca0d85006dd6da0ea
KUBECTL_VERSION=v1.37.2
KUBECTL_SHA256=c04487523e7aa7d1493f7808174c899db93d7a9cf57531420cd76b9bd415f8f4
KUBECONFORM_VERSION=v0.8.0
KUBECONFORM_SHA256=9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883

mkdir -p "$DEST/bin" "$DEST/dl"
fetch() { # url datei sha256
  local url="$1" file="$DEST/dl/$2" sum="$3"
  if [[ ! -f "$file" ]] || ! echo "$sum  $file" | sha256sum -c --quiet - 2>/dev/null; then
    curl -fsSL --retry 3 -o "$file.part" "$url"
    mv "$file.part" "$file"
  fi
  echo "$sum  $file" | sha256sum -c --quiet - || { echo "sha256 falsch: $file" >&2; rm -f "$file"; exit 1; }
}

fetch "https://github.com/kubernetes-sigs/kind/releases/download/$KIND_VERSION/kind-$ARCH" \
  "kind-$KIND_VERSION" "$KIND_SHA256"
install -m 0755 "$DEST/dl/kind-$KIND_VERSION" "$DEST/bin/kind"

fetch "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/amd64/kubectl" \
  "kubectl-$KUBECTL_VERSION" "$KUBECTL_SHA256"
install -m 0755 "$DEST/dl/kubectl-$KUBECTL_VERSION" "$DEST/bin/kubectl"

fetch "https://get.helm.sh/helm-$HELM_VERSION-$ARCH.tar.gz" "helm-$HELM_VERSION.tgz" "$HELM_SHA256"
tar -xzf "$DEST/dl/helm-$HELM_VERSION.tgz" -C "$DEST/dl" "$ARCH/helm"
install -m 0755 "$DEST/dl/$ARCH/helm" "$DEST/bin/helm"

# Helm 3 nur fuer den Kompatibilitaets-Lint (helm3 lint/template).
fetch "https://get.helm.sh/helm-$HELM3_VERSION-$ARCH.tar.gz" "helm-$HELM3_VERSION.tgz" "$HELM3_SHA256"
tar -xzf "$DEST/dl/helm-$HELM3_VERSION.tgz" -C "$DEST/dl" "$ARCH/helm"
install -m 0755 "$DEST/dl/$ARCH/helm" "$DEST/bin/helm3"

fetch "https://github.com/yannh/kubeconform/releases/download/$KUBECONFORM_VERSION/kubeconform-$ARCH.tar.gz" \
  "kubeconform-$KUBECONFORM_VERSION.tgz" "$KUBECONFORM_SHA256"
tar -xzf "$DEST/dl/kubeconform-$KUBECONFORM_VERSION.tgz" -C "$DEST/dl" kubeconform
install -m 0755 "$DEST/dl/kubeconform" "$DEST/bin/kubeconform"

rm -rf "$DEST/dl/$ARCH" "$DEST/dl/kubeconform"
"$DEST/bin/kind" version
"$DEST/bin/kubectl" version --client | head -1
"$DEST/bin/helm" version --short
"$DEST/bin/helm3" version --short
"$DEST/bin/kubeconform" -v
