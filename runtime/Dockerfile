FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates buildah netavark git && \
    # kubectl — real tool target: this pod's own ServiceAccount in the opssquad namespace
    curl -fsSL -o /usr/local/bin/kubectl "https://dl.k8s.io/release/v1.31.0/bin/linux/amd64/kubectl" && \
    chmod +x /usr/local/bin/kubectl && \
    # terraform — real tool target: ./terraform-demo (local-only provider, no cloud creds)
    curl -fsSL -o /tmp/terraform.zip "https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_amd64.zip" && \
    unzip -q /tmp/terraform.zip -d /usr/local/bin && rm /tmp/terraform.zip && \
    # trivy — real tool target: this container's own filesystem, and (config
    # subcommand) ./terraform-demo for iac.scan
    curl -fsSL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin && \
    # helm — real tool target: k8s/charts/canary, a trivial release in this pod's own namespace
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash && \
    # gitleaks — real tool target: ./git-demo, built below
    curl -fsSL -o /tmp/gitleaks.tar.gz "https://github.com/gitleaks/gitleaks/releases/download/v8.21.2/gitleaks_8.21.2_linux_x64.tar.gz" && \
    tar xzf /tmp/gitleaks.tar.gz -C /usr/local/bin gitleaks && rm /tmp/gitleaks.tar.gz && \
    apt-get purge -y unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY terraform-demo ./terraform-demo
COPY docker-demo ./docker-demo
COPY charts ./charts
RUN terraform -chdir=terraform-demo init -no-color

# git-demo — real tool target for git.diff/git.log/secrets.scan. Built
# fresh at image-build time (not a nested .git committed into this repo,
# not fetched) — same "real, safe, self-contained, regenerated on every
# build" role terraform-demo/docker-demo play. One commit deliberately
# contains an AKIA-shaped fake key so secrets.scan has a genuine positive
# to find, not just an empty-history clean scan every time.
RUN mkdir git-demo && cd git-demo && git init -q && \
    git config user.email demo@opssquad.dev && git config user.name opssquad-demo && \
    echo "hello" > app.txt && git add . && git commit -q -m "initial commit" && \
    printf 'AWS_KEY=AKIAJ2XZ7KQPBQFHUFUA\n' >> app.txt && git add . && git commit -q -m "add config (oops)" && \
    echo "world" >> app.txt && git add . && git commit -q -m "fix typo"

# Bake in trivy's vulnerability DB so a scan at request time doesn't need
# network access or a slow first-run download.
RUN trivy fs --download-db-only --quiet /app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
