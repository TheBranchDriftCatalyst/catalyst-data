#!/bin/bash
# Trigger a Dagster asset materialization via GraphQL API
# Usage: ./materialize.sh <location_name> <asset_key> [run_config_json]
#
# Examples:
#   ./materialize.sh congress_data congress_bills
#   ./materialize.sh congress_data congress_bills '{"ops":{"congress_bills":{"config":{"max_bills":10}}}}'

set -euo pipefail

DAGSTER_URL="${DAGSTER_URL:-http://dagster.talos00}"
LOCATION="$1"
ASSET_KEY="$2"
RUN_CONFIG="${3:-{}}"

# Build the GraphQL mutation for asset materialization
QUERY=$(cat <<'GRAPHQL'
mutation LaunchAssetMaterialization($executionParams: ExecutionParams!) {
  launchPipelineExecution(executionParams: $executionParams) {
    __typename
    ... on LaunchRunSuccess {
      run {
        runId
        status
      }
    }
    ... on PipelineNotFoundError {
      message
    }
    ... on InvalidSubsetError {
      message
    }
    ... on RunConflict {
      message
    }
    ... on ConflictingExecutionParamsError {
      message
    }
    ... on PresetNotFoundError {
      message
    }
    ... on RunConfigValidationInvalid {
      errors {
        message
        reason
      }
    }
    ... on PythonError {
      message
      stack
    }
    ... on UnauthorizedError {
      message
    }
  }
}
GRAPHQL
)

VARIABLES=$(python3 -c "
import json
location = '$LOCATION'
asset_key = '$ASSET_KEY'
run_config = json.loads('$RUN_CONFIG')
variables = {
    'executionParams': {
        'selector': {
            'repositoryLocationName': location,
            'repositoryName': '__repository__',
            'pipelineName': '__ASSET_JOB',
        },
        'runConfigData': json.dumps(run_config),
        'mode': 'default',
        'executionMetadata': {
            'tags': [
                {'key': 'dagster/step_selection', 'value': asset_key},
                {'key': 'dagster/asset_selection', 'value': json.dumps([{'path': [asset_key]}])},
            ]
        },
        'stepKeys': [asset_key],
    }
}
print(json.dumps(variables))
")

PAYLOAD=$(python3 -c "
import json
query = '''$QUERY'''
variables = json.loads('$VARIABLES')
print(json.dumps({'query': query, 'variables': variables}))
")

RESPONSE=$(curl -s "$DAGSTER_URL/graphql" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
