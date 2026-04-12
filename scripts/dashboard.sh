#!/usr/bin/env bash
# Catalyst Data Dashboard - Dynamic cluster status display
# Queries the cluster for all catalyst-data services and their status
#
# Usage:
#   ./scripts/dashboard.sh              # Full dashboard
#   ./scripts/dashboard.sh --summary    # One-line status for Tilt/CI
#   ./scripts/dashboard.sh --plain      # ANSI-only (no gum), auto-detected for non-TTY
#
# shellcheck disable=SC2016,SC2034

set -euo pipefail

# Get script directory and source common library
DASHBOARD_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${DASHBOARD_SCRIPT_DIR}/.." && pwd)"

# shellcheck source=lib/dashboard-common.sh
source "${DASHBOARD_SCRIPT_DIR}/lib/dashboard-common.sh"

# ============================================================================
# Configuration
# ============================================================================
NAMESPACE="catalyst-data"
DOMAIN="${DOMAIN:-talos00}"

# Dagster platform deployments (share app: dagster, differentiated by component)
DAGSTER_DEPLOYMENTS=("dagster-webserver" "dagster-daemon" "dagster-postgres")

# Code locations (each has unique app: label matching deployment name)
CODE_LOCATIONS=("congress-data" "media-ingest" "open-leaks" "knowledge-graph")

# Infrastructure services (unique app: labels)
INFRA_SERVICES=("neo4j" "postgres-knowledge")

# ============================================================================
# Print ASCII header
# ============================================================================
print_header() {
  echo -e "${CYAN}${BOLD}"
  cat << 'EOF'
 ██████╗ █████╗ ████████╗ █████╗ ██╗  ██╗   ██╗███████╗████████╗    ██████╗  █████╗ ████████╗ █████╗
██╔════╝██╔══██╗╚══██╔══╝██╔══██╗██║  ╚██╗ ██╔╝██╔════╝╚══██╔══╝    ██╔══██╗██╔══██╗╚══██╔══╝██╔══██╗
██║     ███████║   ██║   ███████║██║   ╚████╔╝ ███████╗   ██║       ██║  ██║███████║   ██║   ███████║
██║     ██╔══██║   ██║   ██╔══██║██║    ╚██╔╝  ╚════██║   ██║       ██║  ██║██╔══██║   ██║   ██╔══██║
╚██████╗██║  ██║   ██║   ██║  ██║███████╗██║   ███████║   ██║       ██████╔╝██║  ██║   ██║   ██║  ██║
 ╚═════╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝   ╚══════╝   ╚═╝       ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝
                              Dagster Data Platform
EOF
  echo -e "${RESET}"
  echo ""
}

# ============================================================================
# Fetch all catalyst-data namespace data
# ============================================================================
fetch_data() {
  echo -e "${DIM}Loading catalyst-data status...${RESET}"

  # Cluster-wide data
  kubectl get nodes -o json > "$CACHE_DIR/nodes.json" 2>/dev/null &
  kubectl get pv -o json > "$CACHE_DIR/pvs.json" 2>/dev/null &

  # Namespace-specific data
  if namespace_exists "$NAMESPACE"; then
    kubectl get pods -n "$NAMESPACE" -o json > "$CACHE_DIR/pods.json" 2>/dev/null &
    kubectl get deployments -n "$NAMESPACE" -o json > "$CACHE_DIR/deployments.json" 2>/dev/null &
    kubectl get svc -n "$NAMESPACE" -o json > "$CACHE_DIR/services.json" 2>/dev/null &
    kubectl get pvc -n "$NAMESPACE" -o json > "$CACHE_DIR/pvcs.json" 2>/dev/null &
    kubectl get secrets -n "$NAMESPACE" -o json > "$CACHE_DIR/secrets.json" 2>/dev/null &
  fi

  wait

  # Clear the loading message
  echo -e "\033[1A\033[2K"
}

# ============================================================================
# Pod status helpers
# ============================================================================

