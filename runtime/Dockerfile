FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates buildah netavark && \
    # kubectl — real tool target: this pod's own ServiceAccount in the opssquad namespace
    curl -fsSL -o /usr/local/bin/kubectl "https://dl.k8s.io/release/v1.31.0/bin/linux/amd64/kubectl" && \
    chmod +x /usr/local/bin/kubectl && \
    # terraform — real tool target: ./terraform-demo (local-only provider, no cloud creds)
    curl -fsSL -o /tmp/terraform.zip "https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_amd64.zip" && \
    unzip -q /tmp/terraform.zip -d /usr/local/bin && rm /tmp/terraform.zip && \
    # trivy — real tool target: this container's own filesystem
    curl -fsSL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin && \
    # helm — real tool target: k8s/charts/canary, a trivial release in this pod's own namespace
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash && \
    apt-get purge -y unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY terraform-demo ./terraform-demo
COPY docker-demo ./docker-demo
COPY charts ./charts
RUN terraform -chdir=terraform-demo init -no-color

# Bake in trivy's vulnerability DB so a scan at request time doesn't need
# network access or a slow first-run download.
RUN trivy fs --download-db-only --quiet /app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
