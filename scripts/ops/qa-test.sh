#!/bin/bash
# QA Test Runner - Trigger and monitor Dagster asset materializations
# Usage:
#   ./qa-test.sh materialize <location> <asset_key>
#   ./qa-test.sh status <run_id>
#   ./qa-test.sh wait <run_id> [timeout_seconds]
#   ./qa-test.sh logs <run_id>

set -euo pipefail

DAGSTER_URL="${DAGSTER_URL:-http://dagster.talos00}"

materialize() {
    local location="$1"
    local asset_key="$2"

    local response=$(curl -s "$DAGSTER_URL/graphql" -H "Content-Type: application/json" -d "{
        \"query\": \"mutation { launchPipelineExecution(executionParams: { selector: { repositoryLocationName: \\\"$location\\\", repositoryName: \\\"__repository__\\\", pipelineName: \\\"__ASSET_JOB\\\" }, mode: \\\"default\\\", executionMetadata: { tags: [] }, stepKeys: [\\\"$asset_key\\\"] }) { __typename ... on LaunchRunSuccess { run { runId status } } ... on PythonError { message } ... on RunConfigValidationInvalid { errors { message } } } }\"
    }")

    local run_id=$(echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['launchPipelineExecution']['run']['runId'])" 2>/dev/null)

    if [ -n "$run_id" ]; then
        echo "$run_id"
    else
        echo "FAILED: $response" >&2
        return 1
    fi
}

get_status() {
    local run_id="$1"

    local response=$(curl -s "$DAGSTER_URL/graphql" -H "Content-Type: application/json" -d "{
        \"query\": \"{ runOrError(runId: \\\"$run_id\\\") { __typename ... on Run { runId status startTime endTime stepKeysToExecute } } }\"
    }")

    echo "$response" | python3 -c "
import sys, json
d = json.load(sys.stdin)
run = d['data']['runOrError']
status = run['status']
steps = ', '.join(run.get('stepKeysToExecute') or [])
start = run.get('startTime', '')
end = run.get('endTime', '')
duration = ''
if start and end:
    duration = f' ({int(float(end) - float(start))}s)'
print(f'{status}{duration} [{steps}]')
" 2>/dev/null
}

wait_for() {
    local run_id="$1"
    local timeout="${2:-300}"
    local elapsed=0

    while [ $elapsed -lt $timeout ]; do
        local status=$(get_status "$run_id")
        local state=$(echo "$status" | awk '{print $1}')

        case "$state" in
            SUCCESS|FAILURE|CANCELED)
                echo "$status"
                return 0
                ;;
            *)
                sleep 5
                elapsed=$((elapsed + 5))
                ;;
        esac
    done

    echo "TIMEOUT after ${timeout}s: $(get_status "$run_id")"
    return 1
}

get_logs() {
    local run_id="$1"

    curl -s "$DAGSTER_URL/graphql" -H "Content-Type: application/json" -d "{
        \"query\": \"{ logsForRun(runId: \\\"$run_id\\\", afterCursor: null, limit: 100) { ... on EventConnection { events { ... on MessageEvent { message timestamp level stepKey } ... on StepMaterializationEvent { stepKey materialization { label metadataEntries { label ... on TextMetadataEntry { text } ... on IntMetadataEntry { intValue } ... on JsonMetadataEntry { jsonString } } } } } } } }\"
    }" | python3 -c "
import sys, json
d = json.load(sys.stdin)
events = d['data']['logsForRun']['events']
for e in events:
    if 'materialization' in e:
        mat = e['materialization']
        print(f'MATERIALIZATION: {mat[\"label\"]}')
        for entry in mat.get('metadataEntries', []):
            label = entry.get('label', '')
            value = entry.get('text', entry.get('intValue', entry.get('jsonString', '')))
            if value:
                print(f'  {label}: {value}')
    elif 'message' in e:
        level = e.get('level', '')
        step = e.get('stepKey', '')
        msg = e.get('message', '')
        if level in ('ERROR', 'WARNING') or 'ERROR' in msg or 'STEP_' in msg:
            print(f'[{level}] {step}: {msg[:200]}')
" 2>/dev/null
}

case "${1:-help}" in
    materialize) materialize "$2" "$3" ;;
    status) get_status "$2" ;;
    wait) wait_for "$2" "${3:-300}" ;;
    logs) get_logs "$2" ;;
    *) echo "Usage: $0 {materialize|status|wait|logs} <args>" ;;
esac