# Get pod status by deployment name (from cached deployments.json → pods.json)
# Used for Dagster platform pods that all share app: dagster
get_pod_status_by_deploy() {
  local deploy_name=$1
  local pod_prefix="${deploy_name}-"

  # Find pods whose name starts with the deployment name prefix
  local status
  status=$(jq -r ".items[] | select(.metadata.name | startswith(\"${pod_prefix}\")) | .status.phase" "$CACHE_DIR/pods.json" 2>/dev/null | head -1)
  echo "${status:-NotFound}"
}

get_pod_ready_by_deploy() {
  local deploy_name=$1
  local pod_prefix="${deploy_name}-"

  local ready
  ready=$(jq -r ".items[] | select(.metadata.name | startswith(\"${pod_prefix}\")) | .status.containerStatuses[0].ready // false" "$CACHE_DIR/pods.json" 2>/dev/null | head -1)
  echo "${ready:-false}"
}

# Get pod status by app label (for resources with unique app: labels)
get_pod_status_by_label() {
  local app_label=$1

  local status
  status=$(jq -r ".items[] | select(.metadata.labels.app == \"${app_label}\") | .status.phase" "$CACHE_DIR/pods.json" 2>/dev/null | head -1)
  echo "${status:-NotFound}"
}

get_pod_ready_by_label() {
  local app_label=$1

  local ready
  ready=$(jq -r ".items[] | select(.metadata.labels.app == \"${app_label}\") | .status.containerStatuses[0].ready // false" "$CACHE_DIR/pods.json" 2>/dev/null | head -1)
  echo "${ready:-false}"
}

# ============================================================================
# Print service with tree-style display
# ============================================================================
print_service() {
  local name=$1
  local status=$2
  local ready=$3
  local url=${4:-""}
  local is_last=${5:-false}

  # Status icon
  local icon color
  if [[ "$status" == "Running" && "$ready" == "true" ]]; then
    icon="${GREEN}●${RESET}"
  elif [[ "$status" == "Running" ]]; then
    icon="${YELLOW}○${RESET}"
  elif [[ "$status" == "NotFound" ]]; then
    icon="${RED}✗${RESET}"
  else
    icon="${YELLOW}⚠${RESET}"
  fi

  # Tree character
  local tree_char="┣━"
  [[ "$is_last" == "true" ]] && tree_char="┗━"

  # Format output
  local line="  ${tree_char} ${icon} ${BOLD}${name}${RESET}"
  if [[ -n "$url" ]]; then
    line="${line}  ${DIM}→ ${CYAN}${url}${RESET}"
  fi
  if [[ "$status" != "Running" ]]; then
    line="${line}  ${DIM}[${status}]${RESET}"
  fi

  echo -e "$line"
}

# ============================================================================
# Print Dagster Platform section
# ============================================================================
print_dagster_platform() {
  print_section "DAGSTER PLATFORM"

  local status ready
  # Webserver
  status=$(get_pod_status_by_deploy "dagster-webserver")
  ready=$(get_pod_ready_by_deploy "dagster-webserver")
  print_service "dagster-webserver" "$status" "$ready" "http://dagster.${DOMAIN} (→3000)"

  # Daemon
  status=$(get_pod_status_by_deploy "dagster-daemon")
  ready=$(get_pod_ready_by_deploy "dagster-daemon")
  print_service "dagster-daemon" "$status" "$ready"

  # Postgres
  status=$(get_pod_status_by_deploy "dagster-postgres")
  ready=$(get_pod_ready_by_deploy "dagster-postgres")
  print_service "dagster-postgres" "$status" "$ready" "localhost:5432" true
  echo ""
}

