# Custom Grafana image (Phase 9, Work Unit 6 — design ADR #7).
#
# Bakes Grafana's native file-based provisioning (datasource + dashboard
# provider + the actual dashboard JSON) into the image, so the
# `app-health` dashboard renders on first boot with zero manual UI
# clicks and zero runtime scripts. Built and pushed by hand to the same
# ACR the main app uses (see README's "Monitoring image build" section)
# — CI wiring for this image is a Work Unit 7 follow-up, not yet in
# place. `var.grafana_image` defaults to the public upstream tag below,
# so a standalone `terraform apply` still succeeds before this image has
# ever been built, mirroring the existing `var.container_image`
# placeholder pattern.
FROM grafana/grafana:11.2.0

COPY provisioning/datasources/prometheus.yaml /etc/grafana/provisioning/datasources/prometheus.yaml
COPY provisioning/dashboards/app-health.yaml /etc/grafana/provisioning/dashboards/app-health.yaml
COPY dashboards/app-health.json /var/lib/grafana/dashboards/app-health.json
