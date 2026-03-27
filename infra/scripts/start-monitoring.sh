#!/usr/bin/env bash
# start-monitoring.sh — Start the full observability stack with one command.
#
# Usage:
#   bash infra/scripts/start-monitoring.sh          # start all services
#   bash infra/scripts/start-monitoring.sh --stop    # stop all services
#   bash infra/scripts/start-monitoring.sh --reset   # stop + delete volumes
#   bash infra/scripts/start-monitoring.sh --status  # check service health

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/infra/docker/docker-compose.monitoring.yml"

# Export workspace root for Promtail volume mount
export WORKSPACE_ROOT="${WORKSPACE_ROOT:-$PROJECT_ROOT/workspace}"

compose() {
    docker compose -f "$COMPOSE_FILE" "$@"
}

case "${1:-start}" in
    start)
        echo "Starting orchestrator monitoring stack..."
        compose up -d
        echo ""
        echo "Monitoring stack is running:"
        echo "  Prometheus  → http://localhost:9091"
        echo "  Grafana     → http://localhost:3000  (admin / admin)"
        echo "  Jaeger      → http://localhost:16686"
        echo "  Loki        → http://localhost:3100"
        echo ""
        echo "Enable metrics in your orchestrator by setting:"
        echo "  monitoring.metrics_enabled: true   in config/default.yaml"
        echo "  monitoring.tracing_enabled: true   for distributed tracing"
        echo "  monitoring.loki_enabled: true      for direct log shipping"
        ;;
    --stop|stop)
        echo "Stopping monitoring stack..."
        compose down
        echo "Monitoring stack stopped."
        ;;
    --reset|reset)
        echo "Stopping monitoring stack and removing all data volumes..."
        compose down -v
        echo "Monitoring stack reset. All historical data removed."
        ;;
    --status|status)
        echo "Monitoring stack status:"
        echo ""
        compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
        echo ""
        # Health checks
        echo "Service health:"
        for svc in "Prometheus:localhost:9091/-/healthy" "Grafana:localhost:3000/api/health" "Jaeger:localhost:16686/" "Loki:localhost:3100/ready"; do
            name="${svc%%:*}"
            url="${svc#*:}"
            if curl -sf --max-time 3 "http://$url" > /dev/null 2>&1; then
                echo "  $name: healthy"
            else
                echo "  $name: unreachable"
            fi
        done
        ;;
    *)
        echo "Usage: $0 [start|--stop|--reset|--status]"
        exit 1
        ;;
esac
