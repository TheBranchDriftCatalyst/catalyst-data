# Root Tiltfile - Catalyst Data Ops Dashboard
# Observes ArgoCD-managed cluster, provides ops UI and port-forwards
#
# Usage:
#   tilt up                    # Start dashboard
#
# This Tiltfile does NOT deploy anything - ArgoCD manages all deployments.
# It only observes existing resources and provides ops tooling.

# Load Tilt extensions
load('ext://uibutton', 'cmd_button', 'location')
load('ext://k8s_attach', 'k8s_attach')

# Configuration
config.define_string('k8s_context', args=False, usage='Kubernetes context to use')
cfg = config.parse()

settings = {
    'k8s_context': cfg.get('k8s_context', 'admin@catalyst-cluster'),
}

# Cluster context
allow_k8s_contexts('admin@catalyst-cluster')

print("""
======================================================================
  Catalyst Data - Ops Dashboard (Observe-Only Mode)
======================================================================
  Context: %s
  ArgoCD: ACTIVE (ArgoCD owns all deployments)

  This dashboard observes your cluster and provides:
  - Log streaming for all workloads
  - Port-forwards to services
  - Ops buttons for common tasks
======================================================================
""" % settings['k8s_context'])

# ============================================
# LABEL CONSTANTS
# ============================================
LABEL_DAGSTER = '1-dagster-platform'
LABEL_CODE_LOCATIONS = '2-code-locations'
LABEL_EXPLORER = '3-data-explorer'
LABEL_INFRA = '4-infrastructure'
LABEL_OPS = '5-ops'

# ============================================
# OPS - Manual tasks and cluster inspection
# ============================================

local_resource(
    'cluster-status',
    '''echo "CATALYST-DATA CLUSTER STATUS" && echo "=============================" && \
       echo "" && echo "Pods:" && kubectl get pods -n catalyst-data -o wide && \
       echo "" && echo "Services:" && kubectl get svc -n catalyst-data && \
       echo "" && echo "PVCs:" && kubectl get pvc -n catalyst-data''',
    auto_init=True,
    trigger_mode=TRIGGER_MODE_MANUAL,
    labels=[LABEL_OPS]
)

local_resource(
    'dagster-runs',
    'kubectl logs -n catalyst-data deployment/dagster-daemon --tail=100',
    auto_init=False,
    trigger_mode=TRIGGER_MODE_MANUAL,
    labels=[LABEL_OPS]
)

local_resource(
    'code-location-health',
    '''echo "Checking gRPC connectivity from webserver pod..." && \
       WEBSERVER_POD=$(kubectl get pod -n catalyst-data -l app=dagster,app.kubernetes.io/component=webserver -o jsonpath='{.items[0].metadata.name}' 2>/dev/null) && \
       if [ -z "$WEBSERVER_POD" ]; then echo "ERROR: No webserver pod found"; exit 1; fi && \
       echo "Pod: $WEBSERVER_POD" && echo "" && \
       for svc in congress-data media-ingest open-leaks knowledge-graph; do \
         if kubectl exec -n catalyst-data "$WEBSERVER_POD" -- sh -c "nc -z -w2 ${svc}.catalyst-data.svc.cluster.local 4000" 2>/dev/null; then \
           echo "✓ ${svc}:4000 - reachable"; \
         else \
           echo "✗ ${svc}:4000 - UNREACHABLE"; \
         fi; \
       done''',
    auto_init=False,
    trigger_mode=TRIGGER_MODE_MANUAL,
    labels=[LABEL_OPS]
)

local_resource(
    'validate-manifests',
    '''echo "Validating kustomizations..." && \
       kubectl apply --dry-run=client -k k8s/ 2>&1 && \
       echo "" && echo "All manifests valid"''',
    auto_init=False,
    trigger_mode=TRIGGER_MODE_MANUAL,
    labels=[LABEL_OPS]
)

# ============================================
# DAGSTER PLATFORM - Webserver, Daemon, Postgres
# ============================================

k8s_attach('dagster-webserver', 'deployment/dagster-webserver', namespace='catalyst-data',
           port_forwards=['3000:3000'], labels=[LABEL_DAGSTER])