# ============================================================================
# Print Code Locations section
# ============================================================================
print_code_locations() {
  print_section "CODE LOCATIONS"

  local port=4001
  local total=${#CODE_LOCATIONS[@]}
  local i=0

  for loc in "${CODE_LOCATIONS[@]}"; do
    i=$((i + 1))
    local is_last="false"
    [[ $i -eq $total ]] && is_last="true"

    local status ready
    status=$(get_pod_status_by_label "$loc")
    ready=$(get_pod_ready_by_label "$loc")
    print_service "$loc" "$status" "$ready" "gRPC →${port}:4000" "$is_last"
    port=$((port + 1))
  done
  echo ""
}

# ============================================================================
# Print Data Explorer section
# ============================================================================
print_data_explorer() {
  print_section "DATA EXPLORER"

  local status ready
  status=$(get_pod_status_by_label "data-explorer")
  ready=$(get_pod_ready_by_label "data-explorer")
  print_service "data-explorer" "$status" "$ready" "http://data.${DOMAIN} (→8501)" true
  echo ""
}

# ============================================================================
# Print Infrastructure section
# ============================================================================
print_infrastructure() {
  print_section "KNOWLEDGE GRAPH INFRASTRUCTURE"

  local status ready

  # Neo4j
  status=$(get_pod_status_by_label "neo4j")
  ready=$(get_pod_ready_by_label "neo4j")
  print_service "neo4j" "$status" "$ready" "→7474 (browser), →7687 (bolt)"

  # Postgres-knowledge
  status=$(get_pod_status_by_label "postgres-knowledge")
  ready=$(get_pod_ready_by_label "postgres-knowledge")
  print_service "postgres-knowledge" "$status" "$ready" "→5433:5432" true
  echo ""
}

# ============================================================================
# Print Storage section
# ============================================================================
print_storage() {
  print_section "STORAGE"

  if [[ ! -f "$CACHE_DIR/pvcs.json" ]]; then
    echo -e "  ${DIM}No PVC data available${RESET}"
    echo ""
    return
  fi

  local total bound pending
  total=$(jq '.items | length' "$CACHE_DIR/pvcs.json" 2>/dev/null)
  bound=$(jq '[.items[] | select(.status.phase == "Bound")] | length' "$CACHE_DIR/pvcs.json" 2>/dev/null)
  pending=$(jq '[.items[] | select(.status.phase == "Pending")] | length' "$CACHE_DIR/pvcs.json" 2>/dev/null)

  local status_color=$GREEN
  [[ "$bound" != "$total" ]] && status_color=$YELLOW

  echo -e "  ${status_color}${bound}/${total} PVCs Bound${RESET}"
  [[ "$pending" -gt 0 ]] && echo -e "  ${YELLOW}⚠ ${pending} Pending${RESET}"
  echo ""

  # List each PVC
  jq -r '.items[] | .metadata.name + "|" + .status.phase + "|" + .spec.resources.requests.storage + "|" + (.spec.storageClassName // "default")' "$CACHE_DIR/pvcs.json" 2>/dev/null | sort | while IFS='|' read -r name phase capacity sc; do
    local icon="${GREEN}●${RESET}"
    [[ "$phase" != "Bound" ]] && icon="${YELLOW}○${RESET}"

    # Shorten storage class
    local sc_short="$sc"
    case "$sc" in
      fatboy-nfs-appdata) sc_short="nfs:appdata" ;;
      local-path) sc_short="local" ;;
    esac

    echo -e "    ${icon} ${DIM}${name}${RESET} ${BLUE}(${capacity})${RESET} ${DIM}[${sc_short}]${RESET}"
  done
  echo ""
}

# ============================================================================
# Print Service URLs section
# ============================================================================
print_service_urls() {
  print_section "SERVICE URLS"
  echo -e "  ${DIM}Requires /etc/hosts entries for *.${DOMAIN}${RESET}"
  echo ""

  echo -e "  ${BOLD}Web UIs:${RESET}"
  echo -e "    Dagster:            http://dagster.${DOMAIN}"
  echo -e "    Data Explorer:      http://data.${DOMAIN}"
  echo ""

  echo -e "  ${BOLD}Local Port-Forwards (via Tilt):${RESET}"
  echo -e "    Dagster Webserver:  http://localhost:3000"
  echo -e "    Data Explorer:      http://localhost:8501"
  echo -e "    Neo4j Browser:      http://localhost:7474"
  echo -e "    Neo4j Bolt:         bolt://localhost:7687"
  echo -e "    Dagster Postgres:   localhost:5432"
  echo -e "    Knowledge Postgres: localhost:5433"
  echo ""

  echo -e "  ${BOLD}Code Location gRPC (via Tilt):${RESET}"
  echo -e "    congress-data:      localhost:4001"
  echo -e "    media-ingest:       localhost:4002"
  echo -e "    open-leaks:         localhost:4003"
  echo -e "    knowledge-graph:    localhost:4004"
  echo ""
}

