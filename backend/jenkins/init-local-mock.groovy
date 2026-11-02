// ONLY for a NEW disposable local Jenkins; never mount into an existing controller.
// No plugins required. Preserve Jenkins CSRF defaults; expose HTTP on loopback only.
import jenkins.model.Jenkins
import hudson.model.FreeStyleProject
import hudson.model.ParametersDefinitionProperty
import hudson.model.StringParameterDefinition
import hudson.tasks.Shell

Jenkins controller = Jenkins.get()
controller.setNumExecutors(2)
controller.setQuietPeriod(0)
['argus-install', 'argus-cases'].each { name ->
    if (controller.getItem(name) == null) {
        def job = controller.createProject(FreeStyleProject, name)
        job.setDescription('Argus disposable local mock job; NO DEVICE EXECUTION')
        def parameters = ['ARGUS_TOKEN', 'RUN_ID', 'NODE_ID', 'ENV_ID', 'IMAGE_REF', 'DEVICE_IDS', 'CASE_REFS', 'PIPELINE_HASH'].collect {
            new StringParameterDefinition(it, '', 'Immutable Argus run input')
        }
        job.addProperty(new ParametersDefinitionProperty(parameters))
        job.getBuildersList().add(new Shell('''#!/bin/sh
set -eu
for name in ARGUS_TOKEN RUN_ID NODE_ID ENV_ID IMAGE_REF DEVICE_IDS CASE_REFS PIPELINE_HASH; do
  # Validate required values with fixed cases; never evaluate user-provided shell.
  case "$name" in
    ARGUS_TOKEN) test -n "$ARGUS_TOKEN" ;;
    RUN_ID) test -n "$RUN_ID" ;;
    NODE_ID) test -n "$NODE_ID" ;;
    ENV_ID) test -n "$ENV_ID" ;;
    IMAGE_REF) test -n "$IMAGE_REF" ;;
    DEVICE_IDS) test -n "$DEVICE_IDS" ;;
    CASE_REFS) test -n "$CASE_REFS" ;;
    PIPELINE_HASH) test -n "$PIPELINE_HASH" ;;
  esac
done
printf 'MOCK ONLY: run=%s node=%s environment=%s\\n' "$RUN_ID" "$NODE_ID" "$ENV_ID"
sleep 2
case "$NODE_ID" in mock-fail) echo 'Intentional mock failure'; exit 1 ;; esac
echo 'No devices or real test cases were executed.'
'''))
        job.save()
    }
}
controller.save()