k8s_attach('dagster-daemon', 'deployment/dagster-daemon', namespace='catalyst-data',
           labels=[LABEL_DAGSTER])
k8s_attach('dagster-postgres', 'deployment/dagster-postgres', namespace='catalyst-data',
           port_forwards=['5432:5432'], labels=[LABEL_DAGSTER])

cmd_button(
    name='btn-open-dagster',
    resource='dagster-webserver',
    argv=['sh', '-c', 'open http://dagster.talos00 || echo "Open http://dagster.talos00 in your browser"'],
    text='Open Dagster UI',
    icon_name='open_in_browser'
)

# ============================================
# CODE LOCATIONS - Dagster code locations
# ============================================

k8s_attach('congress-data', 'deployment/congress-data', namespace='catalyst-data',
           port_forwards=['4001:4000', '9091:9090'], labels=[LABEL_CODE_LOCATIONS])
k8s_attach('media-ingest', 'deployment/media-ingest', namespace='catalyst-data',
           port_forwards=['4002:4000', '8080:8080'], labels=[LABEL_CODE_LOCATIONS])
k8s_attach('open-leaks', 'deployment/open-leaks', namespace='catalyst-data',
           port_forwards=['4003:4000'], labels=[LABEL_CODE_LOCATIONS])
k8s_attach('knowledge-graph', 'deployment/knowledge-graph', namespace='catalyst-data',
           port_forwards=['4004:4000'], labels=[LABEL_CODE_LOCATIONS])

# ============================================
# DATA EXPLORER - Streamlit app (observe mode)
# ============================================

k8s_attach('data-explorer', 'deployment/data-explorer', namespace='catalyst-data',
           port_forwards=['8501:8501'], labels=[LABEL_EXPLORER])

cmd_button(
    name='btn-open-explorer',
    resource='data-explorer',
    argv=['sh', '-c', 'open http://data.talos00 || echo "Open http://data.talos00 in your browser"'],
    text='Open Data Explorer',
    icon_name='open_in_browser'
)

# Uncomment to switch data-explorer from observe to live-dev mode.
# When activating, also comment out the k8s_attach('data-explorer'...) above.
# include('./packages/data-explorer/Tiltfile')

# ============================================
# INFRASTRUCTURE - Neo4j, Postgres-Knowledge
# ============================================

k8s_attach('neo4j', 'deployment/neo4j', namespace='catalyst-data',
           port_forwards=['7474:7474', '7687:7687'], labels=[LABEL_INFRA])
k8s_attach('postgres-knowledge', 'deployment/postgres-knowledge', namespace='catalyst-data',
           port_forwards=['5433:5432'], labels=[LABEL_INFRA])

cmd_button(
    name='btn-neo4j-info',
    resource='neo4j',
    argv=['sh', '-c', 'echo "Neo4j Browser: http://localhost:7474" && echo "Bolt: bolt://localhost:7687" && echo "Auth: neo4j/neo4j-homelab"'],
    text='Connection Info',
    icon_name='info'
)

cmd_button(
    name='btn-pg-knowledge-info',
    resource='postgres-knowledge',
    argv=['sh', '-c', 'echo "PostgreSQL: localhost:5433" && echo "Database: knowledge_graph" && echo "Auth: kg/kg-homelab" && echo "" && echo "psql -h localhost -p 5433 -U kg -d knowledge_graph"'],
    text='Connection Info',
    icon_name='info'
)

# ============================================
# CONFIGURATION
# ============================================

update_settings(
    max_parallel_updates=3,
    k8s_upsert_timeout_secs=300,
    suppress_unused_image_warnings=None
)

print("""
Ready! UI Groups:
  1-dagster-platform  - Webserver (→3000), Daemon, Postgres (→5432)
  2-code-locations    - congress-data (→4001), media-ingest (→4002),
                        open-leaks (→4003), knowledge-graph (→4004)
  3-data-explorer     - Streamlit app (→8501)
  4-infrastructure    - Neo4j (→7474/7687), Postgres-Knowledge (→5433)
  5-ops               - Cluster status, Dagster runs, health checks

Note: Some resources may show 'pending' if not yet deployed.
      ArgoCD manages all deployments - Tilt only observes.
""")
