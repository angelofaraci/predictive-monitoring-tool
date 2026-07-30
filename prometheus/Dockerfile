# Custom Prometheus image (Phase 9, Work Unit 6 — design ADR #7).
#
# Bakes the scrape config into the image because Azure Container Apps has
# no equivalent of a docker-compose bind mount: this is the simplest way
# to ship config-as-code here without a runtime script or a shared-file
# volume (both considered and rejected in the design). Built and pushed by
# hand to the same ACR the main app uses (see README's "Monitoring image
# build" section) — CI wiring for this image is a Work Unit 7 follow-up,
# not yet in place. `var.prometheus_image` defaults to the public upstream
# tag below, so a standalone `terraform apply` still succeeds before this
# image has ever been built, mirroring the existing `var.container_image`
# placeholder pattern.
FROM prom/prometheus:v2.55.1

COPY prometheus.yml /etc/prometheus/prometheus.yml