# ============================================================================
# Print Quick Commands section
# ============================================================================
print_quick_commands() {
  print_section "QUICK COMMANDS"
  echo -e "  ${CYAN}logs-web${RESET}     │ kubectl logs -n catalyst-data deploy/dagster-webserver -f"
  echo -e "  ${CYAN}logs-daemon${RESET}  │ kubectl logs -n catalyst-data deploy/dagster-daemon -f"
  echo -e "  ${CYAN}logs-loc${RESET}     │ kubectl logs -n catalyst-data deploy/<code-location> -f"
  echo -e "  ${CYAN}shell-web${RESET}    │ kubectl exec -it -n catalyst-data deploy/dagster-webserver -- bash"
  echo -e "  ${CYAN}psql-dag${RESET}     │ kubectl exec -it -n catalyst-data deploy/dagster-postgres -- psql -U dagster"
  echo -e "  ${CYAN}psql-kg${RESET}      │ kubectl exec -it -n catalyst-data deploy/postgres-knowledge -- psql -U kg -d knowledge_graph"
  echo -e "  ${CYAN}cypher${RESET}       │ kubectl exec -it -n catalyst-data deploy/neo4j -- cypher-shell -u neo4j -p neo4j-homelab"
  echo -e "  ${CYAN}tilt${RESET}         │ tilt up"
  echo -e "  ${CYAN}dashboard${RESET}    │ ./scripts/dashboard.sh"
  echo ""
}

# ============================================================================
# Summary mode - one-line status for Tilt/CI
# ============================================================================
print_summary() {
  dashboard_init

  if ! namespace_exists "$NAMESPACE"; then
    echo -e "    Catalyst Data: ${RED}✗${RESET} namespace not found"
    return
  fi

  local pod_count running_count
  pod_count=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l | tr -d ' ')
  running_count=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | grep -c "Running" || echo "0")

  local code_loc_healthy=0
  for loc in "${CODE_LOCATIONS[@]}"; do
    local status
    status=$(kubectl get pods -n "$NAMESPACE" -l "app=${loc}" --no-headers 2>/dev/null | grep -c "Running" || echo "0")
    [[ "$status" -gt 0 ]] && code_loc_healthy=$((code_loc_healthy + 1))
  done

  local status_icon="${GREEN}✓${RESET}"
  [[ "$running_count" != "$pod_count" ]] && status_icon="${YELLOW}⚠${RESET}"

  echo -e "    Catalyst Data: ${status_icon} ${running_count}/${pod_count} pods │ Code locations: ${code_loc_healthy}/${#CODE_LOCATIONS[@]} healthy"
}

# ============================================================================
# Main
# ============================================================================
main() {
  local mode="full"

  while [[ $# -gt 0 ]]; do
    case $1 in
      --summary | -s)
        mode="summary"
        shift
        ;;
      --full | -f)
        mode="full"
        shift
        ;;
      --plain | -p)
        # plain mode — just ensure no gum usage; ANSI colors still used
        shift
        ;;
      *)
        shift
        ;;
    esac
  done

  if [[ "$mode" == "summary" ]]; then
    print_summary
    return 0
  fi

  # Initialize (checks kubectl, kubeconfig, creates cache dir)
  dashboard_init

  clear
  print_header

  # Fetch all data
  fetch_data

  # Sections
  print_dagster_platform
  print_code_locations
  print_data_explorer
  print_infrastructure
  print_storage
  print_service_urls
  print_quick_commands
}

# Run
main "$@"
